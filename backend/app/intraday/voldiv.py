"""Volatility + key level + RSI divergence reversal.

The idea: big moves often exhaust at levels other traders watch. When a high-volatility move pushes
into a key level and momentum fades (price makes a new extreme but RSI doesn't), the move is
running out of buyers/sellers. Trade the reversal once price confirms.

Three filters, then a trigger (long side; the short side mirrors it)
  1. Volatility: the move from yesterday's close is at least k sigma for the time of day, sigma
     from the same calendar month in earlier years (see extremes.py). k = 0 switches it off.
  2. Key level: the new low is within 0.15 daily sigma of a level: prior-day high/low, prior-week
     high/low, yesterday's close, and (NG/ES/NQ/CL/GC) the put wall, call wall, gamma flip and
     max pain known before the session.
  3. Bullish RSI divergence on N-minute bars: price makes a lower low than an earlier swing low
     (within the last 30 bars) while RSI(14) makes a higher low, and RSI was oversold (< 35) at
     the first low.
  Trigger: within 3 bars, an N-minute bar closes above the high of the divergence bar.
  Entry at that close. Stop 0.1 sigma below the lowest low since the divergence. Target 2R
  (or VWAP if it is closer but at least 1R away). Flat by the close.

The backtest switches each filter on and off (ablation) so you can see what each one adds, and
splits sessions 60/40 in-sample / out-of-sample like the research tab.
"""
from __future__ import annotations

import itertools
import math
import time

import numpy as np
import pandas as pd

from .. import universe
from ..data import intraday
from . import extremes as ex
from . import research as ir

LEVEL_NAMES = {"prev_high": "Prior-day high", "prev_low": "Prior-day low", "prev_close": "Prior close",
               "week_high": "Prior-week high", "week_low": "Prior-week low", "call_wall": "Call wall",
               "put_wall": "Put wall", "flip": "Gamma flip", "max_pain": "Max pain"}


# ---------------------------------------------------------------------- building blocks
def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    au, ad = up[:n].mean(), dn[:n].mean()
    out[n] = 100 - 100 / (1 + au / ad) if ad > 0 else 100.0
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i - 1]) / n
        ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100 - 100 / (1 + au / ad) if ad > 0 else 100.0
    return out


def tf_bars(m, o, h, l, c, tf: int):
    """Aggregate 1-minute arrays into tf-minute bars: (last 1m index, minute, o, h, l, c)."""
    edges = np.searchsorted(m, np.arange(tf, int(m[-1]) + tf, tf), side="right")
    rows, a = [], 0
    for b in edges:
        if b > a:
            rows.append((b - 1, m[b - 1], o[a], h[a:b].max(), l[a:b].min(), c[b - 1]))
        a = b
    if not rows:
        return None
    arr = np.array(rows, dtype=float)
    return {"i": arr[:, 0].astype(int), "m": arr[:, 1], "o": arr[:, 2], "h": arr[:, 3], "l": arr[:, 4], "c": arr[:, 5]}


def divergence(b: dict, r: np.ndarray, n: int, side: int, lookback: int = 30, ob: float = 40.0):
    """Index of the earlier swing point if bar n forms a regular divergence (side 1 bullish, -1 bearish)."""
    lo, hi = b["l"], b["h"]
    start = max(0, n - lookback)
    if n - start < 6 or not np.isfinite(r[n]):
        return None
    if side > 0:
        if lo[n] > lo[start:n].min():                  # must be a new low for the window
            return None
        for p in range(n - 4, start - 1, -1):
            a, z = max(0, p - 3), min(n, p + 4)
            if lo[p] == lo[a:z].min() and np.isfinite(r[p]) and r[p] < ob and lo[n] < lo[p] and r[n] > r[p] + 3:
                return p
    else:
        if hi[n] < hi[start:n].max():
            return None
        for p in range(n - 4, start - 1, -1):
            a, z = max(0, p - 3), min(n, p + 4)
            if hi[p] == hi[a:z].max() and np.isfinite(r[p]) and r[p] > 100 - ob and hi[n] > hi[p] and r[n] < r[p] - 3:
                return p
    return None


def near_level(px: float, levels: dict, tol: float):
    best = None
    for k, v in levels.items():
        if v is None or not np.isfinite(v):
            continue
        dist = abs(px - v)
        if dist <= tol and (best is None or dist < best[1]):
            best = (k, dist, v)
    return best


# ---------------------------------------------------------------------- one session
def leg_z(b: dict, n: int, sd_day: float, gs: float, T: float, tf: int, pc: float, bars: int = 6) -> float:
    """Speed of the last `bars` bars (the push into the level) in sigma for that length of time."""
    j = max(0, n - bars)
    span = max(tf, b["m"][n] - b["m"][j])
    return math.log(b["c"][n] / b["c"][j]) / (sd_day * math.sqrt((1 - gs) * span / T))


def session_trade(s, levels: dict, sd_day: float, gs: float, tf: int = 5, k: float = 1.5,
                  use_level: bool = True, use_div: bool = True, vol: str = "leg"):
    pc = s["prev_close"]
    if pc is None or not np.isfinite(sd_day) or sd_day <= 0:
        return None
    m, o, h, l, c, vw = s["m"], s["o"], s["h"], s["l"], s["c"], s["vwap"]
    b = tf_bars(m, o, h, l, c, tf)
    if b is None or len(b["c"]) < 20:
        return None
    r = rsi(b["c"])
    T = float(m[-1])
    u = sd_day * pc
    tol = 0.25 * u
    for n in range(15, len(b["c"]) - 1):
        if b["m"][n] > T - 45:
            break
        if vol == "day":
            frac = gs + (1 - gs) * b["m"][n] / T
            z = math.log(b["c"][n] / pc) / (sd_day * math.sqrt(frac))
            z_dn, z_up = z, z
        else:
            # the impulse that drove price into the area: the fastest 6-bar leg in the last 30 bars
            legs = [leg_z(b, q, sd_day, gs, T, tf, pc) for q in range(max(6, n - 30), n + 1)]
            z_dn, z_up = min(legs), max(legs)
            z = z_dn if abs(z_dn) > abs(z_up) else z_up
        for side in (1, -1):
            if vol != "off" and (z_dn if side > 0 else z_up) * side > -k:   # need a fast move AGAINST the trade's side
                continue
            ext = b["l"][n] if side > 0 else b["h"][n]
            win = b["l"][max(0, n - 12):n] if side > 0 else b["h"][max(0, n - 12):n]
            if (side > 0 and ext > win.min()) or (side < 0 and ext < win.max()):
                continue                                      # not a fresh extreme
            lvl = near_level(ext, levels, tol) if use_level else None
            if use_level and lvl is None:
                continue
            p = divergence(b, r, n, side) if use_div else None
            if use_div and p is None:
                continue
            trig = b["h"][n] if side > 0 else b["l"][n]
            for q in range(n + 1, min(len(b["c"]), n + 4)):
                ext = min(ext, b["l"][q]) if side > 0 else max(ext, b["h"][q])
                if (b["c"][q] - trig) * side > 0:
                    entry = b["c"][q]
                    stop = ext - side * 0.1 * u
                    risk = abs(entry - stop)
                    if risk <= 0:
                        return None
                    i0 = b["i"][q]
                    v_now = vw[i0]
                    tgt = entry + side * 2 * risk
                    if (v_now - entry) * side >= risk and (v_now - tgt) * side < 0:
                        tgt = v_now
                    for j in range(i0 + 1, len(c)):
                        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
                            return side, entry, stop, n, lvl, z, p
                        if (side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt):
                            return side, entry, tgt, n, lvl, z, p
                    return side, entry, c[-1], n, lvl, z, p
            break                                             # never confirmed: keep scanning for a later setup
    return None


# ---------------------------------------------------------------------- level history
def level_history(md, sym: str, sessions: list[dict]) -> dict:
    d = md.daily[sym].copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d = d.set_index("date")
    wk = d.resample("W-FRI").agg({"high": "max", "low": "min"})
    out, prev = {}, None
    opt = None
    from ..options import chains
    has_csv = (chains.DATA_DIR / f"{sym}_levels.csv").exists()
    if sym in chains.SUPPORTED and (has_csv or not md.settings.live):   # live: only real option levels
        try:
            from ..options import levels as olv
            opt, _ = _opt_hist(md, sym, [s["date"] for s in sessions], olv)
        except Exception:  # noqa: BLE001
            opt = None
    for s in sessions:
        ts = pd.Timestamp(s["date"])
        w = wk[wk.index < ts - pd.Timedelta(days=ts.weekday())]
        lv = {"prev_close": s["prev_close"]}
        if prev is not None:
            lv["prev_high"], lv["prev_low"] = float(prev["h"].max()), float(prev["l"].min())
        if len(w):
            lv["week_high"], lv["week_low"] = float(w["high"].iloc[-1]), float(w["low"].iloc[-1])
        if opt is not None and ts in opt.index:
            for kk in ("call_wall", "put_wall", "flip", "max_pain"):
                v = opt.loc[ts].get(kk)
                if v is not None and not pd.isna(v):
                    lv[kk] = float(v)
        out[s["date"]] = lv
        prev = s
    return out


_OPT: dict = {}


def _opt_hist(md, sym, dates, olv):
    key = (sym, len(dates), str(dates[-1]) if dates else "")
    if key not in _OPT:
        _OPT[key] = olv.history(md, sym, dates)
    return _OPT[key]


# ---------------------------------------------------------------------- backtest
_BT: dict = {}


def backtest(md, symbol: str, commission: float = 2.5, slip_ticks: float = 1.0) -> dict:
    c = universe.get(symbol)
    sym = c.data_symbol
    key = (sym, commission, slip_ticks, len(md.daily[sym]))
    hit = _BT.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    pv, tv = c.point_value, c.tick_value
    rt = 2 * (commission + slip_ticks * tv)
    df, src = intraday.bars(md, sym)
    sessions = ir.prepare_sessions(df, sym)
    d = ex.daily_frame(md, sym, "month")
    d.index = pd.to_datetime(d["date"]).dt.normalize()
    lh = level_history(md, sym, sessions)
    S = len(sessions)
    split = int(S * 0.6)
    grid = []
    best = None
    vols = (("off", 0.0), ("day", 1.5), ("leg", 1.5), ("leg", 2.0))
    for tf, (vol, k), use_level, use_div in itertools.product((5, 15), vols, (True, False), (True, False)):
        idx, pnl, side, info = [], [], [], []
        for si, s in enumerate(sessions):
            ts = pd.Timestamp(s["date"])
            if ts not in d.index:
                continue
            row = d.loc[ts]
            gs = float(row["gap_share"]) if np.isfinite(row["gap_share"]) else 0.2
            t = session_trade(s, lh[s["date"]], float(row["sd"]), gs, tf, k, use_level, use_div, vol)
            if t:
                idx.append(si); side.append(t[0]); pnl.append((t[2] - t[1]) * t[0] * pv - rt)
                info.append(t)
        idx_a, pnl_a, side_a = np.array(idx, int), np.array(pnl), np.array(side)
        row = {"tf": tf, "vol": vol, "k": k, "level": use_level, "div": use_div,
               "all": ir._stats(pnl_a, tv), "is": ir._stats(pnl_a[idx_a < split], tv), "oos": ir._stats(pnl_a[idx_a >= split], tv),
               "longs": ir._stats(pnl_a[side_a > 0], tv), "shorts": ir._stats(pnl_a[side_a < 0], tv)}
        row["_dates"] = [(str(sessions[i]["date"]), t[0]) for i, t in zip(idx, info)]
        grid.append(row)
        sc = ir._score(pnl_a[idx_a < split], 6)
        if best is None or sc > best[0]:
            best = (sc, row, idx_a, pnl_a, info)
    _, brow, bidx, bpnl, binfo = best
    cum = np.cumsum(bpnl) if len(bpnl) else np.array([])
    trades = []
    for si, p, t in list(zip(bidx, bpnl, binfo))[-12:][::-1]:
        lvl = t[4]
        trades.append({"date": str(sessions[si]["date"]), "side": "LONG" if t[0] > 0 else "SHORT", "entry": round(float(t[1]), 6),
                       "exit": round(float(t[2]), 6), "pnl": round(float(p), 0), "z": round(float(t[5]), 2),
                       "level": LEVEL_NAMES.get(lvl[0], lvl[0]) if lvl else "—", "divergence": t[6] is not None})
    fv = ("leg", 1.5)                                       # the strategy as defined; the grid shows the alternatives
    full = next(g for g in grid if g["tf"] == 5 and (g["vol"], g["k"]) == fv and g["level"] and g["div"])
    same_vol = lambda g: (g["vol"], g["k"]) == fv  # noqa: E731
    ablation = []
    for lab, f in (("All three filters", lambda g: same_vol(g) and g["level"] and g["div"]),
                   ("Without the volatility filter", lambda g: g["vol"] == "off" and g["level"] and g["div"]),
                   ("Without the key level", lambda g: same_vol(g) and not g["level"] and g["div"]),
                   ("Without RSI divergence", lambda g: same_vol(g) and g["level"] and not g["div"]),
                   ("Reversal bar only (no filters)", lambda g: g["vol"] == "off" and not g["level"] and not g["div"])):
        g = next(x for x in grid if x["tf"] == full["tf"] and f(x))
        ablation.append({"label": lab, **{kk: g[kk] for kk in ("all", "is", "oos")}})
    full_dates = full.get("_dates", [])
    for g in grid:
        g.pop("_dates", None)
    out = {"symbol": sym, "source": src, "sessions": S, "full_setup_dates": full_dates[-20:], "split": str(sessions[split]["date"]) if S > split else None,
           "rt_cost": round(rt, 2), "grid": grid,
           "chosen": {kk: brow[kk] for kk in ("tf", "vol", "k", "level", "div")}, "oos": brow["oos"],
           "equity": [{"time": str(sessions[i]["date"]), "value": round(float(v), 0)} for i, v in zip(bidx, cum)],
           "trades": trades, "ablation": ablation, "ablation_tf": full["tf"], "ablation_vol": f"{fv[0]} ≥ {fv[1]}σ",
           "has_options_levels": any(k in lh[sessions[-1]["date"]] for k in ("call_wall", "put_wall")) if sessions else False}
    _BT[key] = (time.time(), out)
    return out


# ---------------------------------------------------------------------- today / live
def today(md, symbol: str, tf: int = 5, k: float = 1.5, on: str | None = None) -> dict:
    """Run the detector on the current session (or the last completed one if today has too few bars)."""
    c = universe.get(symbol)
    sym = c.data_symbol
    ses = md.sessions[sym]
    bars = ses.bars
    label = "today (live session)"
    if len(bars) >= 6 * tf and on is None:
        a = np.array([[b[1], b[2], b[3], b[4]] for b in bars], float)
        m = np.arange(1, len(a) + 1)
        o, h, l, cl = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
        pc = float(ses.prev_close)
        T = None
    else:
        df, _ = intraday.bars(md, sym)
        ss = ir.prepare_sessions(df, sym)
        s = next((x for x in ss if str(x["date"]) == on), ss[-1]) if on else ss[-1]
        m, o, h, l, cl, pc = s["m"], s["o"], s["h"], s["l"], s["c"], s["prev_close"]
        label = f"example session with an A+ setup ({s['date']})" if on else f"last completed session ({s['date']})"
        T = float(m[-1])
    ref = ex.live_ref(md, sym) or {"sd": 0.02, "gs": 0.2, "mu": 0.0, "pc": pc}
    if on:                                                   # the reference sigma that applied on that day
        dfr = ex.daily_frame(md, sym, "month")
        dfr.index = pd.to_datetime(dfr["date"]).dt.normalize()
        if pd.Timestamp(on) in dfr.index:
            rr = dfr.loc[pd.Timestamp(on)]
            ref = {"sd": float(rr["sd"]), "gs": float(rr["gap_share"]) if np.isfinite(rr["gap_share"]) else 0.2, "mu": 0.0, "pc": pc}
    u = ref["sd"] * pc
    b = tf_bars(m, o, h, l, cl, tf)
    r = rsi(b["c"]) if b is not None else np.array([])
    lv = md.daily[sym].copy()
    lv["date"] = pd.to_datetime(lv["date"]).dt.normalize()
    if on:
        lv = lv[lv["date"] <= pd.Timestamp(on)]
    levels = {"prev_close": pc, "prev_high": float(lv["high"].iloc[-2]), "prev_low": float(lv["low"].iloc[-2])}
    from ..options import chains
    if sym in chains.SUPPORTED and on and ((chains.DATA_DIR / f"{sym}_levels.csv").exists() or not md.settings.live):
        try:
            from ..options import levels as olv
            hist, _ = olv.history(md, sym, [pd.Timestamp(on).date()])
            if len(hist):
                levels.update({kk: float(hist.iloc[0][kk]) for kk in ("call_wall", "put_wall", "flip", "max_pain") if pd.notna(hist.iloc[0].get(kk))})
        except Exception:  # noqa: BLE001
            pass
    elif sym in chains.SUPPORTED:
        from ..options.levels import quick_levels
        q = quick_levels(md, sym)
        if q:
            levels.update({kk: q[kk] for kk in ("call_wall", "put_wall", "flip", "max_pain") if q.get(kk) is not None})
    wk = lv.set_index("date").resample("W-FRI").agg({"high": "max", "low": "min"}).iloc[:-1]
    if len(wk):
        levels["week_high"], levels["week_low"] = float(wk["high"].iloc[-1]), float(wk["low"].iloc[-1])
    signals = []
    if b is not None:
        Tn = T or 390.0
        for n in range(15, len(b["c"])):
            legs = [leg_z(b, q, ref["sd"], ref["gs"], Tn, tf, pc) for q in range(max(6, n - 30), n + 1)]
            for side in (1, -1):
                p = divergence(b, r, n, side)
                if p is None:
                    continue
                z = min(legs) if side > 0 else max(legs)
                ext = b["l"][n] if side > 0 else b["h"][n]
                lvl = near_level(ext, levels, 0.25 * u)
                conf = any((b["c"][q] - (b["h"][n] if side > 0 else b["l"][n])) * side > 0 for q in range(n + 1, min(len(b["c"]), n + 4)))
                signals.append({"bar": n, "prior": p, "side": "LONG" if side > 0 else "SHORT", "z": round(z, 2), "confirmed": conf,
                                "vol_ok": z * side <= -k, "level": LEVEL_NAMES.get(lvl[0]) if lvl else None,
                                "level_px": lvl[2] if lvl else None, "trigger": float(b["h"][n] if side > 0 else b["l"][n]),
                                "stop": float(ext - side * 0.1 * u), "time_min": float(b["m"][n])})
    rnd = lambda v: None if v is None else round(round(v / c.tick_size) * c.tick_size, 6)  # noqa: E731
    last = float(cl[-1])
    frac_now = ref["gs"] + (1 - ref["gs"]) * min(1.0, (m[-1] / (T or 390)))
    z_now = math.log(last / pc) / (ref["sd"] * math.sqrt(frac_now))
    tail = 60
    chart = None
    if b is not None:
        s0 = max(0, len(b["c"]) - tail)
        chart = {"o": [rnd(x) for x in b["o"][s0:]], "h": [rnd(x) for x in b["h"][s0:]], "l": [rnd(x) for x in b["l"][s0:]],
                 "c": [rnd(x) for x in b["c"][s0:]], "rsi": [None if not np.isfinite(x) else round(float(x), 1) for x in r[s0:]],
                 "offset": s0}
    full = [s for s in signals if s["vol_ok"] and s["level"]]
    for sg in signals:
        sg["grade"] = "A+ (all three)" if sg["vol_ok"] and sg["level"] else "B (two of three)" if (sg["vol_ok"] or sg["level"]) else "C (divergence only)"
    return {"symbol": sym, "label": label, "tf": tf, "k": k, "last": last, "prev_close": pc, "z_now": round(z_now, 2),
            "rsi_now": None if not len(r) or not np.isfinite(r[-1]) else round(float(r[-1]), 1),
            "sd_px": rnd(u), "levels": sorted([{"name": LEVEL_NAMES[kk], "key": kk, "price": rnd(v), "dist_sd": round((v - last) / u, 2)}
                                               for kk, v in levels.items() if v is not None], key=lambda x: -x["price"]),
            "divergences": [{**s, "trigger": rnd(s["trigger"]), "stop": rnd(s["stop"]), "level_px": rnd(s["level_px"])} for s in signals[-8:]],
            "full_setups": len(full), "chart": chart}
