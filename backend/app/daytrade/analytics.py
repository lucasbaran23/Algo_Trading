"""Day-trading analytics from daily bars (+ today's intraday session).

* Market regime: ADX(14), Kaufman efficiency ratio and the Choppiness Index decide
  TRENDING vs RANGE-BOUND, the question that decides *which* setups to trade today.
* Range statistics: average daily range (ADR), percentiles, how much of today's
  "normal" range is already used, and projected high/low bands.
* Composite volume profile: each prior session's volume is spread over its high-low
  range (weighted towards the close) to estimate POC / value area / high- and
  low-volume nodes. Daily bars can only approximate a real profile; with a tick feed,
  swap in true price-by-volume.
* Weekday seasonality: how this market behaves on today's weekday.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[float, float, float]:
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = _true_range(df).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    a = dx.ewm(alpha=1 / n, adjust=False).mean()
    return float(a.iloc[-1]), float(pdi.iloc[-1]), float(mdi.iloc[-1])


def adx_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = _true_range(df).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr
    mdi = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def efficiency_ratio(closes: pd.Series, n: int = 20) -> float:
    ch = abs(closes.iloc[-1] - closes.iloc[-1 - n])
    vol = closes.diff().abs().tail(n).sum()
    return float(ch / vol) if vol else 0.0


def choppiness(df: pd.DataFrame, n: int = 14) -> float:
    tr_sum = _true_range(df).tail(n).sum()
    rng = df["high"].tail(n).max() - df["low"].tail(n).min()
    return float(100 * math.log10(tr_sum / rng) / math.log10(n)) if rng > 0 and tr_sum > 0 else 50.0


def regime(df: pd.DataFrame) -> dict:
    a, pdi, mdi = adx(df)
    closes = df["close"]
    er = efficiency_ratio(closes)
    chop = choppiness(df)
    sma20, sma50 = closes.tail(20).mean(), closes.tail(50).mean()
    slope = (closes.tail(20).mean() - closes.iloc[-25:-5].mean()) / (closes.tail(20).std() + 1e-12)
    # Direction from the DI lines; only "MIXED" when they're close AND price disagrees with the 20-day average
    if abs(pdi - mdi) < 5 and ((pdi > mdi) != (closes.iloc[-1] > sma20)):
        direction = "MIXED"
    else:
        direction = "UP" if pdi > mdi else "DOWN"
    trend_votes = (a >= 25) + (er >= 0.35) + (chop <= 45)
    range_votes = (a < 20) + (er < 0.2) + (chop >= 60)
    if trend_votes >= 2 and direction != "MIXED":
        label = f"TRENDING {direction}"
        kind = "trend"
    elif range_votes >= 2:
        label, kind = "RANGE-BOUND", "range"
    else:
        label, kind = "TRANSITION", "transition"
    tips = {
        "trend": "Trade with the trend: buy pullbacks to VWAP / prior-day levels (or sell rallies), let winners run, "
                 "don't fade strength. Breakouts of the prior-day high/low tend to follow through.",
        "range": "Fade the edges: sell near value-area high / R1, buy near value-area low / S1, take profits near the POC "
                 "or pivot. Be skeptical of breakouts; wait for acceptance outside the range.",
        "transition": "No clean regime: trade smaller, demand confirmation (opening-range break plus a VWAP hold), and "
                      "take quicker profits. Watch for a volatility expansion to pick the direction.",
    }
    return {"label": label, "kind": kind, "direction": direction, "adx": round(a, 1), "plus_di": round(pdi, 1),
            "minus_di": round(mdi, 1), "efficiency": round(er, 2), "chop": round(chop, 1),
            "above_sma20": bool(closes.iloc[-1] > sma20), "above_sma50": bool(closes.iloc[-1] > sma50),
            "slope_z": round(float(slope), 2), "tip": tips[kind]}


def regime_series(df: pd.DataFrame) -> pd.Series:
    """Per-day regime ('trend'/'range'/'transition') known at each day's close. Used to test setups by regime."""
    a = adx_series(df)
    c = df["close"]
    er = (c - c.shift(20)).abs() / c.diff().abs().rolling(20).sum()
    tr = _true_range(df)
    chop = 100 * np.log10(tr.rolling(14).sum() / (df["high"].rolling(14).max() - df["low"].rolling(14).min())) / math.log10(14)
    tv = (a >= 25).astype(int) + (er >= 0.35).astype(int) + (chop <= 45).astype(int)
    rv = (a < 20).astype(int) + (er < 0.2).astype(int) + (chop >= 60).astype(int)
    return pd.Series(np.where(tv >= 2, "trend", np.where(rv >= 2, "range", "transition")), index=df.index)


def ranges(df: pd.DataFrame, today_high: float, today_low: float, today_open: float, last: float) -> dict:
    hist = df.iloc[:-1]
    rng = (hist["high"] - hist["low"]).tail(60)
    tr = _true_range(hist).tail(20)
    adr = float(rng.tail(20).mean())
    atr = float(tr.mean())
    used = (today_high - today_low) / adr if adr else 0.0
    p = lambda q: float(np.percentile(rng, q))  # noqa: E731
    pct_rank = float((rng < (hist["high"].iloc[-1] - hist["low"].iloc[-1])).mean() * 100)
    return {
        "adr20": adr, "atr20": atr, "range_p25": p(25), "range_p50": p(50), "range_p80": p(80),
        "today_range": today_high - today_low, "adr_used_pct": round(100 * used, 1),
        "prev_range_pct_rank": round(pct_rank, 0),
        # If today trades a normal ADR, these bracket the day (anchored to whichever extreme is already set)
        "proj_high": today_low + adr, "proj_low": today_high - adr,
        "open_up_1adr": today_open + adr / 2, "open_dn_1adr": today_open - adr / 2,
        "remaining": max(0.0, adr - (today_high - today_low)),
    }


def volume_profile(df: pd.DataFrame, tick: float, sessions: int = 20, bins: int = 48) -> dict:
    """Composite profile of the last `sessions` days from daily OHLCV (triangular weight towards each close)."""
    d = df.iloc[:-1].tail(sessions)
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if hi <= lo:
        return {}
    step = max(tick, (hi - lo) / bins)
    edges = np.arange(lo, hi + step, step)
    mids = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(len(mids))
    for r in d.itertuples():
        mask = (mids >= r.low) & (mids <= r.high)
        if not mask.any():
            continue
        span = max(r.high - r.low, tick)
        w = 1.0 + 1.5 * (1 - np.abs(mids[mask] - r.close) / span)   # more time/volume near where it settled
        vol[mask] += (r.volume or 1) * w / w.sum()
    poc_i = int(vol.argmax())
    # value area: expand from POC until 70% of volume is enclosed
    total, inside = vol.sum(), vol[poc_i]
    a, b = poc_i, poc_i
    while inside < 0.7 * total and (a > 0 or b < len(vol) - 1):
        up = vol[b + 1] if b < len(vol) - 1 else -1
        dn = vol[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1
            inside += up
        else:
            a -= 1
            inside += dn
    rnd = lambda x: round(round(x / tick) * tick, 6)  # noqa: E731
    mean = vol.mean()
    hvn = [rnd(mids[i]) for i in range(1, len(vol) - 1) if vol[i] > 1.3 * mean and vol[i] >= vol[i - 1] and vol[i] >= vol[i + 1]]
    lvn = [rnd(mids[i]) for i in range(1, len(vol) - 1) if vol[i] < 0.5 * mean and vol[i] <= vol[i - 1] and vol[i] <= vol[i + 1]]
    return {
        "sessions": sessions, "poc": rnd(mids[poc_i]), "vah": rnd(edges[b + 1]), "val": rnd(edges[a]),
        "hvn": hvn[:4], "lvn": lvn[:4],
        "bins": [{"price": rnd(m), "vol": round(float(v / vol.max()), 4)} for m, v in zip(mids, vol)],
        "estimated": True,
    }


def weekday_stats(df: pd.DataFrame, weekday: int, years: int = 5) -> dict:
    d = df.tail(252 * years).copy()
    d["ret"] = d["close"].pct_change()
    d["rng"] = (d["high"] - d["low"]) / d["close"].shift()
    d["wd"] = pd.to_datetime(d["date"]).dt.weekday
    sub, allr = d[d.wd == weekday].dropna(), d.dropna()
    if len(sub) < 20:
        return {}
    return {
        "weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][weekday],
        "samples": int(len(sub)),
        "up_pct": round(100 * float((sub.ret > 0).mean()), 1),
        "avg_ret_pct": round(100 * float(sub.ret.mean()), 3),
        "avg_range_pct": round(100 * float(sub.rng.mean()), 2),
        "range_vs_avg": round(float(sub.rng.mean() / allr.rng.mean()), 2),
    }


def month_stats(df: pd.DataFrame, month: int) -> dict:
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    m = d.set_index("date")["close"].resample("ME").last().pct_change().dropna()
    sub = m[m.index.month == month]
    if len(sub) < 3:
        return {}
    return {"month": pd.Timestamp(2000, month, 1).strftime("%B"), "years": int(len(sub)),
            "up_pct": round(100 * float((sub > 0).mean()), 0), "avg_ret_pct": round(100 * float(sub.mean()), 2)}
