"""Deterministic market simulator.

Generates realistic-looking (but entirely synthetic) daily OHLC, futures curve,
CFTC COT positioning and weekly inventory data, so the terminal runs offline
and every signal/alert can be demonstrated. Everything here is labelled SIM in
the UI and must never be presented to customers as real market data.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..universe import Contract

HISTORY_DAYS = 2600  # ~10 years of business days (enough for backtests)


def _rng(seed: int, *keys: str) -> np.random.Generator:
    digest = hashlib.sha256(("|".join(keys) + f"|{seed}").encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def _end_date(today: date | None = None) -> pd.Timestamp:
    today = today or date.today()
    ts = pd.Timestamp(today) - pd.offsets.BDay(0)
    if ts.weekday() >= 5:
        ts = ts - pd.offsets.BDay(1)
    return ts.normalize()


def seasonal_factor(month_float: np.ndarray | float, peak_month: int) -> np.ndarray | float:
    """Cosine seasonal curve in [-1, 1] peaking at `peak_month`."""
    return np.cos(2 * np.pi * (np.asarray(month_float) - peak_month) / 12.0)


def daily_history(c: Contract, seed: int, today: date | None = None) -> pd.DataFrame:
    rng = _rng(seed, "px", c.data_symbol)
    dates = pd.bdate_range(end=_end_date(today), periods=HISTORY_DAYS)
    n = len(dates)
    daily_vol = c.sim_vol / np.sqrt(252)

    # GARCH(1,1)-style volatility clustering
    omega, alpha, beta = daily_vol**2 * 0.05, 0.08, 0.87
    var = np.empty(n)
    shocks = np.empty(n)
    var[0] = daily_vol**2
    z = rng.standard_t(df=5, size=n) / np.sqrt(5 / 3)
    for i in range(n):
        if i:
            var[i] = omega + alpha * shocks[i - 1] ** 2 + beta * var[i - 1]
        shocks[i] = np.sqrt(var[i]) * z[i]

    # Regime-switching drift (trend phases), mean reversion to a slow anchor, seasonality
    drift = np.zeros(n)
    regime = 0.0
    for i in range(1, n):
        if rng.random() < 1 / 60:
            regime = rng.normal(0, daily_vol * 0.18)
        drift[i] = regime
    month_f = dates.month.values + dates.day.values / 31.0
    season = 0.06 * seasonal_factor(month_f, c.season_peak_month)
    log_p = np.empty(n)
    log_p[0] = np.log(c.sim_price)
    anchor = log_p[0]
    base = log_p[0]
    for i in range(1, n):
        # the "fair value" anchor trends in regimes but is itself pulled back towards a
        # long-run level (cost of production), so 10-year ranges stay realistic (~2-4x)
        anchor += drift[i] * 0.5 - 0.004 * (anchor - base)
        mr = -0.015 * (log_p[i - 1] - anchor - season[i - 1])
        log_p[i] = log_p[i - 1] + drift[i] + mr + shocks[i] + (season[i] - season[i - 1])
    # Rescale so the latest close sits near the configured level
    log_p += np.log(c.sim_price) - log_p[-1]
    close = np.exp(log_p)

    gap = rng.normal(0, daily_vol * 0.25, n)
    open_ = np.r_[close[0], close[:-1]] * np.exp(gap)
    rng_hi = np.abs(rng.normal(0, np.sqrt(var) * 0.4, n))
    rng_lo = np.abs(rng.normal(0, np.sqrt(var) * 0.4, n))
    high = np.maximum(open_, close) * np.exp(rng_hi)
    low = np.minimum(open_, close) * np.exp(-rng_lo)
    volume = (rng.lognormal(11, 0.35, n) * (1 + 8 * np.abs(shocks) / daily_vol / 10)).astype(int)

    # Second-month contract: contango/backwardation driven by inventory-like state + season
    carry_state = np.zeros(n)
    for i in range(1, n):
        carry_state[i] = 0.97 * carry_state[i - 1] + rng.normal(0, 0.002)
    spread_pct = 0.004 + carry_state - 0.003 * seasonal_factor(month_f, c.season_peak_month)
    second = close * (1 + spread_pct)

    tick = c.tick_size
    rnd = lambda a: np.round(np.round(a / tick) * tick, 6)  # noqa: E731
    return pd.DataFrame(
        {
            "date": dates,
            "open": rnd(open_),
            "high": rnd(high),
            "low": rnd(low),
            "close": rnd(close),
            "volume": volume,
            "second": rnd(second),
        }
    )


def cot_history(c: Contract, prices: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Weekly (Tuesday) positioning. Managed money chases 12-week momentum; commercials take the other side."""
    rng = _rng(seed, "cot", c.data_symbol)
    px = prices.set_index("date")["close"]
    tuesdays = pd.date_range(px.index[0] + timedelta(days=90), px.index[-1], freq="W-TUE")
    px_w = px.reindex(tuesdays, method="ffill")
    mom = np.log(px_w).diff(12).fillna(0).values
    mom_z = mom / (np.std(mom) + 1e-9)
    base_oi = 400_000 * (1 + rng.random())
    n = len(tuesdays)
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.85 * noise[i - 1] + rng.normal(0, 0.25)
    mm_net_frac = 0.12 * np.tanh(0.8 * mom_z + noise) + 0.02
    oi = base_oi * (1 + 0.15 * np.sin(np.arange(n) / 9) + rng.normal(0, 0.02, n))
    mm_net = mm_net_frac * oi
    mm_long = 0.18 * oi + np.maximum(mm_net, 0) + rng.normal(0, 0.005, n) * oi
    mm_short = mm_long - mm_net
    pm_net = -0.8 * mm_net + rng.normal(0, 0.01, n) * oi - 0.05 * oi
    pm_long = 0.25 * oi + np.maximum(pm_net, 0)
    pm_short = pm_long - pm_net
    sw_net = -(mm_net + pm_net) * 0.6
    df = pd.DataFrame(
        {
            "date": tuesdays,
            "open_interest": oi.astype(int),
            "mm_long": mm_long.astype(int),
            "mm_short": mm_short.astype(int),
            "pm_long": pm_long.astype(int),
            "pm_short": pm_short.astype(int),
            "swap_net": sw_net.astype(int),
        }
    )
    df["mm_net"] = df["mm_long"] - df["mm_short"]
    df["pm_net"] = df["pm_long"] - df["pm_short"]
    return df


def inventory_history(c: Contract, prices: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Weekly stocks with a seasonal cycle. Deviations from normal are inversely linked to price."""
    rng = _rng(seed, "inv", c.data_symbol)
    px = prices.set_index("date")["close"]
    weeks = pd.date_range(px.index[0], px.index[-1], freq="W-FRI")
    n = len(weeks)
    base = {"CL": 430.0, "NG": 3000.0, "RB": 225.0, "HO": 120.0}.get(c.data_symbol, 1000.0)
    amp = {"NG": 0.35, "RB": 0.06, "HO": 0.12}.get(c.data_symbol, 0.05)
    woy = weeks.isocalendar().week.values.astype(float)
    # Stocks build in the off-season: trough near the demand peak month
    seasonal = base * (1 - amp * np.cos(2 * np.pi * (woy / 52.0 - (c.season_peak_month + 0.5) / 12.0)))
    dev = np.zeros(n)
    px_w = np.log(px.reindex(weeks, method="ffill").bfill().values)
    rets = np.r_[0, np.diff(px_w)]
    for i in range(1, n):
        dev[i] = 0.93 * dev[i - 1] - 0.6 * rets[i] + rng.normal(0, 0.008)
    level = seasonal * (1 + dev)
    return pd.DataFrame({"date": weeks, "stocks": np.round(level, 1), "week": woy.astype(int)})


class TickSimulator:
    """Random-walk intraday ticks starting from the last daily close."""

    def __init__(self, contracts: list[Contract], last_closes: dict[str, float], seed: int):
        self.rng = _rng(seed, "ticks", str(date.today()))
        self.contracts = {c.symbol: c for c in contracts}
        self.prices = dict(last_closes)

    def step(self, dt_s: float) -> dict[str, float]:
        out = {}
        for sym, c in self.contracts.items():
            # Intraday "demo speed": ~1 trading day of variance every ~40 minutes
            sigma = c.sim_vol / np.sqrt(252) * np.sqrt(dt_s / 2400)
            p = self.prices[sym] * float(np.exp(self.rng.standard_t(4) / np.sqrt(2) * sigma))
            p = round(round(p / c.tick_size) * c.tick_size, 6)
            self.prices[sym] = p
            out[sym] = p
        return out
