import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tradesignals import notify
from app.tradesignals.manager import SignalManager


def _sig(mgr, sid, side=1, entry_type="stop", entry=100.0, stop=99.0, tp1=101.0, tp2=102.0, group=None, expires_in=3600):
    now = time.time()
    s = {"id": sid, "symbol": "CL", "name_full": "WTI", "setup": "t", "name": "Test setup", "style": "Breakout",
         "timeframe": "15m", "horizon": "Intraday", "grade": "C", "side": side, "entry_type": entry_type,
         "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "reasons": [], "group": group,
         "direction": "LONG" if side > 0 else "SHORT", "status": "ACTIVE" if entry_type == "market" else "PENDING",
         "outcome": None, "created": now, "expires": now + expires_in, "filled": None,
         "fill": entry if entry_type == "market" else None, "closed": None, "exit": None, "stop_initial": stop,
         "tp1_hit": False, "r": None, "risk_ticks": 100, "risk_usd": 1000, "rr1": 1, "rr2": 2, "confidence": 2,
         "confidence_why": [], "size": {"contracts": 1, "risk_per_contract": 1000, "micro": None}, "price_at_signal": 99.5}
    mgr.signals[sid] = s
    return s


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "push", lambda *a, **k: False)
    return SignalManager(tmp_path)


def run(mgr, *prices):
    evs = []
    for p in prices:
        evs += mgr.on_prices({"CL": p})
    return [e["event"] for e in evs]


def test_stop_entry_tp1_breakeven_then_target(mgr):
    s = _sig(mgr, "a")
    assert run(mgr, 99.8, 100.0) == ["activated"]
    assert run(mgr, 101.0) == ["tp1"] and s["stop"] == 100.0           # stop moved to breakeven
    assert run(mgr, 102.0) == ["closed"]
    assert s["outcome"] == "TARGET" and s["r"] == pytest.approx(0.5 * 1 + 0.5 * 2)


def test_tp1_then_breakeven_exit(mgr):
    s = _sig(mgr, "b")
    run(mgr, 100.0, 101.0, 100.0)
    assert s["outcome"] == "BREAKEVEN" and s["r"] == pytest.approx(0.5)


def test_short_stopped(mgr):
    s = _sig(mgr, "c", side=-1, entry=100.0, stop=101.0, tp1=99.0, tp2=98.0)
    run(mgr, 100.0, 101.0)
    assert s["outcome"] == "STOPPED" and s["r"] == pytest.approx(-1.0)


def test_stop_entry_invalidated_when_stop_hit_first(mgr):
    s = _sig(mgr, "d")
    assert run(mgr, 98.9) == ["closed"]
    assert s["status"] == "CANCELLED" and s["outcome"] == "INVALIDATED" and s["r"] is None


def test_limit_gap_through_stop_is_an_honest_loss(mgr):
    s = _sig(mgr, "e", entry_type="limit", entry=100.0, stop=99.0, tp1=101.0, tp2=102.0)
    assert run(mgr, 98.5) == ["activated", "closed"]
    assert s["outcome"] == "STOPPED" and s["r"] == pytest.approx(-1.0)


def test_oco_cancels_sibling(mgr):
    a = _sig(mgr, "f", group="g1")
    b = _sig(mgr, "h", side=-1, entry=98.0, stop=99.0, tp1=97.0, tp2=96.0, group="g1")
    run(mgr, 100.0)
    assert a["status"] == "ACTIVE" and b["status"] == "CANCELLED" and b["outcome"] == "OCO"


def test_expiry_and_time_exit(mgr):
    p = _sig(mgr, "i", expires_in=-1)
    m = _sig(mgr, "j", entry_type="market", expires_in=-1)
    run(mgr, 99.5)
    assert p["status"] == "EXPIRED" and m["outcome"] == "TIME" and m["r"] == pytest.approx(-0.5)


def test_persistence_and_stats(mgr, tmp_path):
    _sig(mgr, "k")
    run(mgr, 100.0, 102.0)
    again = SignalManager(tmp_path)
    assert "k" in again.signals and again.signals["k"]["outcome"] == "TARGET"
    st = again.stats()
    assert st["overall"]["n"] == 1 and st["overall"]["avg_r"] == pytest.approx(2.0)


def test_live_generation_and_api():
    with TestClient(app) as c:
        from app.main import hub
        evs = hub.tsig.generate(hub)
        for e in evs:
            s = e["signal"]
            risk = abs(s["entry"] - s["stop"])
            assert risk > 0 and (s["tp1"] - s["entry"]) * s["side"] > 0 and (s["tp2"] - s["tp1"]) * s["side"] >= 0
            assert 1 <= s["confidence"] <= 3 and s["grade"] in "ABC" and s["reasons"]
        assert isinstance(c.get("/api/tsignals?status=open").json(), list)
        assert "overall" in c.get("/api/tsignals/stats").json()
        assert c.post("/api/tsignals/nope/dismiss").status_code == 404
        open_ = c.get("/api/tsignals?status=open").json()
        if open_:
            sid = open_[0]["id"]
            assert c.post(f"/api/tsignals/{sid}/dismiss").json()["outcome"] == "DISMISSED"
