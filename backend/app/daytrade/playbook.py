"""Day-trade playbook: classic intraday setups, backtested per market and per regime.

Each setup is a single intraday trade, entered and exited within the same session,
so it can be tested on daily OHLC bars:
  entry from the open / a stop or limit at a known level -> stop -> target -> exit at the close.
Daily bars don't show the order of events inside the day, so each day is walked with the
standard OHLC path model: up days trade open -> low -> high -> close, down days
open -> high -> low -> close. Stop and limit orders fill when the path crosses them (a
gap through a level fills at the open), stops and targets are checked in travel order,
and anything still open exits at the close. Costs (commission + slippage ticks, both
sides) are charged on every trade. Results are per 1 contract. Verify finalists on
intraday data before risking money.

Every day is tagged with the regime known at the prior close (trend / range /
transition), so the screen can answer: "in a market like today's, which setup paid?"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .analytics import regime_series


@dataclass
class Setup:
    key: str
    name: str
    style: str          # Breakout / Reversion / Trend
    fits: tuple         # regimes it is designed for
    rule: str
    fn: Callable


def _ctx(df: pd.DataFrame) -> dict:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(14, min_periods=5).mean().shift(1).bfill().to_numpy()  # known before the session
    sma20 = pd.Series(c).rolling(20).mean().shift(1).to_numpy()
    sma20_prev = pd.Series(c).rolling(20).mean().shift(6).to_numpy()
    rng = h - l
    nr7 = pd.Series(rng).rolling(7).apply(lambda x: float(x[-1] == x.min()), raw=True).shift(1).fillna(0).to_numpy()
    inside = np.r_[False, (h[1:] <= h[:-1]) & (l[1:] >= l[:-1])]
    inside_prev = np.r_[False, inside[:-1]]
    return {"o": o, "h": h, "l": l, "c": c, "atr": atr, "sma20": sma20, "sma20_prev": sma20_prev,
            "nr7": nr7.astype(bool), "inside_prev": inside_prev}


def _path(o, h, l, c):
    """OHLC path model: up days are assumed to trade O->L->H->C, down days O->H->L->C."""
    return [o, l, h, c] if c >= o else [o, h, l, c]


def run_path(path, side, kind, level=None, stop_dist=None, stop_px=None, target=None):
    """Walk the path; enter (market at the open, or a stop/limit order at `level`), then exit at
    stop, target or the close. Returns (entry, exit, reason) or None if the order never filled.
    A gap through a level fills at the open (worse for stops, better for targets)."""
    entered, entry, stop = kind == "market", None, None
    if entered:
        entry = path[0]
        stop = stop_px if stop_px is not None else entry - side * stop_dist
    cur = path[0]
    for nxt in path[1:]:
        seg_lo, seg_hi = min(cur, nxt), max(cur, nxt)
        if not entered:
            if kind == "stop":
                hit = (side > 0 and seg_hi >= level) or (side < 0 and seg_lo <= level)
                fill = (max(cur, level) if side > 0 else min(cur, level))
            else:  # limit
                hit = (side > 0 and seg_lo <= level) or (side < 0 and seg_hi >= level)
                fill = (min(cur, level) if side > 0 else max(cur, level))
            if not hit:
                cur = nxt
                continue
            entered, entry = True, fill
            stop = stop_px if stop_px is not None else entry - side * stop_dist
            cur = fill
            seg_lo, seg_hi = min(cur, nxt), max(cur, nxt)
        # in position: check this segment for stop / target in travel order
        if side > 0:
            if nxt < cur and seg_lo <= stop:
                return entry, min(cur, stop), "stop"
            if target is not None and nxt > cur and seg_hi >= target:
                return entry, max(cur, target) if cur > target else target, "target"
        else:
            if nxt > cur and seg_hi >= stop:
                return entry, max(cur, stop), "stop"
            if target is not None and nxt < cur and seg_lo <= target:
                return entry, min(cur, target) if cur < target else target, "target"
        cur = nxt
    return (entry, path[-1], "close") if entered else None


def _bar(x, i):
    return _path(x["o"][i], x["h"][i], x["l"][i], x["c"][i])


# ---------------------------------------------------------------------- setups -> (side, entry, exit, reason) or None
def _wrap(side, r):
    return None if r is None else (side, *r)


def pdh_pdl_breakout(x, i):
    pdh, pdl, a = x["h"][i - 1], x["l"][i - 1], x["atr"][i]
    path = _bar(x, i)
    # whichever level the path reaches first is traded (one trade per day)
    longr = run_path(path, 1, "stop", level=pdh, stop_dist=0.35 * a)
    shortr = run_path(path, -1, "stop", level=pdl, stop_dist=0.35 * a)
    if longr and shortr:
        first_up = next((p for p in path if p >= pdh or p <= pdl), path[0]) >= pdh
        return _wrap(1, longr) if first_up else _wrap(-1, shortr)
    return _wrap(1, longr) if longr else _wrap(-1, shortr)


def gap_fade(x, i):
    pc, o, a = x["c"][i - 1], x["o"][i], x["atr"][i]
    g = o - pc
    if not (0.25 * a < abs(g) < 1.2 * a):
        return None
    side = -1 if g > 0 else 1
    return _wrap(side, run_path(_bar(x, i), side, "market", stop_dist=0.4 * a, target=pc))


def gap_and_go(x, i):
    pc, o, a = x["c"][i - 1], x["o"][i], x["atr"][i]
    g = o - pc
    if abs(g) <= 0.25 * a:
        return None
    side = 1 if g > 0 else -1
    return _wrap(side, run_path(_bar(x, i), side, "market", stop_dist=0.4 * a))


def pivot_fade(x, i):
    H, L, C = x["h"][i - 1], x["l"][i - 1], x["c"][i - 1]
    p = (H + L + C) / 3
    r1, s1 = 2 * p - L, 2 * p - H
    o = x["o"][i]
    if not (s1 < o < r1):
        return None
    path = _bar(x, i)
    longr = run_path(path, 1, "limit", level=s1, stop_px=s1 - 0.5 * (p - s1), target=p)
    shortr = run_path(path, -1, "limit", level=r1, stop_px=r1 + 0.5 * (r1 - p), target=p)
    if longr and shortr:
        first_dn = next((q for q in path if q <= s1 or q >= r1), o) <= s1
        return _wrap(1, longr) if first_dn else _wrap(-1, shortr)
    return _wrap(1, longr) if longr else _wrap(-1, shortr)


def trend_open_drive(x, i):
    s, sp, pc, o, a = x["sma20"][i], x["sma20_prev"][i], x["c"][i - 1], x["o"][i], x["atr"][i]
    if np.isnan(s) or np.isnan(sp):
        return None
    if pc > s and s > sp and o >= pc:
        side = 1
    elif pc < s and s < sp and o <= pc:
        side = -1
    else:
        return None
    return _wrap(side, run_path(_bar(x, i), side, "market", stop_dist=0.5 * a))


def compression_breakout(x, i):
    if not (x["nr7"][i] or x["inside_prev"][i]):
        return None
    ph, pl, a = x["h"][i - 1], x["l"][i - 1], x["atr"][i]
    mid = (ph + pl) / 2
    path = _bar(x, i)
    longr = run_path(path, 1, "stop", level=ph, stop_px=min(mid, ph - 0.3 * a))
    shortr = run_path(path, -1, "stop", level=pl, stop_px=max(mid, pl + 0.3 * a))
    if longr and shortr:
        first_up = next((q for q in path if q >= ph or q <= pl), path[0]) >= ph
        return _wrap(1, longr) if first_up else _wrap(-1, shortr)
    return _wrap(1, longr) if longr else _wrap(-1, shortr)


SETUPS = [
    Setup("pdh_pdl", "Prior-day high/low breakout", "Breakout", ("trend",),
          "Buy a break above yesterday's high (sell below yesterday's low). Stop 0.35 ATR. Hold to the close.", pdh_pdl_breakout),
    Setup("gap_and_go", "Gap-and-go", "Trend", ("trend", "transition"),
          "Open gaps more than 0.25 ATR: trade in the gap's direction from the open. Stop 0.4 ATR. Hold to the close.", gap_and_go),
    Setup("open_drive", "Trend open-drive", "Trend", ("trend",),
          "With the 20-day trend rising (falling) and the open at or above (below) yesterday's close: enter at the open. Stop 0.5 ATR.", trend_open_drive),
    Setup("gap_fade", "Gap fade to prior close", "Reversion", ("range", "transition"),
          "Open gaps 0.25-1.2 ATR: fade it, target yesterday's close. Stop 0.4 ATR beyond the open.", gap_fade),
    Setup("pivot_fade", "Pivot S1/R1 fade", "Reversion", ("range",),
          "Buy a test of S1 (sell R1), target the pivot. Stop half the S1-pivot distance beyond the level.", pivot_fade),
    Setup("compression", "Inside-day / NR7 breakout", "Breakout", ("transition", "trend"),
          "After an inside day or the narrowest range in 7 days: trade the break of yesterday's range. Stop at the range midpoint.", compression_breakout),
]


def backtest_setups(df: pd.DataFrame, point_value: float, tick: float, cost_per_side: float, slip_ticks: float = 1.0,
                    lookback: int = 756) -> dict:
    """Run every setup over the last `lookback` completed sessions; stats overall and by regime."""
    d = df.iloc[:-1].reset_index(drop=True)          # today's session is incomplete
    x = _ctx(d)
    reg = regime_series(d).shift(1).fillna("transition").to_numpy()   # regime known before each session
    start = max(30, len(d) - lookback)
    rt_cost = 2 * (cost_per_side + slip_ticks * tick * point_value)
    out = {}
    for s in SETUPS:
        trades = []
        for i in range(start, len(d)):
            r = s.fn(x, i)
            if r is None:
                continue
            side, entry, exit_px, why = r
            pnl = (exit_px - entry) * side * point_value - rt_cost
            trades.append({"i": i, "regime": reg[i], "pnl": pnl, "why": why})
        out[s.key] = trades
    return {"trades": out, "days": len(d) - start, "rt_cost": rt_cost,
            "first_date": str(pd.Timestamp(d["date"].iloc[start]).date()), "last_date": str(pd.Timestamp(d["date"].iloc[-1]).date())}


def _stats(trades: list[dict], tick_value: float) -> dict:
    if not trades:
        return {"n": 0}
    p = np.array([t["pnl"] for t in trades])
    wins, losses = p[p > 0], p[p <= 0]
    sd = p.std(ddof=1) if len(p) > 1 else 0.0
    return {
        "n": int(len(p)), "win_pct": round(100 * len(wins) / len(p), 1),
        "avg": round(float(p.mean()), 2), "avg_ticks": round(float(p.mean() / tick_value), 1),
        "pf": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else 99.0,
        "total": round(float(p.sum()), 0),
        "t_stat": round(float(p.mean() / (sd / math.sqrt(len(p)))), 2) if sd > 0 else 0.0,
        "stop_pct": round(100 * sum(t["why"] == "stop" for t in trades) / len(p), 0),
    }


def rank(bt: dict, current_regime: str, tick_value: float, min_trades: int = 12) -> list[dict]:
    rows = []
    for s in SETUPS:
        tr = bt["trades"][s.key]
        allst = _stats(tr, tick_value)
        reg = _stats([t for t in tr if t["regime"] == current_regime], tick_value)
        use = reg if reg.get("n", 0) >= min_trades else allst
        # Rank on reliability-weighted edge: average $/trade scaled by t-stat confidence (capped)
        conf = max(0.0, min(1.0, (use.get("t_stat", 0) + 1) / 3)) if use.get("n") else 0
        score = (use.get("avg", 0) or 0) * (0.5 + conf)
        fit = current_regime in s.fits
        avg, pf, t = use.get("avg", 0) or 0, use.get("pf", 0) or 0, use.get("t_stat", 0) or 0
        # A positive average isn't enough: require profit factor AND statistical confidence (t-stat)
        verdict = ("PREFERRED" if avg > 0 and pf >= 1.15 and t >= 1.0 and fit else
                   "WORKS" if avg > 0 and pf >= 1.05 and t >= 0.5 else
                   "MARGINAL" if avg > 0 else "AVOID")
        rows.append({"key": s.key, "name": s.name, "style": s.style, "rule": s.rule, "fits_regime": fit,
                     "designed_for": list(s.fits), "all": allst, "regime": reg, "used": "regime" if use is reg else "all",
                     "score": round(score, 2), "verdict": verdict})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def todays_setups(prev: pd.Series, today_open: float, atr: float, sma20: float, sma20_prev: float, tick: float,
                  prev_inside: bool, prev_nr7: bool) -> dict:
    """Concrete trigger / stop / target prices for today, per setup."""
    rnd = lambda v: round(round(v / tick) * tick, 6)  # noqa: E731
    H, L, C = float(prev.high), float(prev.low), float(prev.close)
    p = (H + L + C) / 3
    r1, s1 = 2 * p - L, 2 * p - H
    g = today_open - C
    out = {
        "pdh_pdl": {"long": f"Buy stop {rnd(H + tick)}, stop {rnd(H - 0.35 * atr)}",
                    "short": f"Sell stop {rnd(L - tick)}, stop {rnd(L + 0.35 * atr)}", "active": True},
        "pivot_fade": {"long": f"Buy limit {rnd(s1)}, stop {rnd(s1 - 0.5 * (p - s1))}, target {rnd(p)}",
                       "short": f"Sell limit {rnd(r1)}, stop {rnd(r1 + 0.5 * (r1 - p))}, target {rnd(p)}",
                       "active": s1 < today_open < r1},
        "gap_fade": {"active": 0.25 * atr < abs(g) < 1.2 * atr,
                     "note": (f"Gap {'up' if g > 0 else 'down'} {abs(g / atr):.2f} ATR: "
                              f"{'sell' if g > 0 else 'buy'} the open {rnd(today_open)}, target {rnd(C)}, "
                              f"stop {rnd(today_open + (0.4 if g > 0 else -0.4) * atr)}")
                     if 0.25 * atr < abs(g) < 1.2 * atr else f"Not today: gap is {abs(g / atr):.2f} ATR (needs 0.25-1.2)"},
        "gap_and_go": {"active": abs(g) > 0.25 * atr,
                       "note": (f"{'Buy' if g > 0 else 'Sell'} the open {rnd(today_open)}, stop "
                                f"{rnd(today_open - (0.4 if g > 0 else -0.4) * atr)}") if abs(g) > 0.25 * atr else
                       f"Gap only {abs(g) / atr:.2f} ATR: no gap setup today"},
        "open_drive": {"active": bool(C > sma20 > sma20_prev and today_open >= C) or bool(C < sma20 < sma20_prev and today_open <= C),
                       "note": "Trend + open aligned" if (C > sma20 > sma20_prev and today_open >= C) or (C < sma20 < sma20_prev and today_open <= C)
                       else "Open not aligned with the 20-day trend"},
        "compression": {"active": bool(prev_inside or prev_nr7),
                        "note": f"Yesterday was {'an inside day' if prev_inside else 'NR7' if prev_nr7 else 'a normal-range day'}: "
                                + (f"buy > {rnd(H)} / sell < {rnd(L)}, stop at midpoint {rnd((H + L) / 2)}" if (prev_inside or prev_nr7) else "no setup")},
    }
    for k in ("pdh_pdl", "pivot_fade"):
        out[k]["note"] = out[k]["long"] + " · " + out[k]["short"]
    return out
