import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.store import MarketData
from app.intraday import voldiv as vd
from app.main import app


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def test_rsi_bounds_and_direction():
    up = vd.rsi(np.linspace(100, 120, 60))
    dn = vd.rsi(np.linspace(120, 100, 60))
    assert np.nanmin(up) > 90 and np.nanmax(dn) < 10


def _bars(lows, rsis):
    lows = np.array(lows, float)
    return {"l": lows, "h": lows + 1, "c": lows + 0.5, "o": lows + 0.5, "m": np.arange(len(lows)) * 5.0}, np.array(rsis, float)


def test_bullish_divergence_detected_and_rejected():
    # swing low at bar 10 (RSI 25), lower low at bar 20 with a higher RSI (38): bullish divergence
    lows = [100 - i * 0.5 for i in range(11)] + [96, 97, 98, 97, 96, 95.5, 95, 94.8, 94.6, 94.0]
    rs = [50 - i * 2.5 for i in range(11)] + [35, 40, 45, 42, 40, 39, 38, 38, 37, 38]
    b, r = _bars(lows, rs)
    assert vd.divergence(b, r, 20, 1) == 10
    r2 = r.copy(); r2[20] = 20                                     # RSI also makes a lower low: no divergence
    assert vd.divergence(b, r2, 20, 1) is None


def test_near_level():
    hit = vd.near_level(100.1, {"prev_low": 100.0, "put_wall": 99.0, "flip": None}, 0.25)
    assert hit[0] == "prev_low"
    assert vd.near_level(100.1, {"prev_low": 98.0}, 0.25) is None


def test_backtest_ablation_and_api(md):
    b = vd.backtest(md, "NG")
    assert len(b["grid"]) == 32 and len(b["ablation"]) == 5
    # adding filters can only remove trades
    a = {x["label"]: x["all"].get("n", 0) for x in b["ablation"]}
    assert a["All three filters"] <= a["Without RSI divergence"] <= a["Reversal bar only (no filters)"]
    with TestClient(app) as c:
        r = c.get("/api/strategy/voldiv/NG")
        assert r.status_code == 200
        j = r.json()
        assert j["today"]["levels"] and (j["example"] is None or j["example"]["chart"])
        assert c.get("/api/strategy/voldiv/NG?tf=7").status_code == 400
