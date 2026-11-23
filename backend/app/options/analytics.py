"""Options positioning and dealer gamma (GEX) for futures options, via Black-76.

Gamma exposure (GEX), in $ per 1% move = Σ sign × gamma × OI × multiplier × F² × 1%.
The sign encodes who is assumed to be short the options (the dealer's side):
  standard     dealers LONG calls, SHORT puts (customers overwrite calls and buy put protection),
               the usual equity-index convention
  short_calls  dealers SHORT calls and SHORT puts (customers buy calls and puts), closer to
               natural gas in winter, when consumers and speculators pay up for upside
Positive dealer gamma: hedging sells rallies and buys dips, so moves are damped and price tends
to pin near big strikes. Negative dealer gamma: hedging chases the move, so moves accelerate.
The flip is the price where total GEX changes sign. These are MODEL ESTIMATES: real dealer
positioning isn't public, and the result depends on the sign assumption.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from .. import universe

R = 0.04  # discount rate for Black-76


def _ncdf(x):
    return 0.5 * (1 + np.vectorize(math.erf)(np.asarray(x) / math.sqrt(2)))


def _npdf(x):
    x = np.asarray(x)
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def greeks(F, K, T, iv, is_call):
    """Black-76 delta and gamma (per 1.0 move in the future), arrays in, arrays out."""
    F, K, T, iv, is_call = np.broadcast_arrays(*(np.atleast_1d(np.asarray(a, float)) for a in (F, K, T, iv)),
                                               np.atleast_1d(np.asarray(is_call, bool)))
    sq = np.maximum(iv * np.sqrt(T), 1e-9)
    d1 = (np.log(F / K) + 0.5 * iv * iv * T) / sq
    disc = np.exp(-R * T)
    gamma = disc * _npdf(d1) / (F * sq)
    delta = np.where(is_call, disc * _ncdf(d1), -disc * _ncdf(-d1))
    return delta, gamma


def default_mode(symbol: str, today: date) -> str:
    return "short_calls" if symbol == "NG" and today.month in (11, 12, 1, 2, 3) else "standard"


def analyse(chain: pd.DataFrame, meta: dict, mode: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    sym, F, mult = meta["symbol"], float(meta["price"]), float(meta.get("multiplier", 1))
    fut_pv = universe.get(sym).point_value
    tick = universe.get(sym).tick_size
    mode = mode or default_mode(sym, today)
    df = chain.copy()
    df = df[(df["oi"] > 0) | (df["volume"] > 0)]
    df["T"] = [max((e - today).days, 0.5) / 365 for e in df["expiry"]]
    df["is_call"] = df["type"] == "C"
    df["iv"] = df["iv"].where(df["iv"] > 0, df["iv"][df["iv"] > 0].median() if (df["iv"] > 0).any() else 0.3)
    delta, gamma = greeks(F, df["strike"], df["T"], df["iv"], df["is_call"].to_numpy())
    df["delta"], df["gamma"] = delta, gamma
    sign = np.where(df["is_call"], 1.0 if mode == "standard" else -1.0, -1.0)
    df["gex"] = sign * df["gamma"] * df["oi"] * mult * F * F * 0.01          # $ per 1% move

    # ---- GEX profile across prices (sticky-strike IVs)
    span = 0.25 if sym == "NG" else 0.12 if sym in ("CL", "GC") else 0.08
    grid = np.linspace(F * (1 - span), F * (1 + span), 81)
    prof = []
    for S in grid:
        _, g = greeks(S, df["strike"], df["T"], df["iv"], df["is_call"].to_numpy())
        prof.append(float(np.sum(sign * g * df["oi"] * mult * S * S * 0.01)))
    prof = np.array(prof)
    flip = None
    for i in range(1, len(grid)):
        if np.sign(prof[i]) != np.sign(prof[i - 1]):
            x0, x1, y0, y1 = grid[i - 1], grid[i], prof[i - 1], prof[i]
            cand = x0 - y0 * (x1 - x0) / (y1 - y0)
            if flip is None or abs(cand - F) < abs(flip - F):
                flip = float(cand)
    total = float(df["gex"].sum())
    hedge_contracts = -total / (F * fut_pv) if F else 0.0        # futures dealers trade per +1% move

    # ---- strikes
    by_k = df.groupby(["strike", "type"]).agg(oi=("oi", "sum"), volume=("volume", "sum"), gex=("gex", "sum")).reset_index()
    calls, puts = by_k[by_k["type"] == "C"], by_k[by_k["type"] == "P"]
    net = by_k.groupby("strike")["gex"].sum()
    near = net[(net.index > F * (1 - span)) & (net.index < F * (1 + span))]
    above_c = calls[calls["strike"] >= F]
    below_p = puts[puts["strike"] <= F]
    call_wall = float(above_c.loc[above_c["oi"].idxmax(), "strike"]) if len(above_c) else None
    put_wall = float(below_p.loc[below_p["oi"].idxmax(), "strike"]) if len(below_p) else None
    rnd = lambda v: None if v is None else round(round(v / tick) * tick, 6)  # noqa: E731

    # ---- expiries
    exps = []
    for ex, g in df.groupby("expiry"):
        c, p = g[g["is_call"]], g[~g["is_call"]]
        T = float(g["T"].iloc[0])
        atm = g.iloc[(g["strike"] - F).abs().argsort()[:2]]
        atm_iv = float(atm["iv"].mean())
        c25 = c.iloc[(c["delta"] - 0.25).abs().argsort()[:1]] if len(c) else None
        p25 = p.iloc[(p["delta"] + 0.25).abs().argsort()[:1]] if len(p) else None
        rr25 = float(c25["iv"].iat[0] - p25["iv"].iat[0]) if c25 is not None and p25 is not None and len(c25) and len(p25) else None
        straddle = 0.798 * F * atm_iv * math.sqrt(T)                       # ≈ ATM straddle premium = expected move
        # max pain: the settlement price that minimises total payout to option holders
        ks = np.unique(g["strike"])
        pain = [float((np.maximum(k - c["strike"], 0) * c["oi"]).sum() + (np.maximum(p["strike"] - k, 0) * p["oi"]).sum()) for k in ks]
        exps.append({"expiry": ex.isoformat(), "days": max((ex - today).days, 0), "oi_calls": int(c["oi"].sum()), "oi_puts": int(p["oi"].sum()),
                     "vol_calls": int(c["volume"].sum()), "vol_puts": int(p["volume"].sum()),
                     "pcr_oi": round(p["oi"].sum() / max(c["oi"].sum(), 1), 2), "pcr_vol": round(p["volume"].sum() / max(c["volume"].sum(), 1), 2),
                     "atm_iv": round(100 * atm_iv, 1), "rr25": round(100 * rr25, 1) if rr25 is not None else None,
                     "expected_move": rnd(straddle), "expected_move_pct": round(100 * straddle / F, 2),
                     "max_pain": rnd(float(ks[int(np.argmin(pain))])) if len(ks) else None,
                     "oi_share": 0.0, "gex": round(float(g["gex"].sum()), 0)})
    tot_oi = sum(e["oi_calls"] + e["oi_puts"] for e in exps) or 1
    for e in exps:
        e["oi_share"] = round(100 * (e["oi_calls"] + e["oi_puts"]) / tot_oi, 1)
    front = exps[0] if exps else {}

    regime = "POSITIVE" if total > 0 else "NEGATIVE"
    notes = []
    if regime == "POSITIVE":
        notes.append(f"Dealers are modelled LONG gamma ({money_s(total)} per 1%): their hedging sells rallies and buys dips. "
                     "Expect mean reversion and pinning toward big strikes; fade extremes, take profits sooner.")
    else:
        notes.append(f"Dealers are modelled SHORT gamma ({money_s(total)} per 1%): their hedging buys rallies and sells dips. "
                     "Moves can accelerate; favour breakouts and trend continuation, and keep stops honest.")
    if not flip:
        notes.append("No gamma flip within the range shown: under this dealer assumption, gamma keeps the same sign across it.")
    if flip:
        notes.append(f"Gamma flip ≈ {rnd(flip)}: {'above' if F > flip else 'below'} it now. Crossing it changes the "
                     "dynamic from damped to accelerating (or back).")
    if call_wall:
        notes.append(f"Call wall {rnd(call_wall)} (largest call open interest above price): often resistance, a magnet into expiry.")
    if put_wall:
        notes.append(f"Put wall {rnd(put_wall)} (largest put open interest below price): often support, but a break can accelerate selling.")
    if front.get("rr25") is not None:
        rr = front["rr25"]
        notes.append(f"Front-expiry 25-delta risk reversal {rr:+.1f} vol: "
                     + ("calls are richer than puts (CALL SKEW): the market pays for upside protection." if rr > 0.5 else
                        "puts are richer than calls (PUT SKEW): the market pays for downside protection." if rr < -0.5 else "skew is balanced."))
    if front:
        notes.append(f"The front straddle prices a ±{front['expected_move']} ({front['expected_move_pct']}%) move by {front['expiry']} "
                     f"({front['days']} days).")
    notes.append(f"Hedging flow estimate: for every +1% move, dealers {'sell' if hedge_contracts < 0 else 'buy'} about "
                 f"{abs(hedge_contracts):,.0f} {sym} futures to stay delta-neutral (the reverse on a -1% move).")
    top = lambda d, k: [{"strike": rnd(r.strike), "oi": int(r.oi), "volume": int(r.volume)} for r in d.nlargest(k, "oi").itertuples()]  # noqa: E731
    return {
        "symbol": sym, "price": F, "source": meta["source"], "mode": mode, "multiplier": mult,
        "total_gex": round(total, 0), "regime": regime, "flip": rnd(flip), "call_wall": rnd(call_wall), "put_wall": rnd(put_wall),
        "hedge_contracts_per_1pct": round(hedge_contracts, 0),
        "pcr_oi": round(puts["oi"].sum() / max(calls["oi"].sum(), 1), 2),
        "pcr_vol": round(puts["volume"].sum() / max(calls["volume"].sum(), 1), 2),
        "front": front, "expiries": exps,
        "top_calls": top(calls, 6), "top_puts": top(puts, 6),
        "vol_leaders": [{"strike": rnd(r.strike), "type": r.type, "volume": int(r.volume)} for r in by_k.nlargest(6, "volume").itertuples()],
        "strikes": [{"strike": rnd(k), "call_oi": int(calls.loc[calls["strike"] == k, "oi"].sum()),
                     "put_oi": int(puts.loc[puts["strike"] == k, "oi"].sum()), "gex": round(float(v), 0)} for k, v in near.items()],
        "profile": [{"price": round(float(s), 6), "gex": round(float(g), 0)} for s, g in zip(grid, prof)],
        "notes": notes,
    }


def money_s(v: float) -> str:
    a = abs(v)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"
    return ("-" if v < 0 else "+") + s
