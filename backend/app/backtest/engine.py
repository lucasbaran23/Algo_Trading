"""Backtest engine, optimiser, walk-forward analysis and Monte Carlo.

Execution model (daily bars, futures, constant capital):
* Signals are decided at the close of bar i and executed at the OPEN of bar i+1.
* Every contract traded pays commission + slippage (ticks x tick value) per side.
* Optional ATR stop-loss / take-profit, checked intrabar against the high/low; a gap
  through the level fills at the open (worse) price. After a stop the strategy stays
  flat until its signal changes direction (no instant re-entry).
* Position size is fixed at entry: N contracts, or risk-based (account x risk% / (ATR x $/pt)).
* COT data becomes usable on its Friday release (Tuesday + 3 days); EIA inventories on
  the following Wednesday (week-ending Friday + 5 days) - no look-ahead on fundamentals.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import universe
from ..prop.rules import PropRules
from .strategies import STRATEGIES, Context, Strategy

MAX_COMBOS = 600
_INV_Z_CACHE: dict = {}
TRADING_DAYS = 252


# ---------------------------------------------------------------------- config
@dataclass
class RunConfig:
    symbol: str = "CL"
    strategy: str = "sma_cross"
    params: dict = field(default_factory=dict)
    sizing: str = "fixed"            # fixed | risk
    contracts: int = 1
    risk_pct: float = 0.5            # % of account risked per 1 ATR (risk sizing)
    max_contracts: int = 10
    account_size: float = 50_000
    commission: float = 2.5          # per contract per side
    slippage_ticks: float = 1.0      # per contract per side
    stop_atr: float = 0.0            # 0 = off
    target_atr: float = 0.0          # 0 = off
    start: str | None = None
    end: str | None = None


# ---------------------------------------------------------------------- data
def build_context(md, symbol: str, start: str | None = None, end: str | None = None) -> Context:
    c = universe.get(symbol)
    sym = c.data_symbol
    df = md.daily[sym][["date", "open", "high", "low", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    dates = pd.DatetimeIndex(df["date"])

    cot_net = None
    cot = md.cot.get(sym)
    if cot is not None and len(cot):
        s = cot.set_index(pd.to_datetime(cot["date"]) + pd.Timedelta(days=3))["mm_net"].astype(float)
        cot_net = s.reindex(dates.union(s.index)).ffill().reindex(dates).reset_index(drop=True)

    inv_z = None
    inv = md.inventory.get(sym)
    if inv is not None and len(inv):
        key = (sym, id(inv), len(inv))
        if key not in _INV_Z_CACHE:  # O(n^2) over weekly rows: compute once per dataset
            _INV_Z_CACHE[key] = _inventory_z(inv)
        inv_z = _INV_Z_CACHE[key].copy()
        inv_z.index = pd.to_datetime(inv["date"]).values + pd.Timedelta(days=5)
        inv_z = inv_z.reindex(dates.union(inv_z.index)).ffill().reindex(dates).reset_index(drop=True)

    mask = np.ones(len(df), bool)
    if start:
        mask &= df["date"].to_numpy() >= np.datetime64(start)
    if end:
        mask &= df["date"].to_numpy() <= np.datetime64(end)
    # keep a warm-up window before `start` so indicators are primed
    first = int(np.argmax(mask)) if mask.any() else 0
    keep = np.zeros(len(df), bool)
    keep[max(0, first - 300):] = True
    keep &= (df["date"].to_numpy() <= np.datetime64(end)) if end else True
    df, idx = df[keep].reset_index(drop=True), np.flatnonzero(keep)
    ctx = Context(df,
                  cot_net.iloc[idx].reset_index(drop=True) if cot_net is not None else None,
                  inv_z.iloc[idx].reset_index(drop=True) if inv_z is not None else None)
    ctx.eval_start = max(0, first - int(idx[0])) if len(idx) else 0  # type: ignore[attr-defined]
    return ctx


def _inventory_z(inv: pd.DataFrame, years: int = 5) -> pd.Series:
    """Rolling deviation of stocks vs the trailing 5y same-week average, as a z-score."""
    df = inv.reset_index(drop=True)
    devs = np.full(len(df), np.nan)
    for i, r in enumerate(df.itertuples()):
        prior = df.iloc[:i]
        prior = prior[(prior.date < r.date - pd.Timedelta(days=180)) & (prior.date >= r.date - pd.DateOffset(years=years))]
        wk = prior[(prior.week - r.week).abs() <= 1]
        if len(wk) >= 2:
            devs[i] = (r.stocks - wk.stocks.mean()) / wk.stocks.mean()
    s = pd.Series(devs)
    sd = s.rolling(156, min_periods=26).std()
    return (s / sd).astype(float)


def atr_series(df: pd.DataFrame, n: int = 20) -> np.ndarray:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=5).mean().bfill().to_numpy(float)


# ---------------------------------------------------------------------- core simulation
def simulate(ctx: Context, signal: np.ndarray, cfg: RunConfig) -> dict:
    c = universe.get(cfg.symbol)
    pv = c.point_value
    df = ctx.df
    o, h, l, cl = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(df)
    atr = atr_series(df)
    cost_side = cfg.commission + cfg.slippage_ticks * c.tick_value
    start = getattr(ctx, "eval_start", 0)

    pnl = np.zeros(n)                # daily P&L in $
    position = np.zeros(n)           # contracts held at close
    trades: list[dict] = []
    pos, entry_px, entry_i, stop, target, blocked = 0, 0.0, 0, None, None, 0
    trade_cost = 0.0

    def close_trade(i, px, reason):
        nonlocal pos, trade_cost
        gross = (px - entry_px) * pos * pv
        cost = abs(pos) * cost_side
        net = gross - trade_cost - cost
        trades.append({
            "entry_date": str(df.date.iloc[entry_i].date()), "exit_date": str(df.date.iloc[i].date()),
            "side": "LONG" if pos > 0 else "SHORT", "qty": abs(pos),
            "entry": round(float(entry_px), 6), "exit": round(float(px), 6), "pnl": round(float(net), 2),
            "bars": int(i - entry_i), "reason": reason,
        })
        pos, trade_cost = 0, 0.0
        return cost

    for i in range(max(1, start), n):
        day = 0.0
        want = int(signal[i - 1])
        if blocked and want != blocked:
            blocked = 0
        if blocked:
            want = 0
        # 1) overnight gap on the existing position
        day += pos * (o[i] - cl[i - 1]) * pv
        mark = o[i]
        # 2) execute signal change at the open
        cur_dir = int(np.sign(pos))
        if want != cur_dir:
            if pos:
                day -= close_trade(i, o[i], "signal")  # slippage is charged in cost_side
            if want:
                if cfg.sizing == "risk":
                    qty = int(cfg.account_size * cfg.risk_pct / 100 // max(atr[i - 1] * pv, 1e-9))
                else:
                    qty = cfg.contracts
                qty = min(qty, cfg.max_contracts)
                if qty > 0:
                    pos, entry_px, entry_i = want * qty, o[i], i
                    trade_cost = qty * cost_side
                    day -= trade_cost
                    rt = lambda x: round(round(x / c.tick_size) * c.tick_size, 6)  # noqa: E731
                    stop = rt(entry_px - want * cfg.stop_atr * atr[i - 1]) if cfg.stop_atr > 0 else None
                    target = rt(entry_px + want * cfg.target_atr * atr[i - 1]) if cfg.target_atr > 0 else None
        # 3) intrabar stop / target (stop assumed first if both touched: conservative)
        if pos:
            d = np.sign(pos)
            hit_stop = stop is not None and ((d > 0 and l[i] <= stop) or (d < 0 and h[i] >= stop))
            hit_tgt = target is not None and ((d > 0 and h[i] >= target) or (d < 0 and l[i] <= target))
            if hit_stop or hit_tgt:
                # a gap through the level fills at the open: worse for stops, better for targets
                if hit_stop:
                    px = min(o[i], stop) if d > 0 else max(o[i], stop)
                else:
                    px = max(o[i], target) if d > 0 else min(o[i], target)
                day += pos * (px - mark) * pv
                mark = px
                blocked = int(d)
                day -= close_trade(i, px, "stop" if hit_stop else "target")
        # 4) mark to close
        day += pos * (cl[i] - mark) * pv
        pnl[i] = day
        position[i] = pos
    if pos:
        day_cost = close_trade(n - 1, cl[-1], "end")
        pnl[-1] -= day_cost

    dates = df["date"].iloc[start:].reset_index(drop=True)
    pnl, position = pnl[start:], position[start:]
    equity = cfg.account_size + np.cumsum(pnl)
    bench = (cl[start:] - cl[start]) * pv * max(1, cfg.contracts if cfg.sizing == "fixed" else 1)
    return {"dates": dates, "pnl": pnl, "equity": equity, "position": position, "trades": trades,
            "benchmark": cfg.account_size + bench}


# ---------------------------------------------------------------------- metrics
def metrics(pnl: np.ndarray, account: float, trades: list[dict] | None = None, position=None) -> dict:
    pnl = np.asarray(pnl, float)
    n = len(pnl)
    if n == 0:
        return {}
    eq = account + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.r_[account, eq])[1:]
    dd = eq - peak
    r = pnl / account
    sd = r.std()
    down = r[r < 0].std() if (r < 0).sum() > 1 else 0
    years = n / TRADING_DAYS
    net = float(pnl.sum())
    ann = net / account / years if years > 0 else 0.0
    max_dd = float(-dd.min()) if n else 0.0
    out = {
        "net_pnl": round(net, 2),
        "return_pct": round(100 * net / account, 2),
        "cagr_pct": round(100 * ((max(eq[-1], 1) / account) ** (1 / years) - 1), 2) if years > 0.1 else None,
        "ann_return_pct": round(100 * ann, 2),
        "ann_vol_pct": round(100 * sd * math.sqrt(TRADING_DAYS), 2),
        "sharpe": round(r.mean() / sd * math.sqrt(TRADING_DAYS), 2) if sd > 0 else 0.0,
        "sortino": round(r.mean() / down * math.sqrt(TRADING_DAYS), 2) if down > 0 else 0.0,
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(100 * float((-dd / peak).max()), 2) if n else 0.0,
        "calmar": round(ann * account / max_dd, 2) if max_dd > 0 else 0.0,
        "worst_day": round(float(pnl.min()), 2),
        "best_day": round(float(pnl.max()), 2),
        "days": n,
    }
    if position is not None:
        out["exposure_pct"] = round(100 * float((np.asarray(position) != 0).mean()), 1)
    if trades is not None:
        tp = np.array([t["pnl"] for t in trades], float)
        wins, losses = tp[tp > 0], tp[tp <= 0]
        out.update({
            "trades": len(tp),
            "win_rate_pct": round(100 * len(wins) / len(tp), 1) if len(tp) else 0.0,
            "profit_factor": round(wins.sum() / -losses.sum(), 2) if losses.sum() < 0 else (99.0 if len(wins) else 0.0),
            "avg_trade": round(float(tp.mean()), 2) if len(tp) else 0.0,
            "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
            "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
            "avg_bars": round(float(np.mean([t["bars"] for t in trades])), 1) if trades else 0.0,
        })
    return out


def score(m: dict, objective: str) -> float:
    if not m:
        return -1e18
    if objective == "net_pnl":
        return m["net_pnl"]
    if objective == "calmar":
        return m["calmar"]
    if objective == "profit_factor":
        return m.get("profit_factor", 0)
    if objective == "sortino":
        return m["sortino"]
    return m["sharpe"]


def monthly_table(dates: pd.Series, pnl: np.ndarray) -> list[dict]:
    s = pd.Series(pnl, index=pd.DatetimeIndex(dates))
    m = s.groupby([s.index.year, s.index.month]).sum()
    years = sorted({y for y, _ in m.index})
    return [{"year": int(y), "months": [round(float(m.get((y, k), 0.0)), 0) if (y, k) in m.index else None
                                         for k in range(1, 13)],
             "total": round(float(m.loc[y].sum()), 0)} for y in years]


# ---------------------------------------------------------------------- prop-firm evaluation simulation
def prop_eval(pnl: np.ndarray, rules: PropRules, max_days: int = 60, every: int = 5) -> dict:
    """
    Start a fresh evaluation every `every` trading days and replay the strategy's daily
    P&L through the firm rules. Daily bars can't see intraday swings, so intraday
    drawdown rules are approximated at the close - treat results as optimistic.
    """
    results = []
    n = len(pnl)
    for s in range(0, max(1, n - 10), every):
        bal, hwm, days, outcome, used = rules.account_size, rules.account_size, 0, "timeout", 0
        best = 0.0
        for i in range(s, min(n, s + max_days)):
            day = pnl[i]
            used += 1
            bal += day
            if day != 0:
                days += 1
                best = max(best, day)
            if rules.daily_loss_limit and day <= -rules.daily_loss_limit:
                outcome = "fail_daily"
                break
            thr = rules.account_size - rules.max_drawdown if rules.drawdown_mode == "static" else hwm - rules.max_drawdown
            if rules.drawdown_mode != "static" and rules.trail_locks_at_start:
                thr = min(thr, rules.account_size)
            if bal <= thr:
                outcome = "fail_drawdown"
                break
            hwm = max(hwm, bal)
            profit = bal - rules.account_size
            consistent = not rules.consistency_pct or profit <= 0 or best / profit * 100 <= rules.consistency_pct
            if rules.profit_target and profit >= rules.profit_target and days >= rules.min_trading_days and consistent:
                outcome = "pass"
                break
        results.append((outcome, used))
    total = len(results) or 1
    cnt = lambda k: sum(1 for o, _ in results if o == k)  # noqa: E731
    pass_days = [u for o, u in results if o == "pass"]
    return {
        "attempts": len(results),
        "pass_pct": round(100 * cnt("pass") / total, 1),
        "fail_drawdown_pct": round(100 * cnt("fail_drawdown") / total, 1),
        "fail_daily_pct": round(100 * cnt("fail_daily") / total, 1),
        "timeout_pct": round(100 * cnt("timeout") / total, 1),
        "median_days_to_pass": float(np.median(pass_days)) if pass_days else None,
        "max_days": max_days,
        "rules": rules.to_dict(),
    }


# ---------------------------------------------------------------------- public API
def get_strategy(key: str) -> Strategy:
    if key not in STRATEGIES:
        raise KeyError(f"Unknown strategy {key!r}")
    return STRATEGIES[key]


def run(md, cfg: RunConfig, rules: PropRules | None = None) -> dict:
    strat = get_strategy(cfg.strategy)
    ctx = build_context(md, cfg.symbol, cfg.start, cfg.end)
    params = {**strat.defaults(), **cfg.params}
    sim = simulate(ctx, strat.signal(ctx, params), cfg)
    m = metrics(sim["pnl"], cfg.account_size, sim["trades"], sim["position"])
    bm = metrics(sim["benchmark"] - np.r_[cfg.account_size, sim["benchmark"][:-1]], cfg.account_size)
    eq, dates = sim["equity"], sim["dates"]
    peak = np.maximum.accumulate(eq)
    warnings = []
    if strat.requires == "inventory" and ctx.inv_z is None:
        warnings.append("No inventory data for this market: strategy never trades. Use CL, NG, RB or HO.")
    if strat.requires == "cot" and ctx.cot_net is None:
        warnings.append("No COT data for this market.")
    if (eq <= 0).any():
        warnings.append(f"Account wiped out on {dates.iloc[int(np.argmax(eq <= 0))].date()}: equity fell to $0. "
                        "Reduce size or use risk sizing.")
    if m.get("trades", 0) < 30:
        warnings.append(f"Only {m.get('trades', 0)} trades: too few for statistically meaningful results.")
    return {
        "config": {**cfg.__dict__, "params": params},
        "strategy": strat.spec(),
        "metrics": m,
        "benchmark_metrics": bm,
        "equity": [{"time": str(d.date()), "value": round(float(v), 2)} for d, v in zip(dates, eq)],
        "benchmark": [{"time": str(d.date()), "value": round(float(v), 2)} for d, v in zip(dates, sim["benchmark"])],
        "drawdown": [{"time": str(d.date()), "value": round(float(v - p), 2)} for d, v, p in zip(dates, eq, peak)],
        "trades": sim["trades"],
        "monthly": monthly_table(dates, sim["pnl"]),
        "prop_eval": prop_eval(sim["pnl"], rules) if rules else None,
        "warnings": warnings,
    }


def _grid(strat: Strategy, ranges: dict[str, list]) -> tuple[list[str], list[dict]]:
    """ranges: {param: [min, max, step]} for numeric params, or {param: [choices...]} for choice params."""
    names, axes = [], []
    for spec in strat.params:
        name = spec["name"]
        if name not in ranges:
            continue
        r = ranges[name]
        if spec["type"] == "choice":
            vals = [v for v in r if v in spec["choices"]]
        else:
            lo, hi, step = (float(x) for x in r)
            if step <= 0 or hi < lo:
                raise ValueError(f"Bad range for {name}")
            vals = list(np.round(np.arange(lo, hi + step / 2, step), 6))
            if spec["type"] == "int":
                vals = sorted({int(round(v)) for v in vals})
        if not vals:
            raise ValueError(f"Empty range for {name}")
        names.append(name)
        axes.append(vals)
    combos = [dict(zip(names, v)) for v in itertools.product(*axes)]
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"{len(combos)} combinations - limit is {MAX_COMBOS}. Widen the steps or fix a parameter.")
    return names, combos


def _all_runs(md, cfg: RunConfig, ranges: dict):
    strat = get_strategy(cfg.strategy)
    ctx = build_context(md, cfg.symbol, cfg.start, cfg.end)
    names, combos = _grid(strat, ranges)
    base = {**strat.defaults(), **cfg.params}
    runs = []
    for combo in combos:
        p = {**base, **combo}
        sim = simulate(ctx, strat.signal(ctx, p), cfg)
        runs.append((combo, p, sim))
    return strat, names, runs


def _seg_trades(trades, dates, lo, hi):
    d0, d1 = str(dates.iloc[lo].date()), str(dates.iloc[hi - 1].date())
    return [t for t in trades if d0 <= t["exit_date"] <= d1]


def optimize(md, cfg: RunConfig, ranges: dict, objective: str = "sharpe", oos_pct: float = 30.0,
             top: int = 25) -> dict:
    strat, names, runs = _all_runs(md, cfg, ranges)
    n = len(runs[0][2]["pnl"])
    split = int(n * (1 - oos_pct / 100))
    rows = []
    for combo, p, sim in runs:
        dates = sim["dates"]
        m_is = metrics(sim["pnl"][:split], cfg.account_size, _seg_trades(sim["trades"], dates, 0, split))
        m_oos = metrics(sim["pnl"][split:], cfg.account_size, _seg_trades(sim["trades"], dates, split, n)) if split < n else {}
        valid = len(sim["trades"]) > 0
        rows.append({"params": combo, "is": m_is, "oos": m_oos, "valid": valid,
                     "is_score": score(m_is, objective) if valid else None,
                     "oos_score": score(m_oos, objective) if (m_oos and valid) else None})
    invalid = sum(1 for r in rows if not r["valid"])
    rows = [r for r in rows if r["valid"]] or rows
    if not rows[0]["valid"]:
        raise ValueError("No parameter combination produced any trades. Check the ranges and the market.")
    rows.sort(key=lambda r: r["is_score"], reverse=True)

    # Overfitting diagnostics
    is_s = pd.Series([r["is_score"] for r in rows])
    oos_s = pd.Series([r["oos_score"] if r["oos_score"] is not None else np.nan for r in rows])
    rank_corr = float(is_s.rank().corr(oos_s.rank())) if oos_s.notna().sum() > 3 else None
    best = rows[0]
    profitable_share = float(np.mean([r["is"]["net_pnl"] > 0 for r in rows]))

    heatmap = None
    if len(names) >= 2:
        x, y = names[0], names[1]
        xs = sorted({r["params"][x] for r in rows})
        ys = sorted({r["params"][y] for r in rows})
        cell = {}
        for r in rows:
            k = (r["params"][x], r["params"][y])
            cell[k] = max(cell.get(k, -1e18), r["is_score"])
        heatmap = {"x": x, "y": y, "xs": xs, "ys": ys,
                   "z": [[round(cell.get((a, b), float("nan")), 3) for a in xs] for b in ys]}
    split_date = str(runs[0][2]["dates"].iloc[split].date()) if split < n else None
    return {
        "objective": objective, "combos": len(rows), "invalid_combos": invalid, "oos_pct": oos_pct,
        "split_date": split_date,
        "param_names": names,
        "best": best,
        "results": rows[:top],
        "heatmap": heatmap,
        "diagnostics": {
            "is_oos_rank_corr": round(rank_corr, 2) if rank_corr is not None and not math.isnan(rank_corr) else None,
            "profitable_share_pct": round(100 * profitable_share, 1),
            "best_oos_vs_is": round(float(best["oos_score"]) / float(best["is_score"]), 2)
            if best["oos_score"] is not None and best["is_score"] not in (0, -1e18) else None,
        },
    }


def walk_forward(md, cfg: RunConfig, ranges: dict, objective: str = "sharpe", train_days: int = 504,
                 test_days: int = 126, anchored: bool = False) -> dict:
    """Re-optimise on each training window, trade the winner on the next unseen window, stitch the results."""
    strat, names, runs = _all_runs(md, cfg, ranges)
    n = len(runs[0][2]["pnl"])
    dates = runs[0][2]["dates"]
    if train_days + test_days > n:
        raise ValueError(f"Need {train_days + test_days} bars, have {n}. Shorten the windows.")
    folds, oos_pnl, is_scores, oos_scores = [], np.zeros(0), [], []
    oos_dates = []
    start = 0
    while start + train_days + test_days <= n:
        tr0 = 0 if anchored else start
        tr1 = start + train_days
        te1 = min(n, tr1 + test_days)
        live = [r for r in runs if r[2]["trades"]] or runs
        best = max(live, key=lambda r: score(metrics(r[2]["pnl"][tr0:tr1], cfg.account_size), objective))
        m_is = metrics(best[2]["pnl"][tr0:tr1], cfg.account_size)
        m_oos = metrics(best[2]["pnl"][tr1:te1], cfg.account_size,
                        _seg_trades(best[2]["trades"], dates, tr1, te1))
        folds.append({"train": [str(dates.iloc[tr0].date()), str(dates.iloc[tr1 - 1].date())],
                      "test": [str(dates.iloc[tr1].date()), str(dates.iloc[te1 - 1].date())],
                      "params": best[0], "is": m_is, "oos": m_oos})
        is_scores.append(m_is["ann_return_pct"])
        oos_scores.append(m_oos["ann_return_pct"])
        oos_pnl = np.r_[oos_pnl, best[2]["pnl"][tr1:te1]]
        oos_dates += list(dates.iloc[tr1:te1])
        start += test_days
    eq = cfg.account_size + np.cumsum(oos_pnl)
    is_avg = float(np.mean(is_scores)) if is_scores else 0.0
    return {
        "folds": folds,
        "oos_metrics": metrics(oos_pnl, cfg.account_size),
        "oos_equity": [{"time": str(d.date()), "value": round(float(v), 2)} for d, v in zip(oos_dates, eq)],
        "wf_efficiency": round(float(np.mean(oos_scores)) / is_avg, 2) if is_avg > 0 else None,
        "param_stability": {nm: sorted({f["params"].get(nm) for f in folds}, key=str) for nm in names},
    }


def monte_carlo(trades: list[dict], account: float, n_sims: int = 1000, rules: PropRules | None = None,
                seed: int = 7) -> dict:
    """Resample the trade sequence to see the range of drawdowns/outcomes luck alone could produce."""
    tp = np.array([t["pnl"] for t in trades], float)
    if len(tp) < 5:
        return {"error": "Need at least 5 trades for Monte Carlo."}
    rng = np.random.default_rng(seed)
    sims = rng.choice(tp, size=(n_sims, len(tp)), replace=True)
    eq = np.cumsum(sims, axis=1)
    peak = np.maximum.accumulate(np.c_[np.zeros(n_sims), eq], axis=1)[:, 1:]
    max_dd = (peak - eq).max(axis=1)
    final = eq[:, -1]
    pct = lambda a: {str(q): round(float(np.percentile(a, q)), 2) for q in (5, 25, 50, 75, 95)}  # noqa: E731
    out = {
        "sims": n_sims, "trades_per_sim": len(tp),
        "final_pnl": pct(final), "max_dd": pct(max_dd),
        "prob_loss_pct": round(100 * float((final < 0).mean()), 1),
        "paths": [[0.0] + [round(float(v), 0) for v in eq[i, :: max(1, len(tp) // 120)]] for i in range(30)],
    }
    if rules:
        out["prob_breach_max_dd_pct"] = round(100 * float((max_dd >= rules.max_drawdown).mean()), 1)
        out["prop_max_drawdown"] = rules.max_drawdown
    return out
