"""Price-based quantitative signals. Each score is in [-100, +100] (positive = bullish)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clip(x: float) -> float:
    if not math.isfinite(x):
        return 0.0
    return float(max(-100.0, min(100.0, x)))


def realized_vol(closes: pd.Series, window: int = 20) -> float:
    r = np.log(closes).diff().dropna().tail(window)
    return float(r.std() * math.sqrt(252)) if len(r) > 2 else float("nan")


def atr(df: pd.DataFrame, window: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.tail(window).mean())


def rsi(closes: pd.Series, window: int = 14) -> float:
    d = closes.diff().dropna()
    up = d.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean().iloc[-1]
    dn = (-d.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean().iloc[-1]
    if dn == 0:
        return 100.0
    return float(100 - 100 / (1 + up / dn))


def trend_score(closes: pd.Series) -> dict:
    """Time-series momentum blend (20/60/120d), vol-normalised - the classic CTA signal."""
    lr = np.log(closes)
    daily_sd = lr.diff().tail(60).std()
    parts = {}
    for lb in (20, 60, 120):
        if len(lr) > lb and daily_sd > 0:
            parts[lb] = float((lr.iloc[-1] - lr.iloc[-1 - lb]) / (daily_sd * math.sqrt(lb)))
        else:
            parts[lb] = 0.0
    blended = 0.3 * parts[20] + 0.4 * parts[60] + 0.3 * parts[120]
    return {"score": _clip(100 * math.tanh(blended / 1.5)), "tstats": parts}


def mean_reversion_score(closes: pd.Series) -> dict:
    """Short-term stretch: z-score vs 20d mean and RSI(14). Stretched up = bearish (fade)."""
    tail = closes.tail(20)
    z = float((closes.iloc[-1] - tail.mean()) / (tail.std() + 1e-12))
    r = rsi(closes)
    score = -0.6 * (100 * math.tanh(z / 2)) - 0.4 * (r - 50) * 2
    return {"score": _clip(score), "z": z, "rsi": r}


def breakout_score(df: pd.DataFrame, window: int = 20) -> dict:
    """Position inside the Donchian channel; at/through the band = strong breakout."""
    hist = df.iloc[:-1].tail(window)
    hi, lo = float(hist["high"].max()), float(hist["low"].min())
    last = float(df["close"].iloc[-1])
    pos = (last - lo) / (hi - lo) if hi > lo else 0.5
    score = (pos - 0.5) * 200
    return {"score": _clip(score), "upper": hi, "lower": lo, "position": pos}


def carry_score(df: pd.DataFrame) -> dict:
    """Front vs 2nd month. Backwardation (front > deferred) = positive roll yield = bullish."""
    if "second" not in df or df["second"].isna().all():
        return {"score": 0.0, "available": False}
    f, s = float(df["close"].iloc[-1]), float(df["second"].iloc[-1])
    ann = (f / s - 1) * 12  # approx monthly roll, annualised
    return {"score": _clip(100 * math.tanh(ann / 0.15)), "annualized_roll": ann, "available": True}


def seasonality_score(closes: pd.Series, horizon: int = 20, min_years: int = 2) -> dict:
    """Average forward return for this calendar window in prior years."""
    s = closes.copy()
    today = s.index[-1]
    rets = []
    for yrs in range(1, 15):
        anchor = today - pd.DateOffset(years=yrs)
        past = s[s.index <= anchor]
        if len(past) < 1:
            break
        i0 = s.index.get_loc(past.index[-1])
        if i0 + horizon >= len(s) - 1:
            continue
        rets.append(math.log(s.iloc[i0 + horizon] / s.iloc[i0]))
    if len(rets) < min_years:
        return {"score": 0.0, "years": len(rets), "avg_fwd_ret": 0.0, "hit_rate": 0.5}
    avg = float(np.mean(rets))
    hit = float(np.mean([r > 0 for r in rets]))
    dvol = np.log(s).diff().std() * math.sqrt(horizon)
    score = 100 * math.tanh(avg / (dvol + 1e-12)) * (0.5 + abs(hit - 0.5))
    return {"score": _clip(score), "years": len(rets), "avg_fwd_ret": avg, "hit_rate": hit}


def vol_regime(closes: pd.Series) -> dict:
    """Current 20d realised vol percentile vs the last year (risk context, not directional)."""
    r = np.log(closes).diff()
    rv = r.rolling(20).std().dropna() * math.sqrt(252)
    if len(rv) < 30:
        return {"vol": float("nan"), "percentile": 50.0, "regime": "NORMAL"}
    last = float(rv.iloc[-1])
    pct = float((rv.tail(252) < last).mean() * 100)
    regime = "HIGH" if pct >= 80 else "LOW" if pct <= 20 else "NORMAL"
    return {"vol": last, "percentile": pct, "regime": regime}


def day_trade_levels(df: pd.DataFrame, last: float, vwap: float, tick: float) -> dict:
    """Classic floor pivots off the prior session, ATR bands and VWAP for intraday execution."""
    prev = df.iloc[-2]
    h, l, c = float(prev.high), float(prev.low), float(prev.close)
    p = (h + l + c) / 3
    rnd = lambda x: round(round(x / tick) * tick, 6)  # noqa: E731
    a = atr(df.iloc[:-1])
    return {
        "pivot": rnd(p),
        "r1": rnd(2 * p - l), "s1": rnd(2 * p - h),
        "r2": rnd(p + (h - l)), "s2": rnd(p - (h - l)),
        "prev_high": h, "prev_low": l, "prev_close": c,
        "atr": a,
        "atr_ticks": int(round(a / tick)),
        "vwap": rnd(vwap),
        "vwap_dev_atr": (last - vwap) / a if a else 0.0,
        "bias": "LONG > VWAP" if last > vwap else "SHORT < VWAP",
    }
