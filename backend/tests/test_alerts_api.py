from fastapi.testclient import TestClient

from app import universe
from app.alerts import AlertEngine, UserRule
from app.main import app


def _snap(idx=95.0, last=10.0, bias="NEUTRAL"):
    return {
        "cot": {"mm_index_3y": idx, "report_date": "2026-11-17", "mm_net": 1000, "mm_net_chg": 10, "mm_chg_z": 0.1},
        "supply_demand": None,
        "levels": {"r1": 11, "s1": 9, "r2": 12, "s2": 8, "prev_high": 10.5, "prev_low": 9.5, "atr": 1, "atr_ticks": 100,
                   "vwap": 10, "bias": ""},
        "quote": {"last": last},
        "swing": {"score": 0, "bias": bias, "agreement": "0/0"}, "quant_score": 0, "fundamental_score": 0,
        "day": {"score": 0, "bias": "NEUTRAL"},
        "detail": {"vol": {"regime": "NORMAL", "percentile": 50}},
    }


def test_cot_extreme_and_level_cross_and_dedup():
    e = AlertEngine()
    first = e.evaluate({"CL": _snap()}, {})
    assert any(a.kind == "COT" for a in first)
    assert not e.evaluate({"CL": _snap()}, {})           # deduped
    crossed = e.evaluate({"CL": _snap(last=11.2)}, {})   # through R1 and prev high
    assert {a.title.split()[3] for a in crossed if a.kind == "LEVEL"} >= {"R1", "PREV_HIGH"}


def test_user_rule_fires_once():
    e = AlertEngine()
    e.add_rule(UserRule("CL", "last", ">", 70))
    assert e.evaluate({}, {"CL": {"last": 71}})
    assert not e.evaluate({}, {"CL": {"last": 72}})


def test_api_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        rows = c.get("/api/watchlist").json()
        assert {r["symbol"] for r in rows} >= {"CL", "MCL", "GC", "ZC"}
        assert c.get("/api/snapshot/MCL").json()["contract"]["parent"] == "CL"
        assert len(c.get("/api/matrix").json()) == len(universe.DATA_SYMBOLS)
        r = c.post("/api/orders", json={"symbol": "MCL", "side": "BUY", "qty": 1, "stop_ticks": 20, "target_ticks": 40})
        assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"
        r = c.post("/api/orders", json={"symbol": "CL", "side": "BUY", "qty": 50, "stop_ticks": 20})
        assert r.status_code == 409
        r = c.post("/api/orders", json={"symbol": "CL", "side": "BUY", "qty": 1, "stop_ticks": 20, "route": "live"})
        assert r.status_code == 403
        assert c.post("/api/prop/rules", json={"preset": "static_100k"}).json()["rules"]["account_size"] == 100000
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
