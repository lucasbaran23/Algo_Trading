import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.backtest import engine as E
from app.backtest.strategies import STRATEGIES, Context
from app.config import Settings
from app.data.store import MarketData
from app.main import app
from app.prop.rules import PRESETS, PropRules


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def _ctx(closes, spread=0.5):
    c = np.asarray(closes, float)
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=len(c)),
                       "open": c, "high": c + spread, "low": c - spread, "close": c})
    return Context(df)


def test_executes_next_open_no_lookahead():
    ctx = _ctx([100, 100, 110, 120, 120])
    sig = np.array([1, 1, 1, 1, 1])
    cfg = E.RunConfig(symbol="CL", commission=0, slippage_ticks=0)
    sim = E.simulate(ctx, sig, cfg)
    # signal at close of bar 0 -> filled at open of bar 1 (100); exit at final close 120 => +20 * $1000
    assert sim["trades"][0]["entry"] == 100
    assert sim["pnl"].sum() == pytest.approx(20_000)
    assert sim["pnl"][0] == 0                     # nothing earned on the signal bar itself


def test_costs_charged_per_side():
    ctx = _ctx([100] * 6)
    sig = np.array([1, 0, 1, 0, 0, 0])
    cfg = E.RunConfig(symbol="CL", commission=2.5, slippage_ticks=1)   # $2.50 + $10 per side
    sim = E.simulate(ctx, sig, cfg)
    assert len(sim["trades"]) == 2
    assert sim["pnl"].sum() == pytest.approx(-2 * 2 * 12.5)


def test_stop_loss_and_gap_fill():
    closes = [100, 100, 100, 90, 90]
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=5), "open": [100, 100, 100, 92, 90],
                       "high": [101] * 5, "low": [99, 99, 99, 91, 89], "close": closes})
    sim = E.simulate(Context(df), np.ones(5), E.RunConfig(symbol="CL", commission=0, slippage_ticks=0, stop_atr=1))
    t = sim["trades"][0]
    assert t["reason"] == "stop" and t["exit"] == 92            # gapped through the stop -> filled at open
    assert len(sim["trades"]) == 1                               # no instant re-entry after a stop


def test_all_strategies_run_everywhere(md):
    for key in STRATEGIES:
        for sym in ("CL", "GC", "ZC"):
            r = E.run(md, E.RunConfig(symbol=sym, strategy=key))
            assert r["metrics"]["days"] > 1000
            assert len(r["equity"]) == len(r["drawdown"]) == len(r["benchmark"])


def test_mode_long_only(md):
    r = E.run(md, E.RunConfig(symbol="GC", strategy="sma_cross", params={"mode": "long_only"}))
    assert r["trades"] and all(t["side"] == "LONG" for t in r["trades"])


def test_risk_sizing_caps(md):
    r = E.run(md, E.RunConfig(symbol="MCL", strategy="tsmom", sizing="risk", risk_pct=5, max_contracts=3))
    assert r["trades"] and max(t["qty"] for t in r["trades"]) <= 3


def test_optimize_ranks_and_excludes_invalid(md):
    o = E.optimize(md, E.RunConfig(symbol="GC", strategy="sma_cross"),
                   {"fast": [10, 100, 30], "slow": [20, 80, 30]}, "sharpe", 30)
    assert o["invalid_combos"] > 0                     # fast >= slow never trades
    scores = [r["is_score"] for r in o["results"]]
    assert scores == sorted(scores, reverse=True)
    assert all(r["params"]["fast"] < r["params"]["slow"] for r in o["results"])
    assert o["heatmap"]["x"] == "fast"


def test_grid_limit(md):
    with pytest.raises(ValueError):
        E.optimize(md, E.RunConfig(symbol="GC"), {"fast": [5, 100, 1], "slow": [20, 300, 1]})


def test_walk_forward_is_out_of_sample(md):
    w = E.walk_forward(md, E.RunConfig(symbol="GC", strategy="donchian"),
                       {"entry": [20, 60, 20], "exit": [10, 20, 10]}, train_days=504, test_days=252)
    assert len(w["folds"]) >= 5
    for f in w["folds"]:
        assert f["train"][1] < f["test"][0]            # test window strictly after training window


def test_monte_carlo_and_prop_eval(md):
    r = E.run(md, E.RunConfig(symbol="GC", strategy="donchian"), PRESETS["eval_50k_eod"])
    pe = r["prop_eval"]
    assert pe["attempts"] > 100
    assert pe["pass_pct"] + pe["fail_drawdown_pct"] + pe["fail_daily_pct"] + pe["timeout_pct"] == pytest.approx(100, abs=0.5)
    mc = E.monte_carlo(r["trades"], 50_000, 500, PRESETS["eval_50k_eod"])
    assert mc["max_dd"]["5"] <= mc["max_dd"]["50"] <= mc["max_dd"]["95"]
    assert 0 <= mc["prob_breach_max_dd_pct"] <= 100


def test_prop_eval_daily_loss():
    pnl = np.r_[np.zeros(3), -1500, np.zeros(100)]
    pe = E.prop_eval(pnl, PropRules(), every=200)
    assert pe["fail_daily_pct"] == 100


def test_backtest_api():
    with TestClient(app) as c:
        assert len(c.get("/api/backtest/strategies").json()) == len(STRATEGIES)
        r = c.post("/api/backtest/run", json={"symbol": "GC", "strategy": "tsmom"})
        assert r.status_code == 200 and r.json()["metrics"]["days"] > 0
        r = c.post("/api/backtest/optimize", json={"symbol": "GC", "strategy": "tsmom", "ranges": {"lookback": [40, 200, 40]}})
        assert r.status_code == 200 and r.json()["results"]
        assert c.post("/api/backtest/optimize", json={"symbol": "GC"}).status_code == 400
        assert c.post("/api/backtest/run", json={"symbol": "GC", "strategy": "nope"}).status_code == 400
        assert c.post("/api/backtest/montecarlo", json={"symbol": "GC", "strategy": "donchian"}).status_code == 200
        r = c.post("/api/backtest/walkforward", json={"symbol": "GC", "strategy": "sma_cross",
                                                       "ranges": {"fast": [10, 30, 10], "slow": [50, 150, 50]}})
        assert r.status_code == 200 and r.json()["folds"]
