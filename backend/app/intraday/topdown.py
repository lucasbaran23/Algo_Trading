"""Top-down multi-timeframe analysis: Weekly -> Daily -> 4H -> 1H -> 15m -> 5m -> 1m.

The higher timeframes (W, D) set the bias, the middle ones (4H, 1H) show where we are in the
swing (trend leg or pullback), and the lower ones (15m, 5m, 1m) time the entry. For each
timeframe: trend (EMA20/EMA50 stack and slope), market structure (higher highs/lows from
swing pivots), momentum (RSI 14), volatility (ATR and its percentile) and the nearest swing
levels. Scores roll up into an alignment score and a plain-language plan.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import universe
from ..data import intraday

TFS = [("W", "Weekly", 0.25), ("D", "Daily", 0.22), ("4H", "4-hour", 0.16), ("1H", "1-hour", 0.14),
       ("15m", "15-minute", 0.11), ("5m", "5-minute", 0.08), ("1m", "1-minute", 0.04)]
ROLE = {"W": "bias", "D": "bias", "4H": "swing", "1H": "swing", "15m": "entry", "5m": "entry", "1m": "entry"}


def _rsi(c: pd.Series, n: int = 14) -> float:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    v = 100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1]) if dn.iloc[-1] > 0 else 100.0
    return float(v)


def _swings(h: np.ndarray, l: np.ndarray, k: int = 3) -> tuple[list, list]:
    highs, lows = [], []
    for i in range(k, len(h) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append((i, h[i]))
        if l[i] == l[i - k:i + k + 1].min():
            lows.append((i, l[i]))
    return highs, lows


def analyse(bars: pd.DataFrame, tick: float) -> dict:
    b = bars.tail(300)
    c, h, l = b["close"], b["high"].to_numpy(), b["low"].to_numpy()
    if len(b) < 30:
        return {"ok": False}
    e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([b["high"] - b["low"], (b["high"] - c.shift()).abs(), (b["low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    last, a = float(c.iloc[-1]), float(atr.iloc[-1])
    slope = float((e20.iloc[-1] - e20.iloc[-6]) / (5 * a)) if a > 0 else 0.0
    stack = 1 if last > e20.iloc[-1] > e50.iloc[-1] else -1 if last < e20.iloc[-1] < e50.iloc[-1] else 0
    hs, ls = _swings(h, l)
    struct, s_sc = "RANGE", 0
    if len(hs) >= 2 and len(ls) >= 2:
        hh, hl = hs[-1][1] > hs[-2][1], ls[-1][1] > ls[-2][1]
        if hh and hl:
            struct, s_sc = "HH / HL (uptrend)", 1
        elif not hh and not hl:
            struct, s_sc = "LH / LL (downtrend)", -1
        else:
            struct = "mixed swings"
    rsi = _rsi(c)
    score = stack + s_sc + (0.5 if slope > 0.1 else -0.5 if slope < -0.1 else 0) + (0.5 if rsi > 55 else -0.5 if rsi < 45 else 0)
    score = max(-2.0, min(2.0, score))
    label = ("STRONG UP" if score >= 1.5 else "UP" if score >= 0.5 else "STRONG DOWN" if score <= -1.5 else
             "DOWN" if score <= -0.5 else "NEUTRAL")
    rng = b["high"].tail(20).max() - b["low"].tail(20).min()
    pos = float((last - b["low"].tail(20).min()) / rng * 100) if rng > 0 else 50.0
    atr_pct = float((atr.dropna().tail(100) < a).mean() * 100) if atr.notna().sum() > 20 else 50.0
    rnd = lambda v: round(round(v / tick) * tick, 6)  # noqa: E731
    res_lv = [v for _, v in hs if v > last]
    sup_lv = [v for _, v in ls if v < last]
    return {
        "ok": True, "last": last, "score": round(score, 2), "label": label, "structure": struct,
        "ema20": rnd(e20.iloc[-1]), "ema50": rnd(e50.iloc[-1]), "slope_atr": round(slope, 2), "rsi": round(rsi, 1),
        "atr": a, "atr_ticks": int(round(a / tick)), "atr_pct": round(atr_pct, 0), "range_pos": round(pos, 0),
        "swing_high": rnd(min(res_lv)) if res_lv else rnd(b["high"].tail(50).max()),
        "swing_low": rnd(max(sup_lv)) if sup_lv else rnd(b["low"].tail(50).min()),
        "spark": [round(float(x), 6) for x in c.tail(60)],
        "time": str(b.index[-1]),
    }


def topdown(md, symbol: str) -> dict:
    c = universe.get(symbol)
    sym = c.data_symbol
    tick = c.tick_size
    daily = md.daily[sym].set_index("date")[["open", "high", "low", "close", "volume"]]
    weekly = daily.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    m1, src = intraday.bars(md, sym)
    frames = {"W": weekly, "D": daily, "4H": intraday.resample(m1, "4h"), "1H": intraday.resample(m1, "1h"),
              "15m": intraday.resample(m1, "15min"), "5m": intraday.resample(m1, "5min"), "1m": m1}
    rows = []
    for key, name, w in TFS:
        a = analyse(frames[key], tick)
        if a.get("ok"):
            rows.append({"tf": key, "name": name, "weight": w, "role": ROLE[key], **a})
    tot_w = sum(r["weight"] for r in rows) or 1
    align = round(100 * sum(r["score"] / 2 * r["weight"] for r in rows) / tot_w, 0)
    bias = [r for r in rows if r["role"] == "bias"]
    swing = [r for r in rows if r["role"] == "swing"]
    entry = [r for r in rows if r["role"] == "entry"]
    avg = lambda rs: sum(r["score"] for r in rs) / len(rs) if rs else 0  # noqa: E731
    b, s, e = avg(bias), avg(swing), avg(entry)
    d1 = next((r for r in rows if r["tf"] == "4H"), None)      # pullback zone = nearest 4H swing levels
    m5 = next((r for r in rows if r["tf"] == "5m"), None)
    fmt = lambda v: f"{v:,.{max(0, len(f'{tick:.10f}'.rstrip('0').split('.')[1]))}f}"  # noqa: E731
    if b >= 0.5 and s >= 0.5 and e >= 0.5:
        setup = "ALIGNED UP"
        plan = ("All timeframes point up: a trend-day environment. Only take longs: buy pullbacks to the 5-minute EMA20 / "
                "VWAP, and add on breaks of 5-minute swing highs. Shorts fight every timeframe.")
    elif b <= -0.5 and s <= -0.5 and e <= -0.5:
        setup = "ALIGNED DOWN"
        plan = ("All timeframes point down: only take shorts. Sell rallies into the 5-minute EMA20 / VWAP and add on "
                "breaks of 5-minute swing lows. Longs fight every timeframe.")
    elif b >= 0.5 and s < 0:
        setup = "PULLBACK IN UPTREND"
        plan = (f"Higher timeframes are up, but the 4H/1H are pulling back. The high-probability trade is the end of the "
                f"pullback: wait for the 15m/5m to turn back up (a break of the last 5m swing high"
                f"{' ' + fmt(m5['swing_high']) if m5 else ''}) near support"
                f"{' ' + fmt(d1['swing_low']) if d1 else ''}, then go long. Don't short the pullback for more than a scalp.")
    elif b <= -0.5 and s > 0:
        setup = "RALLY IN DOWNTREND"
        plan = (f"Higher timeframes are down, but the 4H/1H are bouncing. Wait for the bounce to fail: the 15m/5m turning back "
                f"down (a break of the last 5m swing low{' ' + fmt(m5['swing_low']) if m5 else ''}) near resistance"
                f"{' ' + fmt(d1['swing_high']) if d1 else ''}, then go short.")
    elif abs(b) < 0.5:
        setup = "NO HIGHER-TIMEFRAME BIAS"
        plan = ("Weekly and daily disagree or are flat: expect two-way, range-bound trade. Fade the extremes of the day's "
                "range and the 1H swing levels, take profits quickly, and trade smaller.")
    else:
        setup = "MIXED"
        plan = ("Timeframes conflict. Only take setups where the 15m and 5m agree with the daily, reduce size, and skip "
                "trades into nearby higher-timeframe levels.")
    exec_tf = "5m" if abs(align) >= 40 else "15m"
    return {"symbol": symbol, "source": src, "rows": rows, "alignment": align, "setup": setup, "plan": plan,
            "bias_score": round(b, 2), "swing_score": round(s, 2), "entry_score": round(e, 2),
            "exec_tf": exec_tf,
            "exec_note": ("Strong alignment: execute on the 5-minute chart and let trends run." if exec_tf == "5m" else
                          "Weak alignment: execute on the 15-minute chart to filter noise, and be selective.")}
