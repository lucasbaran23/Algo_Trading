import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.store import MarketData
from app.main import app
from app.options import gammascalp as gs


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def test_straddle_greeks():
    v, d, g, vega = gs._b76(100.0, 100.0, 0.1, 0.3)
    assert v == pytest.approx(0.8 * 100 * 0.3 * np.sqrt(0.1), rel=0.05)   # ATM straddle ≈ 0.8·F·σ·√T
    assert abs(d) < 0.1 and g > 0 and vega > 0


def test_long_gamma_pnl_tracks_realised_minus_implied(md):
    r = gs.scalp(md, "ES", dte=1, hedge="15m", iv_mode="rv_mult", iv_value=1.0)
    st = r["stats"]
    assert st["n"] > 100
    assert st["corr_pnl_rv_minus_iv"] > 0.6
    assert len(r["equity"]) == st["n"] and len(r["scatter"]) == st["n"]


def test_short_is_mirror_of_long_minus_costs(md):
    kw = dict(dte=5, hedge="60m", iv_mode="rv_mult", iv_value=1.1, slip_ticks=0.0, opt_spread_vol=0.0, commission=0.0)
    lo = gs.scalp(md, "NG", side=1, **kw)["stats"]
    sh = gs.scalp(md, "NG", side=-1, **kw)["stats"]
    assert lo["total"] == pytest.approx(-sh["total"], abs=5 * lo["n"])
    costly = gs.scalp(md, "NG", side=-1, **{**kw, "slip_ticks": 1.0, "commission": 2.5, "opt_spread_vol": 0.01})["stats"]
    assert costly["total"] < sh["total"]


def test_richer_options_hurt_the_long(md):
    s = gs.sweep(md, "NG", dte=5)
    avgs = [r["avg"] for r in s["by_iv"]]
    assert avgs == sorted(avgs, reverse=True)
    assert s["best_hedge"] in ("5m", "15m", "60m", "close", "band") and len(s["by_hedge"]) == 5


def test_api_validation():
    with TestClient(app) as c:
        assert c.get("/api/backtest/gammascalp?symbol=NG&dte=7").status_code == 400
        assert c.get("/api/backtest/gammascalp?symbol=ZC").status_code == 404
        assert c.get("/api/backtest/gammascalp?symbol=NG&iv_mode=bogus").status_code == 400
        r = c.get("/api/backtest/gamma-regime?symbol=ES")
        assert r.status_code == 200 and r.json()["rows"]
