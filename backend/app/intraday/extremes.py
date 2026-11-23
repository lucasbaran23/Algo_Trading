"""Sigma-extreme fade: trade against moves that are statistically extreme for this market, in this month.

How "extreme" is measured
  Every daily return is compared with the history of the SAME CALENDAR MONTH in earlier years (or the
  same month and weekday, or the trailing 60 sessions, as alternatives). Only data before the day is
  used. z = (return - mean) / std of that reference sample, and the percentile rank says where the day
  falls (P1 / P99 = 1-in-100 moves for that month).
  Intraday, the move from yesterday's close to "now" is scaled by the share of a day's variance that
  has normally happened by that time (overnight gap share + the elapsed part of the session).

Two versions are tested
  Swing (daily bars, ~10 years): after a close at <= -k sigma, buy (>= +k: sell).
  Intraday (1-minute bars): when the move from yesterday's close reaches k sigma for the time of day,
  fade it, flat by the close.

Confirmation (the "don't catch the falling knife" rule)
  none      enter straight away (baseline, for comparison)
  reversal  intraday: wait for a 5-minute bar to close back beyond the previous 5-minute bar's high
            (for a long) / low (for a short). Swing: wait for price to bounce 0.5 sigma off the
            lowest low (highest high) since the shock, within 3 sessions.
Stops go beyond the extreme; the target is half of the way back to yesterday's close (swing: to the
pre-shock close); otherwise the trade is closed at the session close (swing: after 5 sessions).

Everything is split chronologically 60/40 into in-sample / out-of-sample, like the research tab.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import universe
from ..data import intraday
from . import research as ir

REFS = {"month": "same calendar month, earlier years", "month_weekday": "same month and weekday, earlier years",
        "trailing": "trailing 60 sessions"}


# ---------------------------------------------------------------------- reference distributions
def daily_frame(md, sym: str, ref: str = "month") -> pd.DataFrame:
    d = md.daily[sym][["date", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"])
    d["ret"] = np.log(d["close"]).diff()
    d["gap"] = np.log(d["open"] / d["close"].shift(1))
    d["month"], d["wd"] = d["date"].dt.month, d["date"].dt.weekday
    if ref == "trailing":
        g = d["ret"].rolling(60, min_periods=40)
        d["mu"], d["sd"] = g.mean().shift(1), g.std().shift(1)
        d["p01"], d["p99"] = g.quantile(0.01).shift(1), g.quantile(0.99).shift(1)
        d["n_ref"] = d["ret"].rolling(60).count().shift(1)
    else:
        keys = ["month"] if ref == "month" else ["month", "wd"]
        mp = 40 if ref == "month" else 15
        grp = d.groupby(keys)["ret"]
        d["mu"] = grp.transform(lambda s: s.expanding(mp).mean().shift(1))
        d["sd"] = grp.transform(lambda s: s.expanding(mp).std().shift(1))
        d["p01"] = grp.transform(lambda s: s.expanding(mp).quantile(0.01).shift(1))
        d["p99"] = grp.transform(lambda s: s.expanding(mp).quantile(0.99).shift(1))
        d["n_ref"] = grp.transform(lambda s: s.expanding().count().shift(1))
    d["z"] = (d["ret"] - d["mu"]) / d["sd"]
    # share of daily variance that comes from the overnight gap (expanding, no look-ahead)
    d["gap_share"] = ((d["gap"] ** 2).expanding(60).mean() / (d["ret"] ** 2).expanding(60).mean()).shift(1).clip(0.02, 0.8)
    return d


def ref_stats(d: pd.DataFrame, month: int, weekday: int) -> dict:
    r = d["ret"].dropna()
    m = d[d["month"] == month]["ret"].dropna()
    mw = d[(d["month"] == month) & (d["wd"] == weekday)]["ret"].dropna()
    rest = d[d["month"] != month]["ret"].dropna()
    pct = lambda x: round(100 * (math.exp(x) - 1), 2)  # noqa: E731
    def blk(s):
        if len(s) < 10:
            return {"n": int(len(s))}
        sd = float(s.std())
        return {"n": int(len(s)), "sd_pct": pct(sd), "p01_pct": pct(float(s.quantile(0.01))), "p99_pct": pct(float(s.quantile(0.99))),
                "p05_pct": pct(float(s.quantile(0.05))), "p95_pct": pct(float(s.quantile(0.95))),
                "n_2sd": int((s.abs() > 2 * sd).sum()), "n_3sd": int((s.abs() > 3 * sd).sum()),
                "n_3sd_down": int((s < -3 * sd).sum()), "n_3sd_up": int((s > 3 * sd).sum()),
                "normal_3sd_expected": round(len(s) * 0.0027, 1)}
    return {"month": blk(m), "month_weekday": blk(mw), "all": blk(r), "other_months": blk(rest)}


# ---------------------------------------------------------------------- event study
def event_study(d: pd.DataFrame, k: float = 2.5, horizon: int = 10) -> dict:
    c = d["close"].to_numpy()
    z = d["z"].to_numpy()
    sd = d["sd"].to_numpy()
    out = {}
    for lab, mask in (("down", z <= -k), ("up", z >= k)):
        idx = [i for i in np.where(mask)[0] if i + horizon < len(c)]
        if not idx:
            out[lab] = {"n": 0}
            continue
        paths = np.array([[math.log(c[i + h] / c[i]) / sd[i] for h in range(1, horizon + 1)] for i in idx])
        mean = paths.mean(axis=0)
        se = paths.std(axis=0, ddof=1) / math.sqrt(len(idx)) if len(idx) > 1 else np.zeros(horizon)
        rev5 = float((np.sign(paths[:, 4]) == (1 if lab == "down" else -1)).mean()) if horizon >= 5 else None
        out[lab] = {"n": len(idx), "path_sd": [round(float(x), 3) for x in mean],
                    "t": [round(float(m / s), 2) if s > 0 else 0.0 for m, s in zip(mean, se)],
                    "reverted_5d_pct": round(100 * rev5, 0) if rev5 is not None else None,
                    "dates": [str(d["date"].iloc[i].date()) for i in idx[-8:]]}
    return out


# ---------------------------------------------------------------------- swing backtest (daily bars)
def _swing_trades(d: pd.DataFrame, k: float, confirm: str, tick: float, hold: int = 5) -> list[tuple]:
    o, h, l, c = (d[x].to_numpy() for x in ("open", "high", "low", "close"))
    z, sd = d["z"].to_numpy(), d["sd"].to_numpy()
    n, i, out = len(c), 1, []
    while i < n - 2:
        if not np.isfinite(z[i]) or abs(z[i]) < k:
            i += 1
            continue
        side = -1 if z[i] > 0 else 1                     # fade
        u = sd[i] * c[i]                                 # one daily sigma in price
        pre = c[i - 1]
        ext = l[i] if side > 0 else h[i]
        entry = j0 = None
        stopped_on_entry_day = False
        if confirm == "none":
            j0, entry = i + 1, o[i + 1]
            # stop is known before the open; within the entry day assume the adverse extreme could come first
            stopped_on_entry_day = (l[j0] if side > 0 else h[j0]) * side <= (ext - side * 0.25 * u) * side
        else:
            # Daily bars only give OHLC, so the order inside a day follows the path model used elsewhere:
            # open -> the nearer extreme -> the other extreme -> close.
            better = lambda a, b: (a - b) * side            # > 0: a is further in the trade's favour than b  # noqa: E731
            for j in range(i + 1, min(n, i + 4)):
                adv, fav = (l[j], h[j]) if side > 0 else (h[j], l[j])
                trig = ext + side * 0.5 * u
                if better(o[j], trig) >= 0:                    # gapped through the trigger: fill at the open
                    j0, entry = j, o[j]
                    stopped_on_entry_day = better(adv, ext - side * 0.25 * u) <= 0
                    break
                if abs(o[j] - adv) <= abs(o[j] - fav):         # adverse extreme first: new extreme, then the bounce
                    if better(adv, ext) < 0:
                        ext = adv
                    trig = ext + side * 0.5 * u
                    if better(fav, trig) >= 0:
                        j0, entry = j, trig
                        break
                else:                                          # bounce first, then the adverse extreme
                    if better(fav, trig) >= 0:
                        j0, entry = j, trig
                        stopped_on_entry_day = better(adv, ext - side * 0.25 * u) <= 0
                        break
                    if better(adv, ext) < 0:
                        ext = adv
        if entry is None:
            i += 4
            continue
        stop = ext - side * 0.25 * u
        tgt = ext + side * 0.5 * abs(pre - ext)
        if (tgt - entry) * side < 0.25 * u:              # already reverted: nothing left to capture
            i = j0 + 1
            continue
        exit_px, j = None, j0
        if stopped_on_entry_day:
            exit_px = stop
        else:
            for j in range(j0, min(n, j0 + hold)):
                if j > j0 and ((side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop)):
                    exit_px = min(stop, o[j]) if side > 0 else max(stop, o[j])
                    break
                if j > j0 and ((side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt)):
                    exit_px = max(tgt, o[j]) if side > 0 else min(tgt, o[j])
                    break
                if j == j0 and (c[j] - tgt) * side >= 0:
                    exit_px = tgt
                    break
        if exit_px is None:
            exit_px = c[j]
        out.append((i, side, entry, exit_px, str(d["date"].iloc[i].date()), round(float(z[i]), 2)))
        i = j + 1
    return out


# ---------------------------------------------------------------------- intraday backtest (1-minute bars)
def _bars5(s):
    m, h, l, c = s["m"], s["h"], s["l"], s["c"]
    edges = np.searchsorted(m, np.arange(5, int(m[-1]) + 5, 5), side="right")
    out, a = [], 0
    for b in edges:
        if b > a:
            out.append((b - 1, float(h[a:b].max()), float(l[a:b].min()), float(c[b - 1])))
        a = b
    return out                                           # (last 1-min index, high, low, close)


def _intraday_trade(s, sd_day, gap_share, k, confirm):
    pc = s["prev_close"]
    if pc is None or not np.isfinite(sd_day) or sd_day <= 0:
        return None
    m, h, l, c = s["m"], s["h"], s["l"], s["c"]
    T = float(m[-1])
    u = sd_day * pc
    b5 = _bars5(s)
    armed, ext, k_entry = 0, None, None
    for n, (i, bh, bl, bc) in enumerate(b5):
        if m[i] < 15 or m[i] > T - 45:
            if armed == 0:
                continue
            if m[i] > T - 45:
                return None
        frac = gap_share + (1 - gap_share) * m[i] / T
        z = math.log(bc / pc) / (sd_day * math.sqrt(frac))
        if not armed and abs(z) >= k:
            armed = -1 if z > 0 else 1
            ext = bl if armed > 0 else bh
            if confirm == "none":
                k_entry = n
                break
            continue
        if armed:
            ext = min(ext, bl) if armed > 0 else max(ext, bh)
            ph, pl = b5[n - 1][1], b5[n - 1][2]
            if (armed > 0 and bc > ph) or (armed < 0 and bc < pl):
                k_entry = n
                break
    if k_entry is None:
        return None
    side = armed
    i0, _, _, entry = b5[k_entry]
    stop = ext - side * 0.1 * u
    tgt = ext + side * 0.5 * abs(pc - ext)
    if (tgt - entry) * side <= 0.3 * abs(entry - stop):
        return None
    for j in range(i0 + 1, len(c)):
        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
            return side, entry, stop
        if (side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt):
            return side, entry, tgt
    return side, entry, c[-1]


def _stats(pnl: np.ndarray, tv: float) -> dict:
    return ir._stats(pnl, tv)


def backtest(md, symbol: str, ref: str = "month", commission: float = 2.5, slip_ticks: float = 1.0) -> dict:
    c = universe.get(symbol)
    sym = c.data_symbol
    pv, tv, tick = c.point_value, c.tick_value, c.tick_size
    rt = 2 * (commission + slip_ticks * tv)
    d = daily_frame(md, sym, ref)
    # ---- swing grid
    swing_rows, swing_best = [], None
    n_days = len(d)
    split_i = int(n_days * 0.6)
    for k in (2.0, 2.5, 3.0):
        for conf in ("none", "reversal"):
            tr = _swing_trades(d, k, conf, tick)
            idx = np.array([t[0] for t in tr], int)
            pnl = np.array([(t[3] - t[2]) * t[1] * pv - rt for t in tr])
            side = np.array([t[1] for t in tr])
            row = {"k": k, "confirm": conf, "is": _stats(pnl[idx < split_i], tv), "oos": _stats(pnl[idx >= split_i], tv),
                   "longs": _stats(pnl[side > 0], tv), "shorts": _stats(pnl[side < 0], tv), "all": _stats(pnl, tv)}
            swing_rows.append(row)
            sc = ir._score(pnl[idx < split_i], 6)
            if swing_best is None or sc > swing_best[0]:
                swing_best = (sc, row, tr, pnl)
    _, srow, strades, spnl = swing_best
    cum = np.cumsum(spnl) if len(spnl) else np.array([])
    swing_eq = [{"time": t[4], "value": round(float(v), 0)} for t, v in zip(strades, cum)]
    swing_recent = [{"date": t[4], "z": t[5], "side": "LONG" if t[1] > 0 else "SHORT", "entry": round(t[2], 6), "exit": round(t[3], 6),
                     "pnl": round(float(p), 0)} for t, p in list(zip(strades, spnl))[-10:]][::-1]

    # ---- intraday grid
    df, src = intraday.bars(md, sym)
    sessions = ir.prepare_sessions(df, sym)
    dd = d.set_index(d["date"].dt.normalize())
    S = len(sessions)
    split = int(S * 0.6)
    intra_rows, intra_best = [], None
    for k in (2.0, 2.5, 3.0):
        for conf in ("none", "reversal"):
            idx, pnl, side = [], [], []
            for si, s in enumerate(sessions):
                ts = pd.Timestamp(s["date"])
                if ts not in dd.index:
                    continue
                row_d = dd.loc[ts]
                t = _intraday_trade(s, float(row_d["sd"]), float(row_d["gap_share"]) if np.isfinite(row_d["gap_share"]) else 0.2, k, conf)
                if t:
                    idx.append(si); side.append(t[0]); pnl.append((t[2] - t[1]) * t[0] * pv - rt)
            idx_a, pnl_a, side_a = np.array(idx, int), np.array(pnl), np.array(side)
            row = {"k": k, "confirm": conf, "is": _stats(pnl_a[idx_a < split], tv), "oos": _stats(pnl_a[idx_a >= split], tv),
                   "longs": _stats(pnl_a[side_a > 0], tv), "shorts": _stats(pnl_a[side_a < 0], tv), "all": _stats(pnl_a, tv)}
            intra_rows.append(row)
            sc = ir._score(pnl_a[idx_a < split], 5)
            if intra_best is None or sc > intra_best[0]:
                intra_best = (sc, row, idx_a, pnl_a)
    _, irow, iidx, ipnl = intra_best
    icum = np.cumsum(ipnl) if len(ipnl) else np.array([])
    intra_eq = [{"time": str(sessions[i]["date"]), "value": round(float(v), 0)} for i, v in zip(iidx, icum)]
    return {
        "symbol": sym, "ref": ref, "ref_label": REFS[ref], "rt_cost": round(rt, 2), "source": src,
        "swing": {"grid": swing_rows, "chosen": {"k": srow["k"], "confirm": srow["confirm"]}, "oos": srow["oos"],
                  "equity": swing_eq, "recent": swing_recent, "from": str(d["date"].iloc[0].date()),
                  "split": str(d["date"].iloc[split_i].date())},
        "intraday": {"grid": intra_rows, "chosen": {"k": irow["k"], "confirm": irow["confirm"]}, "oos": irow["oos"],
                     "equity": intra_eq, "sessions": S, "split": str(sessions[split]["date"]) if S > split else None},
    }


# ---------------------------------------------------------------------- today
def today(md, symbol: str, ref: str = "month", k: float = 2.5) -> dict:
    """Where today's move sits vs the month's history, and the price levels for ±2/2.5/3 sigma."""
    from datetime import datetime
    from ..daytrade import calendar as cal
    from ..tradesignals.setups import ET
    c = universe.get(symbol)
    sym = c.data_symbol
    d = daily_frame(md, sym, ref)
    ses = md.sessions[sym]
    pc = float(d["close"].iloc[-2])
    row = d.iloc[-1]
    sd = float(row["sd"]) if np.isfinite(row["sd"]) else float(d["ret"].tail(60).std())
    gs = float(row["gap_share"]) if np.isfinite(row["gap_share"]) else 0.2
    now = datetime.now(ET)
    o_s, c_s = (ir._hm(x) for x in cal.session_window(c.sector, sym))
    mins = now.hour * 60 + now.minute
    frac_t = min(1.0, max(0.0, (mins - o_s) / (c_s - o_s))) if now.weekday() < 5 else 1.0
    frac = gs + (1 - gs) * frac_t
    last = float(ses.last)
    move = math.log(last / pc)
    z_now = (move - (float(row["mu"]) if np.isfinite(row["mu"]) else 0)) / (sd * math.sqrt(frac))
    rnd = lambda v: round(round(v / c.tick_size) * c.tick_size, 6)  # noqa: E731
    bands = [{"k": kk, "now_up": rnd(pc * math.exp(kk * sd * math.sqrt(frac))), "now_down": rnd(pc * math.exp(-kk * sd * math.sqrt(frac))),
              "close_up": rnd(pc * math.exp(kk * sd)), "close_down": rnd(pc * math.exp(-kk * sd))} for kk in (2.0, 2.5, 3.0)]
    pct_rank = None
    hist = d[(d["month"] == now.month)]["ret"].dropna()
    if len(hist) > 20:
        pct_rank = round(100 * float((hist < move).mean()), 1)
    state = "ARMED LONG: wait for a 5-min close above the prior bar's high" if z_now <= -k else \
            "ARMED SHORT: wait for a 5-min close below the prior bar's low" if z_now >= k else \
            "NORMAL: inside the month's usual range"
    return {"symbol": sym, "ref": ref, "ref_label": REFS[ref], "prev_close": pc, "last": last, "move_pct": round(100 * (math.exp(move) - 1), 2),
            "z_now": round(z_now, 2), "pct_rank_month": pct_rank, "sd_day_pct": round(100 * sd, 2), "session_frac": round(frac_t, 2),
            "var_frac": round(frac, 2), "gap_share": round(gs, 2), "bands": bands, "k": k, "state": state,
            "armed": -1 if z_now >= k else 1 if z_now <= -k else 0,
            "ref_stats": ref_stats(d, now.month, now.weekday()), "events": event_study(d, k)}


_LIVE: dict = {}


def live_ref(md, sym: str, ref: str = "month") -> dict | None:
    """Reference sigma for today's session (cached per day): sd, mu, gap share, prev close."""
    d0 = md.daily[sym]
    key = (sym, ref, str(d0["date"].iloc[-1]))
    hit = _LIVE.get(key)
    if hit is not None:
        return hit
    try:
        d = daily_frame(md, sym, ref)
        row = d.iloc[-1]
        sd = float(row["sd"]) if np.isfinite(row["sd"]) else float(d["ret"].tail(60).std())
        out = {"sd": sd, "mu": float(row["mu"]) if np.isfinite(row["mu"]) else 0.0,
               "gs": float(row["gap_share"]) if np.isfinite(row["gap_share"]) else 0.2, "pc": float(d["close"].iloc[-2])}
    except Exception:  # noqa: BLE001
        out = None
    _LIVE[key] = out
    return out


def live_z(ref: dict, last: float, frac_t: float) -> float:
    frac = ref["gs"] + (1 - ref["gs"]) * min(1.0, max(0.0, frac_t))
    return (math.log(last / ref["pc"]) - ref["mu"]) / (ref["sd"] * math.sqrt(frac))
