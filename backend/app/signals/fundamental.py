"""Quantamental signals: CFTC positioning and physical supply/demand balance."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clip(x: float) -> float:
    return float(max(-100.0, min(100.0, x))) if math.isfinite(x) else 0.0


def cot_index(series: pd.Series, lookback: int) -> float:
    """Williams COT index: where the latest net position sits in its lookback range (0-100)."""
    tail = series.tail(lookback)
    lo, hi = tail.min(), tail.max()
    return float(100 * (tail.iloc[-1] - lo) / (hi - lo)) if hi > lo else 50.0


def cot_signals(cot: pd.DataFrame, extreme_hi: float = 90, extreme_lo: float = 10) -> dict:
    """
    Managed money is trend-following: extremes in their positioning are *contrarian*
    (crowded trade). Producers/merchants are the "smart" hedgers: their index is read
    *with* the signal. Weekly change z-score flags unusually large repositioning.
    """
    mm, pm, oi = cot["mm_net"], cot["pm_net"], cot["open_interest"]
    mm_idx_3y = cot_index(mm, 156)
    mm_idx_1y = cot_index(mm, 52)
    pm_idx_3y = cot_index(pm, 156)
    chg = mm.diff().dropna()
    chg_z = float((chg.iloc[-1] - chg.tail(52).mean()) / (chg.tail(52).std() + 1e-9)) if len(chg) > 10 else 0.0
    pct_oi = float(mm.iloc[-1] / oi.iloc[-1] * 100) if oi.iloc[-1] else 0.0

    # Contrarian at MM extremes, confirmation from commercials
    contrarian = 0.0
    if mm_idx_3y >= extreme_hi:
        contrarian = -(mm_idx_3y - extreme_hi) / (100 - extreme_hi) * 100 - 40
    elif mm_idx_3y <= extreme_lo:
        contrarian = (extreme_lo - mm_idx_3y) / extreme_lo * 100 + 40
    commercial = (pm_idx_3y - 50) * 2
    score = 0.6 * contrarian + 0.4 * commercial

    if mm_idx_3y >= extreme_hi:
        state = "CROWDED LONG"
    elif mm_idx_3y <= extreme_lo:
        state = "CROWDED SHORT"
    elif mm_idx_3y >= 70:
        state = "SPEC LONG"
    elif mm_idx_3y <= 30:
        state = "SPEC SHORT"
    else:
        state = "NEUTRAL"

    return {
        "score": _clip(score),
        "report_date": str(cot["date"].iloc[-1].date()),
        "mm_net": int(mm.iloc[-1]),
        "mm_net_chg": int(chg.iloc[-1]) if len(chg) else 0,
        "mm_chg_z": chg_z,
        "mm_index_3y": mm_idx_3y,
        "mm_index_1y": mm_idx_1y,
        "pm_net": int(pm.iloc[-1]),
        "pm_index_3y": pm_idx_3y,
        "mm_pct_oi": pct_oi,
        "open_interest": int(oi.iloc[-1]),
        "state": state,
    }


def supply_demand_signals(inv: pd.DataFrame, years: int = 5) -> dict:
    """
    Inventory vs the 5-year seasonal norm for the same week of year (the way the
    energy desks read the EIA report). Stocks below normal = tight market = bullish.
    `surprise_z` is this week's change vs the normal change for this week.
    """
    df = inv.copy().reset_index(drop=True)
    last = df.iloc[-1]
    cutoff = last.date - pd.DateOffset(years=years)
    hist = df[(df.date < last.date - pd.Timedelta(days=180)) & (df.date >= cutoff - pd.Timedelta(days=7))]
    same_wk = hist[(hist.week - last.week).abs() <= 1]
    if len(same_wk) < 3:
        same_wk = df.iloc[:-1].tail(52)
    norm = float(same_wk.stocks.mean())
    dev_pct = float((last.stocks - norm) / norm * 100)

    df["chg"] = df.stocks.diff()
    hist_chg = df[df.index.isin(same_wk.index)]["chg"].dropna()
    norm_chg = float(hist_chg.mean()) if len(hist_chg) else 0.0
    chg_sd = float(df["chg"].tail(156).std() or 1.0)
    this_chg = float(df["chg"].iloc[-1])
    surprise_z = (this_chg - norm_chg) / chg_sd

    dev_sd = float(
        np.std([(r.stocks - norm) / norm * 100 for r in df.tail(156).itertuples()]) or 1.0
    )
    dev_z = dev_pct / dev_sd
    score = -(0.65 * 100 * math.tanh(dev_z / 1.5) + 0.35 * 100 * math.tanh(surprise_z / 1.5))
    balance = "TIGHT" if dev_z < -0.75 else "LOOSE" if dev_z > 0.75 else "BALANCED"
    return {
        "score": _clip(score),
        "date": str(last.date.date()),
        "stocks": float(last.stocks),
        "five_yr_avg": norm,
        "dev_pct": dev_pct,
        "dev_z": dev_z,
        "weekly_chg": this_chg,
        "normal_chg": norm_chg,
        "surprise_z": surprise_z,
        "balance": balance,
        "history": _history_with_norm(df, years),
    }


def _history_with_norm(df: pd.DataFrame, years: int, points: int = 104) -> list[dict]:
    """Last ~2y of stocks with the trailing 5y same-week average for each point."""
    out = []
    for r in df.tail(points).itertuples():
        prior = df[(df.date < r.date - pd.Timedelta(days=180)) & (df.date >= r.date - pd.DateOffset(years=years))]
        wk = prior[(prior.week - r.week).abs() <= 1]
        out.append({"date": str(r.date.date()), "stocks": float(r.stocks),
                    "norm": float(wk.stocks.mean()) if len(wk) else None})
    return out
