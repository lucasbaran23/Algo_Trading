"""Gamma-scalping backtests.

1) Options gamma scalping (long gamma), and its mirror image (short gamma)
   Each cycle: buy an at-the-money straddle at the RTH open, priced with Black-76 at an assumed
   implied vol, and hold it to expiry N sessions later. Delta-hedge it with futures, either on a
   clock (every 5 / 15 / 60 minutes, or once at the close) or whenever the net delta drifts past a
   band. P&L = straddle payoff - premium + hedge P&L - hedging costs - option entry costs.
   Theory: P&L ≈ ½ Γ F² (σ_realised² - σ_implied²) t. You win when the market moves MORE than the
   option price implied; hedging more often captures more of that variance but pays more costs.
   The short-gamma version (sell the straddle, hedge it) is what option dealers and premium sellers run.

   Implied vol: there is no free history of futures-option implied vol, so it is an INPUT:
     rv_mult  IV = trailing 20-session realised vol × multiplier (1.1 ≈ a typical volatility risk premium)
     fixed    a constant IV
     csv      your own history in backend/data/options/{SYMBOL}_iv.csv (date, iv)
   The breakeven multiplier, where long gamma stops paying, is the key output.

2) Gamma-regime split for futures day trading
   Do momentum setups do better on negative dealer-gamma days, and reversion setups on positive
   ones? This needs a daily history of dealer gamma: backend/data/options/{SYMBOL}_gex.csv
   (date, gex). Without it, a synthetic history is used that has no link to price by construction
   (labelled SIM), so any difference you see there is noise.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .. import universe
from ..daytrade import calendar as cal
from ..data import intraday
from .analytics import R, _ncdf, _npdf

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "options"


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _b76(F, K, T, iv):
    """Black-76 straddle value and delta (vectorised)."""
    F, T = np.asarray(F, float), np.maximum(np.asarray(T, float), 1e-9)
    sq = iv * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * iv * iv * T) / sq
    d2 = d1 - sq
    disc = np.exp(-R * T)
    call = disc * (F * _ncdf(d1) - K * _ncdf(d2))
    put = disc * (K * _ncdf(-d2) - F * _ncdf(-d1))
    delta = disc * (_ncdf(d1) - _ncdf(-d1))                 # straddle delta = call delta + put delta
    gamma = 2 * disc * _npdf(d1) / (F * sq)
    vega = 2 * disc * F * _npdf(d1) * np.sqrt(T)
    return call + put, delta, gamma, vega


def _rth_frame(md, symbol: str) -> tuple[pd.DataFrame, str, tuple[int, int]]:
    c = universe.get(symbol)
    df, src = intraday.bars(md, c.data_symbol)
    o_s, c_s = (_hm(x) for x in cal.session_window(c.sector, c.data_symbol))
    mins = df.index.hour * 60 + df.index.minute
    rth = df[(mins > o_s) & (mins <= c_s)].copy()
    rth["day"] = rth.index.normalize()
    return rth, src, (o_s, c_s)


def _iv_series(md, symbol: str, days: pd.DatetimeIndex, mode: str, value: float) -> tuple[pd.Series, str]:
    c = universe.get(symbol)
    if mode == "csv":
        p = DATA_DIR / f"{c.data_symbol}_iv.csv"
        if p.exists():
            x = pd.read_csv(p)
            s = pd.Series(x.iloc[:, 1].astype(float).to_numpy(), index=pd.to_datetime(x.iloc[:, 0]).dt.normalize())
            s = s / 100 if s.median() > 3 else s
            return s.reindex(days, method="ffill").bfill(), f"CSV implied vol ({p.name})"
        mode = "rv_mult"
    if mode == "fixed":
        return pd.Series(value, index=days), f"fixed IV {value:.0%}"
    # Trailing realised vol measured the way a hedger experiences it: 15-minute returns through the
    # regular session plus the overnight gap, annualised, averaged over the prior 20 sessions.
    rth, _, _ = _rth_frame(md, symbol)
    c15 = rth["close"].resample("15min").last().dropna()
    var_day = (np.log(c15).diff() ** 2).groupby(c15.index.normalize()).sum()
    rv = np.sqrt(var_day.rolling(20, min_periods=10).mean() * 252)
    rv = rv.shift(1).reindex(days, method="ffill").bfill()       # only information before the session
    return rv * value, f"IV = trailing 20-session realised vol (15-min) × {value:.2f}"


def scalp(md, symbol: str, dte: int = 5, hedge: str = "15m", iv_mode: str = "rv_mult", iv_value: float = 1.1,
          side: int = 1, band: float = 0.25, opt_spread_vol: float = 0.01, commission: float = 2.5,
          slip_ticks: float = 1.0, contracts: int = 1) -> dict:
    """Run straddle-plus-hedge cycles of `dte` sessions. side=+1 long gamma, -1 short gamma."""
    c = universe.get(symbol)
    sym = c.data_symbol
    pv, tick = universe.get(sym).point_value, c.tick_size
    rth, src, (o_s, c_s) = _rth_frame(md, sym)
    days = pd.DatetimeIndex(sorted(rth["day"].unique()))
    iv_s, iv_label = _iv_series(md, sym, days, iv_mode, iv_value)
    cost_fut = commission + slip_ticks * tick * pv               # per futures contract per side
    step = {"NG": 0.05, "ES": 5.0, "NQ": 25.0, "CL": 0.5, "GC": 5.0}.get(sym, tick * 10)
    every = {"5m": 5, "15m": 15, "60m": 60, "close": None, "band": 5}[hedge]
    cycles, eq_rows, cum = [], [], 0.0
    for start in range(0, len(days) - dte + 1, dte):
        cyc_days = days[start:start + dte]
        bars = rth[rth["day"].isin(cyc_days)]
        if len(bars) < 30:
            continue
        F0 = float(bars["open"].iloc[0])
        K = round(round(F0 / step) * step, 6)
        iv = float(iv_s.loc[cyc_days[0]])
        if not math.isfinite(iv) or iv <= 0:
            continue
        expiry = bars.index[-1]
        t_years = ((expiry - bars.index).total_seconds() / (365 * 86400)).to_numpy()
        T0 = (expiry - bars.index[0] + pd.Timedelta(minutes=1)).total_seconds() / (365 * 86400)
        prem, _, _, vega0 = _b76(F0, K, T0, iv)
        prem, vega0 = float(prem), float(vega0)
        # hedge decision points
        mins = (bars.index.hour * 60 + bars.index.minute).to_numpy()
        last_of_day = bars["day"].ne(bars["day"].shift(-1)).to_numpy()
        if every is None:
            dec = last_of_day.copy()
        else:
            dec = ((mins - o_s) % every == 0) | last_of_day
        dec[-1] = False                                            # flat at expiry
        px = bars["close"].to_numpy()
        _, delta, _, _ = _b76(px, K, np.maximum(t_years, 1e-6), iv)
        pos = 0.0                                                  # futures position (contracts per straddle)
        hedge_pnl = trades = costs = 0.0
        # opening hedge at the entry price
        _, d0, _, _ = _b76(F0, K, T0, iv)
        pos = -float(d0) * side
        trades += abs(pos)
        costs += abs(pos) * cost_fut
        prev = F0
        for j in range(len(px)):
            hedge_pnl += pos * (px[j] - prev) * pv
            prev = px[j]
            if not dec[j]:
                continue
            target = -float(delta[j]) * side
            if hedge == "band" and abs(target - pos) < band:
                continue
            q = target - pos
            trades += abs(q)
            costs += abs(q) * cost_fut
            pos = target
        F_T = float(px[-1])
        trades += abs(pos)
        costs += abs(pos) * cost_fut
        payoff = abs(F_T - K)
        option_pnl = side * (payoff - prem) * pv
        opt_cost = 0.5 * opt_spread_vol * vega0 * pv                # half the bid/ask in vol terms, on entry
        pnl = (option_pnl + hedge_pnl - costs - opt_cost) * contracts
        # realised vol over the cycle (close-to-close of the hedge bars, annualised by elapsed time)
        lr = np.diff(np.log(np.r_[F0, px]))
        rv = float(math.sqrt(np.sum(lr ** 2) / max(T0, 1e-9)))
        cum += pnl
        cycles.append({"start": str(cyc_days[0].date()), "end": str(cyc_days[-1].date()), "strike": K,
                       "iv": round(100 * iv, 1), "rv": round(100 * rv, 1), "premium_usd": round(prem * pv, 0),
                       "option_pnl": round(option_pnl, 0), "hedge_pnl": round(hedge_pnl, 0),
                       "costs": round(costs + opt_cost, 0), "pnl": round(pnl, 0), "hedge_trades": round(trades, 2)})
        eq_rows.append({"time": str(cyc_days[-1].date()), "value": round(cum, 0)})
    p = np.array([x["pnl"] for x in cycles], float)
    n = len(p)
    if n == 0:
        return {"symbol": sym, "cycles": [], "stats": {"n": 0}, "source": src}
    sd = p.std(ddof=1) if n > 1 else 0.0
    per_year = 252 / dte
    spread = np.array([x["rv"] - x["iv"] for x in cycles])
    stats = {
        "n": n, "total": round(float(p.sum()), 0), "avg": round(float(p.mean()), 0), "win_pct": round(100 * float((p > 0).mean()), 1),
        "t_stat": round(float(p.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0,
        "sharpe": round(float(p.mean() / sd * math.sqrt(per_year)), 2) if sd > 0 else 0.0,
        "worst": round(float(p.min()), 0), "best": round(float(p.max()), 0),
        "avg_iv": round(float(np.mean([x["iv"] for x in cycles])), 1), "avg_rv": round(float(np.mean([x["rv"] for x in cycles])), 1),
        "win_when_rv_gt_iv_pct": round(100 * float((p[spread > 0] > 0).mean()), 1) if (spread > 0).any() else None,
        "avg_costs": round(float(np.mean([x["costs"] for x in cycles])), 0),
        "avg_hedge_trades": round(float(np.mean([x["hedge_trades"] for x in cycles])), 1),
        "option_pnl": round(float(np.sum([x["option_pnl"] for x in cycles])), 0),
        "hedge_pnl": round(float(np.sum([x["hedge_pnl"] for x in cycles])), 0),
        "corr_pnl_rv_minus_iv": round(float(np.corrcoef(spread, p)[0, 1]), 2) if n > 3 and spread.std() > 0 else None,
    }
    return {"symbol": sym, "source": src, "iv_label": iv_label, "side": "LONG gamma" if side > 0 else "SHORT gamma",
            "dte": dte, "hedge": hedge, "band": band, "stats": stats, "cycles": cycles, "equity": eq_rows,
            "scatter": [{"x": round(x["rv"] - x["iv"], 1), "y": x["pnl"]} for x in cycles]}


_SWEEP_CACHE: dict = {}


def sweep(md, symbol: str, dte: int = 5, iv_mode: str = "rv_mult", iv_value: float = 1.1, side: int = 1, **kw) -> dict:
    """Hedge-frequency sweep, IV-assumption sweep (breakeven), and the best configuration's details."""
    import time
    key = (symbol, dte, iv_mode, iv_value, side, tuple(sorted(kw.items())), len(md.daily[universe.get(symbol).data_symbol]))
    hit = _SWEEP_CACHE.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    out = _sweep(md, symbol, dte, iv_mode, iv_value, side, **kw)
    _SWEEP_CACHE[key] = (time.time(), out)
    return out


def _sweep(md, symbol: str, dte: int, iv_mode: str, iv_value: float, side: int, **kw) -> dict:
    hedges = ["5m", "15m", "60m", "close", "band"]
    by_hedge = []
    for h in hedges:
        r = scalp(md, symbol, dte, h, iv_mode, iv_value, side, **kw)
        by_hedge.append({"hedge": h, **r["stats"]})
    best_h = max(by_hedge, key=lambda r: r.get("avg", -1e18))["hedge"]
    mults = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
    by_iv = []
    if iv_mode == "rv_mult":
        for m in mults:
            r = scalp(md, symbol, dte, best_h, "rv_mult", m, side, **kw)
            by_iv.append({"mult": m, **r["stats"]})
    breakeven = None
    for a, b in zip(by_iv, by_iv[1:]):
        if a.get("avg") is not None and b.get("avg") is not None and np.sign(a["avg"]) != np.sign(b["avg"]):
            breakeven = round(a["mult"] + (b["mult"] - a["mult"]) * a["avg"] / (a["avg"] - b["avg"]), 2)
    detail = scalp(md, symbol, dte, best_h, iv_mode, iv_value, side, **kw)
    return {"symbol": universe.get(symbol).data_symbol, "dte": dte, "side": detail.get("side"), "iv_label": detail.get("iv_label"),
            "source": detail.get("source"), "by_hedge": by_hedge, "best_hedge": best_h, "by_iv": by_iv,
            "breakeven_mult": breakeven, "detail": detail}


# ---------------------------------------------------------------------- gamma-regime split for futures day trading
def regime_split(md, symbol: str, commission: float = 2.5, slip_ticks: float = 1.0) -> dict:
    from ..intraday import research as ir
    from ..intraday.strategies import BY_KEY
    c = universe.get(symbol)
    sym = c.data_symbol
    df, src = intraday.bars(md, sym)
    sessions = ir.prepare_sessions(df, sym)
    dates = pd.DatetimeIndex([pd.Timestamp(s["date"]) for s in sessions])
    p = DATA_DIR / f"{sym}_gex.csv"
    if p.exists():
        g = pd.read_csv(p)
        gex = pd.Series(g.iloc[:, 1].astype(float).to_numpy(), index=pd.to_datetime(g.iloc[:, 0]).dt.normalize())
        gex = gex.shift(1).reindex(dates, method="ffill")          # yesterday's positioning, known before the session
        gsrc = f"CSV ({p.name})"
    else:
        rng = np.random.default_rng(5)
        x = np.zeros(len(dates))
        for i in range(1, len(x)):
            x[i] = 0.9 * x[i - 1] + rng.normal()
        gex = pd.Series(x, index=dates)
        gsrc = "SIM: synthetic gamma history with no link to price; differences are noise"
    pv, tv = universe.get(sym).point_value, c.tick_value
    rt_cost = 2 * (commission + slip_ticks * tv)
    out = []
    for key, tf, params in (("orb", 15, {"variant": "breakout"}), ("intraday_momentum", 30, {"signal": "first30"}),
                            ("noise_area", 30, {"band": 1.0}), ("rsi2_reversion", 5, {"lo": 10})):
        strat = BY_KEY[key]
        if not strat.applies(sym, c.sector):
            continue
        idx, net, _ = ir._run(strat, sessions, tf, params, pv, rt_cost)
        reg = np.sign(gex.to_numpy()[idx]) if len(idx) else np.array([])
        row = {"key": key, "name": strat.name, "family": strat.family, "tf": tf}
        for lab, m in (("positive", reg > 0), ("negative", reg < 0)):
            v = net[m]
            sd = v.std(ddof=1) if len(v) > 1 else 0
            row[lab] = {"n": int(len(v)), "avg": round(float(v.mean()), 0) if len(v) else None,
                        "avg_ticks": round(float(v.mean() / tv), 1) if len(v) else None,
                        "win_pct": round(100 * float((v > 0).mean()), 0) if len(v) else None,
                        "t": round(float(v.mean() / (sd / math.sqrt(len(v)))), 2) if sd > 0 else None}
        out.append(row)
    return {"symbol": sym, "source": src, "gex_source": gsrc, "days_positive": int((gex > 0).sum()),
            "days_negative": int((gex < 0).sum()), "rows": out,
            "theory": "Positive dealer gamma tends to damp moves (favouring reversion); negative gamma amplifies them (favouring momentum)."}
