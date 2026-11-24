"""Intraday strategy research: timeframe selection with out-of-sample validation.

For each strategy x timeframe x parameter set:
  1. Run it on every regular-trading-hours session of 1-minute bars (per 1 contract, after costs).
  2. Split the sessions chronologically: the first 60% is "in-sample", where the timeframe and
     parameters are chosen, and the last 40% is "out-of-sample" (OOS), never used for choosing.
     The OOS result of the in-sample winner is the honest estimate.
  3. Rolling walk-forward: re-choose on the trailing 120 sessions, trade the next 25, and repeat.
     The stitched OOS trades give the equity curve.
  4. Cost sensitivity: gross vs net per trade, and the round-turn cost (in ticks) at which the
     edge disappears.
With many configurations tested, the best in-sample result is always flattering: trust the
OOS / walk-forward numbers and their t-stats, not the in-sample ones.
"""
from __future__ import annotations

import itertools
import math
import time
from datetime import date
from statistics import NormalDist

import numpy as np
import pandas as pd

from .. import universe
from ..daytrade import calendar as cal
from ..data import intraday
from .strategies import STRATEGIES

FOMC = cal.FOMC_2026 | {date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
                        date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10)}
_CACHE: dict[str, tuple[float, dict]] = {}


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def prepare_sessions(df: pd.DataFrame, symbol: str, lookback_sigma: int = 14) -> list[dict]:
    """Slice 1-minute bars into RTH sessions with the arrays strategies need."""
    c = universe.get(symbol)
    o_s, c_s = (_hm(x) for x in cal.session_window(c.sector, c.data_symbol))
    mins = df.index.hour * 60 + df.index.minute
    rth = df[(mins > o_s) & (mins <= c_s)]
    sessions = []
    prev_close = None
    abs_moves: list[np.ndarray] = []
    n_rth = c_s - o_s
    for day, g in rth.groupby(rth.index.normalize()):
        res = int(g["res"].median()) if "res" in g else 1        # bar size: 1-minute, or 5-minute for older live days
        if len(g) < (n_rth / max(res, 1)) * 0.6:
            continue
        m = (g.index.hour * 60 + g.index.minute - o_s).to_numpy()
        o, h, l, cl, v = (g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        tp = (h + l + cl) / 3
        vw = np.cumsum(tp * np.maximum(v, 1)) / np.cumsum(np.maximum(v, 1))
        # noise-area sigma: mean |close/open - 1| at each minute over the prior N sessions
        move = np.full(n_rth, np.nan)
        move[np.clip(m - 1, 0, n_rth - 1)] = np.abs(cl / o[0] - 1)
        move = pd.Series(move).ffill().bfill().to_numpy()
        sigma = np.nanmean(np.vstack(abs_moves[-lookback_sigma:]), axis=0) if len(abs_moves) >= lookback_sigma else None
        sig_today = sigma[np.clip(m - 1, 0, n_rth - 1)] if sigma is not None else None
        sessions.append({"date": day.date(), "weekday": day.weekday(), "m": m, "o": o, "h": h, "l": l, "c": cl, "v": v,
                         "vwap": vw, "prev_close": prev_close, "sigma": sig_today, "fomc": day.date() in FOMC,
                         "open_min": o_s})
        abs_moves.append(move)
        prev_close = cl[-1]
    return sessions


def _stats(pnl: np.ndarray, tick_value: float) -> dict:
    n = len(pnl)
    if n == 0:
        return {"n": 0}
    wins = pnl[pnl > 0]
    loss = pnl[pnl <= 0]
    sd = pnl.std(ddof=1) if n > 1 else 0.0
    return {"n": int(n), "avg": round(float(pnl.mean()), 2), "avg_ticks": round(float(pnl.mean() / tick_value), 2),
            "win_pct": round(100 * len(wins) / n, 1), "total": round(float(pnl.sum()), 0),
            "pf": round(float(wins.sum() / -loss.sum()), 2) if loss.sum() < 0 else (99.0 if len(wins) else 0.0),
            "t_stat": round(float(pnl.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0}


def _run(strat, sessions, tf, params, pv, rt_cost):
    """Return per-trade arrays: session index, net pnl, gross pnl."""
    idx, net, gross = [], [], []
    for i, d in enumerate(sessions):
        for side, e, x in strat.fn(d, tf, **params):
            g = (x - e) * side * pv
            idx.append(i)
            gross.append(g)
            net.append(g - rt_cost)
    return np.array(idx, int), np.array(net), np.array(gross)


def _score(net: np.ndarray, min_n: int = 8) -> float:
    if len(net) < min_n:
        return -1e9
    sd = net.std(ddof=1)
    return float(net.mean() / (sd / math.sqrt(len(net)))) if sd > 0 else 0.0


def research(md, symbol: str, commission: float = 2.5, slip_ticks: float = 1.0, train: int = 120, test: int = 25) -> dict:
    c = universe.get(symbol)
    sym = c.data_symbol
    df, src = intraday.bars(md, sym)
    key = f"{symbol}:{src}:{len(df)}:{commission}:{slip_ticks}"
    hit = _CACHE.get(symbol)
    if hit and hit[1].get("_key") == key and time.time() - hit[0] < 1800:
        return hit[1]
    sessions = prepare_sessions(df, sym)
    S = len(sessions)
    split = int(S * 0.6)
    pv, tv = c.point_value, c.tick_value
    rt_cost = 2 * (commission + slip_ticks * tv)
    results = []
    t_crit = 2.0
    applicable = [s for s in STRATEGIES if s.applies(sym, c.sector)]
    # Multiple-testing guard: picking the best of K strategies inflates t-stats. Bonferroni-style
    # critical value for a two-sided 5% test across K strategies (≈2.7 for K = 7).
    t_crit = NormalDist().inv_cdf(1 - 0.05 / (2 * max(1, len(applicable))))
    for strat in applicable:
        combos = []
        pnames = list(strat.params)
        for tf in strat.tf_values:
            for vals in itertools.product(*[strat.params[p] for p in pnames]) if pnames else [()]:
                params = dict(zip(pnames, vals))
                idx, net, gross = _run(strat, sessions, tf, params, pv, rt_cost)
                combos.append({"tf": tf, "params": params, "idx": idx, "net": net, "gross": gross})
        # ---- timeframe table: per tf, the in-sample-best params, with IS and OOS stats
        tf_rows = []
        for tf in strat.tf_values:
            cands = [cb for cb in combos if cb["tf"] == tf]
            best = max(cands, key=lambda cb: _score(cb["net"][cb["idx"] < split]))
            is_m, oos_m = best["idx"] < split, best["idx"] >= split
            tf_rows.append({"tf": tf, "params": best["params"],
                            "is": _stats(best["net"][is_m], tv), "oos": _stats(best["net"][oos_m], tv),
                            "oos_gross_ticks": round(float(best["gross"][oos_m].mean() / tv), 2) if oos_m.any() else None})
        chosen = max(combos, key=lambda cb: _score(cb["net"][cb["idx"] < split]))
        oos_m = chosen["idx"] >= split
        oos = _stats(chosen["net"][oos_m], tv)
        gross_ticks = float(chosen["gross"][oos_m].mean() / tv) if oos_m.any() else 0.0
        # ---- rolling walk-forward
        wf_idx, wf_net, picks = [], [], []
        start = train
        while start < S:
            end = min(S, start + test)
            pick = max(combos, key=lambda cb: _score(cb["net"][(cb["idx"] >= start - train) & (cb["idx"] < start)], 5))
            msk = (pick["idx"] >= start) & (pick["idx"] < end)
            wf_idx += list(pick["idx"][msk])
            wf_net += list(pick["net"][msk])
            picks.append(pick["tf"])
            start = end
        wf_net_a = np.array(wf_net)
        wf = _stats(wf_net_a, tv)
        # daily equity of the walk-forward
        eq, cum = [], 0.0
        by_sess = pd.Series(wf_net_a, index=wf_idx).groupby(level=0).sum() if len(wf_net) else pd.Series(dtype=float)
        for i in range(train, S):
            cum += float(by_sess.get(i, 0.0))
            eq.append({"time": str(sessions[i]["date"]), "value": round(cum, 2)})
        daily = np.array([by_sess.get(i, 0.0) for i in range(train, S)]) if S > train else np.array([])
        sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if len(daily) > 5 and daily.std() > 0 else 0.0
        ot, wt = oos.get("t_stat", 0) or 0, wf.get("t_stat", 0) or 0
        verdict = ("EDGE" if oos.get("n", 0) >= 30 and ot >= t_crit and wt >= 2 else
                   "PROMISING" if oos.get("n", 0) >= 15 and ot >= 1.5 and wt >= 1 else
                   "NO EDGE" if (oos.get("avg", 0) or 0) > -rt_cost / 2 else "NEGATIVE")
        tf_mode = max(set(picks), key=picks.count) if picks else chosen["tf"]
        results.append({
            "key": strat.key, "name": strat.name, "grade": strat.grade, "family": strat.family,
            "tf_label": strat.tf_label, "finding": strat.finding, "caveats": strat.caveats, "how_to": strat.how_to,
            "citations": strat.citations,
            "best_tf": chosen["tf"], "best_params": chosen["params"], "wf_tf_most_chosen": tf_mode,
            "is": _stats(chosen["net"][chosen["idx"] < split], tv), "oos": oos, "wf": {**wf, "sharpe": round(sharpe, 2)},
            "gross_ticks": round(gross_ticks, 2), "breakeven_rt_ticks": round(gross_ticks, 2),
            "rt_cost_ticks": round(rt_cost / tv, 2),
            "tf_table": tf_rows, "equity": eq, "verdict": verdict,
            "configs_tested": len(combos),
        })
    order = {"EDGE": 0, "PROMISING": 1, "NO EDGE": 2, "NEGATIVE": 3}
    results.sort(key=lambda r: (order[r["verdict"]], -(r["oos"].get("t_stat", -99) or -99)))
    out = {
        "_key": key, "symbol": symbol, "data_symbol": sym, "source": src, "sessions": S,
        "from": str(sessions[0]["date"]) if sessions else None, "to": str(sessions[-1]["date"]) if sessions else None,
        "split_date": str(sessions[split]["date"]) if S > split else None,
        "rt_cost": round(rt_cost, 2), "rt_cost_ticks": round(rt_cost / tv, 2), "tick_value": tv,
        "rth": cal.session_window(c.sector, sym), "strategies": results, "t_crit": round(t_crit, 2),
        "note": ("SIMULATED intraday data: steps are independent by construction, so no strategy can have a real "
                 "edge here. These numbers show the method and the cost drag. Load real 1-minute bars to measure real edges.")
        if src == "SIM" else ("Real delayed bars from Yahoo: 1-minute for the last 30 days, 5-minute before that. "
                              "The store grows each day the terminal runs; for years of history import a CSV." if src.startswith("YAHOO")
                              else "Real intraday data loaded from CSV."),
    }
    _CACHE[symbol] = (time.time(), out)
    return out
