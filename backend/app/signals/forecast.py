"""Ensemble price forecast with volatility cone and walk-forward hit rate.

Deliberately simple and transparent: shrunk momentum drift + AR(1) on returns +
seasonal drift, with EWMA volatility for the range. The walk-forward hit rate is
shown next to every forecast so users can see how much (or little) edge it has.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ewma_vol(returns: np.ndarray, lam: float = 0.94) -> float:
    var = float(np.var(returns[:30])) if len(returns) >= 30 else float(np.var(returns))
    for r in returns:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


def _drift_components(r: np.ndarray, horizon: int) -> tuple[float, float]:
    mom = float(r[-60:].mean()) * 0.25 * horizon
    x, y = r[-500:-1], r[-499:]
    beta = float(np.dot(x - x.mean(), y - y.mean()) / (np.dot(x - x.mean(), x - x.mean()) + 1e-12))
    beta = max(-0.3, min(0.3, beta))
    # AR(1) cumulative effect over the horizon
    ar = float(r[-1]) * sum(beta**k for k in range(1, horizon + 1))
    return mom, ar


def walk_forward_hit_rate(closes: pd.Series, horizon: int = 5, window: int = 250) -> float:
    lr = np.log(closes.values)
    r = np.diff(lr)
    hits, n = 0, 0
    start = max(520, len(r) - window - horizon)
    for t in range(start, len(r) - horizon):
        mom, ar = _drift_components(r[: t + 1], horizon)
        pred = mom + ar
        real = lr[t + 1 + horizon] - lr[t + 1]
        if pred != 0:
            hits += int(np.sign(pred) == np.sign(real))
            n += 1
    return hits / n if n else float("nan")


def forecast(closes: pd.Series, seasonal_avg_fwd_20d: float = 0.0, horizons=(5, 20)) -> dict:
    lr = np.log(closes.values)
    r = np.diff(lr)
    last = float(closes.iloc[-1])
    sd = ewma_vol(r[-250:])
    out: dict = {"last": last, "daily_vol": sd, "horizons": {}}
    for h in horizons:
        mom, ar = _drift_components(r, h)
        seas = seasonal_avg_fwd_20d * (h / 20) * 0.5
        mu = mom + ar + seas
        sig = sd * math.sqrt(h)
        out["horizons"][str(h)] = {
            "exp_return": mu,
            "target": last * math.exp(mu),
            "low_1s": last * math.exp(mu - sig),
            "high_1s": last * math.exp(mu + sig),
            "low_2s": last * math.exp(mu - 2 * sig),
            "high_2s": last * math.exp(mu + 2 * sig),
            "p_up": _phi(mu / sig) if sig > 0 else 0.5,
            "components": {"momentum": mom, "ar1": ar, "seasonal": seas},
        }
    h = max(horizons)
    mu_d = out["horizons"][str(h)]["exp_return"] / h
    out["cone"] = [
        {
            "day": k,
            "mid": last * math.exp(mu_d * k),
            "hi1": last * math.exp(mu_d * k + sd * math.sqrt(k)),
            "lo1": last * math.exp(mu_d * k - sd * math.sqrt(k)),
            "hi2": last * math.exp(mu_d * k + 2 * sd * math.sqrt(k)),
            "lo2": last * math.exp(mu_d * k - 2 * sd * math.sqrt(k)),
        }
        for k in range(0, h + 1)
    ]
    return out
