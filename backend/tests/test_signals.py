import numpy as np
import pandas as pd

from app import universe
from app.config import Settings
from app.data import sim
from app.data.store import MarketData
from app.signals import forecast, fundamental, quant
from app.signals.engine import SignalEngine


def _series(values):
    idx = pd.bdate_range("2022-01-03", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_trend_score_sign_follows_direction():
    up = _series(np.exp(np.linspace(0, 0.5, 300) + np.random.default_rng(1).normal(0, 0.01, 300)))
    down = _series(up.values[::-1])
    assert quant.trend_score(up)["score"] > 30
    assert quant.trend_score(down)["score"] < -30


def test_cot_index_bounds_and_extremes():
    s = pd.Series(np.arange(200, dtype=float))
    assert fundamental.cot_index(s, 156) == 100.0
    assert fundamental.cot_index(s[::-1].reset_index(drop=True), 156) == 0.0


def test_cot_signals_contrarian_at_crowded_long():
    n = 200
    df = pd.DataFrame({
        "date": pd.date_range("2022-01-04", periods=n, freq="W-TUE"),
        "mm_net": np.r_[np.zeros(n - 1), 1e6],          # record long
        "pm_net": np.r_[np.zeros(n - 1), -1e6],         # commercials record short
        "open_interest": np.full(n, 2e6),
    })
    out = fundamental.cot_signals(df)
    assert out["state"] == "CROWDED LONG"
    assert out["score"] < -50


def test_supply_demand_tight_market_is_bullish():
    c = universe.get("CL")
    px = sim.daily_history(c, 1)
    inv = sim.inventory_history(c, px, 1)
    norm = fundamental.supply_demand_signals(inv)["five_yr_avg"]
    inv.loc[inv.index[-1], "stocks"] = 0.8 * norm   # force stocks well below the 5y norm
    out = fundamental.supply_demand_signals(inv)
    assert out["dev_z"] < 0 and out["score"] > 0 and out["balance"] == "TIGHT"
    assert all("norm" in h for h in out["history"])


def test_forecast_cone_brackets_target():
    c = universe.get("GC")
    closes = sim.daily_history(c, 3).set_index("date")["close"]
    f = forecast.forecast(closes)
    h = f["horizons"]["20"]
    assert h["low_2s"] < h["low_1s"] < h["target"] < h["high_1s"] < h["high_2s"]
    assert 0 <= h["p_up"] <= 1
    assert len(f["cone"]) == 21


def test_day_levels_ordered():
    df = sim.daily_history(universe.get("NG"), 5)
    lv = quant.day_trade_levels(df, float(df.close.iloc[-1]), float(df.close.iloc[-1]), 0.001)
    assert lv["s2"] < lv["s1"] < lv["pivot"] < lv["r1"] < lv["r2"]
    assert lv["atr_ticks"] > 0


def test_engine_snapshot_scores_in_range():
    md = MarketData(Settings())
    md.load()
    eng = SignalEngine(md)
    eng.warmup()
    snaps = eng.compute_all()
    assert set(snaps) == set(universe.DATA_SYMBOLS)
    for s in snaps.values():
        assert -100 <= s["swing"]["score"] <= 100
        assert -100 <= s["day"]["score"] <= 100
        for v in s["scores"].values():
            assert v is None or -100 <= v <= 100
    # S&D only where an EIA series exists
    assert snaps["CL"]["supply_demand"] and snaps["GC"]["supply_demand"] is None
