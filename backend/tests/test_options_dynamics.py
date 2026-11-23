from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.store import MarketData
from app.main import app
from app.natgas import dynamics
from app.options import analytics as oa
from app.options import chains


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def test_black76_greeks():
    d_c, g_c = oa.greeks(100, 100, 0.25, 0.3, np.array([True]))
    d_p, g_p = oa.greeks(100, 100, 0.25, 0.3, np.array([False]))
    assert g_c[0] == pytest.approx(g_p[0]) and 0.45 < d_c[0] < 0.6 and -0.55 < d_p[0] < -0.4
    assert d_c[0] - d_p[0] == pytest.approx(np.exp(-oa.R * 0.25))       # put-call parity on deltas


def _chain(today):
    ex = today + timedelta(days=10)
    rows = [{"expiry": ex, "strike": k, "type": t, "oi": oi, "volume": oi // 10, "iv": 0.4}
            for k, t, oi in [(3.0, "P", 5000), (3.2, "P", 2000), (3.4, "C", 1000), (3.4, "P", 1000), (3.6, "C", 2000), (3.8, "C", 6000)]]
    return pd.DataFrame(rows), {"symbol": "NG", "price": 3.4, "multiplier": 10000, "source": "TEST"}


def test_gex_modes_walls_and_max_pain():
    today = date(2026, 7, 1)
    ch, meta = _chain(today)
    std = oa.analyse(ch, meta, "standard", today)
    short = oa.analyse(ch, meta, "short_calls", today)
    assert short["total_gex"] < std["total_gex"] and short["total_gex"] < 0
    assert std["call_wall"] == 3.8 and std["put_wall"] == 3.0
    assert std["front"]["max_pain"] in (3.2, 3.4, 3.6)
    assert std["pcr_oi"] == pytest.approx(8000 / 9000, abs=0.01)
    assert oa.default_mode("NG", date(2026, 12, 1)) == "short_calls" and oa.default_mode("ES", date(2026, 12, 1)) == "standard"


def test_sim_chain_skews(md):
    ng, mng = chains.load(md, "NG")
    es, mes = chains.load(md, "ES")
    a_ng, a_es = oa.analyse(ng, mng, today=date.today()), oa.analyse(es, mes, today=date.today())
    if date.today().month in (11, 12, 1, 2, 3):
        assert a_ng["front"]["rr25"] > 0                    # winter call skew in natural gas
    assert a_es["front"]["rr25"] < 0                        # equity put skew
    assert a_es["flip"] is None or 6000 < a_es["flip"] < 7600


def test_csv_chain(tmp_path, monkeypatch, md):
    ex = (date.today() + timedelta(days=20)).isoformat()
    pd.DataFrame({"expiry": [ex] * 4, "strike": [3.0, 3.5, 3.0, 3.5], "type": ["C", "C", "P", "P"],
                  "open_interest": [100, 900, 800, 50], "volume": [1, 2, 3, 4], "iv": [45, 40, 50, 42],
                  "underlying": [3.3] * 4}).to_csv(tmp_path / "NG_chain.csv", index=False)
    monkeypatch.setattr(chains, "DATA_DIR", tmp_path)
    ch, meta = chains.load(md, "NG")
    assert meta["source"] == "CSV" and meta["price"] == 3.3 and ch["iv"].max() < 1 and len(ch) == 4


def test_dynamics_and_policy(md):
    d = dynamics.dynamics(md, Settings())
    assert len(d["tails"]) == 12 and d["sensitivity"]["winter"]["n"] > 100
    assert d["policy"]["active"] == (date.today().month in (11, 12, 1, 2, 3))
    w = d["winter_summary"]
    assert w["up95_pct"] > 0 > w["down05_pct"]


def test_winter_policy_in_signals(monkeypatch):
    with TestClient(app) as c:
        from app.main import hub
        from app.natgas import dynamics as dyn
        monkeypatch.setattr(dyn, "policy_active", lambda today=None: True)
        dyn._QS.clear()
        ng = {"policy": True, "bias": "NEUTRAL", "rev_hdd": 0, "next14_pct": 0}
        snap = hub.engine.snapshots["NG"]
        reg = {"direction": "DOWN", "kind": "trend"}
        last = hub.md.sessions["NG"].last
        base = {"setup": "x", "name": "t", "style": "Trend", "timeframe": "15m", "grade": "C", "entry_type": "market",
                "tp1": last - 0.1, "tp2": last - 0.2, "reasons": [], "group": None, "expires_in": 600, "entry": last, "stop": last + 0.05}
        assert hub.tsig._create(hub, "NG", {**base, "side": -1, "horizon": "Swing (days)"}, snap, None, reg, ng) is None
        s = hub.tsig._create(hub, "NG", {**base, "side": -1, "horizon": "Intraday"}, snap, None, reg, ng)
        full = hub.prop.risk_size("MNG", s["risk_ticks"], 20, hub.prices())["contracts"]
        assert s["size"]["micro"]["contracts"] == full // 2 and any("Winter policy" in r for r in s["reasons"])
        assert c.get("/api/options/NG").status_code == 200 and c.get("/api/options/ZC").status_code == 404
        assert c.get("/api/options/ES?mode=bad").status_code == 400
        assert "policy" in c.get("/api/natgas/dynamics").json()
