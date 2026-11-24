"""Trading futures around key options levels: call wall, put wall, gamma flip and max pain.

Why these levels matter (the dealer-hedging mechanism)
  Option market makers hedge their option books with futures. When they are LONG gamma (positive
  GEX, price above the flip), their hedging is contrarian: they sell into rallies and buy dips, which
  damps moves and makes big strikes act as resistance (call wall) and support (put wall).
  When they are SHORT gamma (negative GEX, below the flip), hedging follows the move: they sell into
  drops and buy into rallies, so a break of a wall can accelerate.
  Into expiry, gamma concentrates at the biggest strikes and price tends to be pulled toward them
  (pinning; max pain is a rough proxy for where that is).

The playbook tested here (one trade per rule per session, flat by the close)
  wall_fade   Positive gamma: price tags the call wall (put wall) and a 5-minute bar closes back
              inside: sell (buy). Stop beyond the session extreme; target 1.5R.
  wall_break  Negative gamma: a 5-minute close beyond the put wall (call wall): go with the break.
              Stop back inside the wall; target 2R.
  flip_cross  A 5-minute close through the gamma flip from the side it opened on: trade the new regime's
              direction. Stop on the other side; target 2R.
  pin         Last 2 sessions before the front expiry, from mid-session: if price is far from max pain,
              trade toward it. Target max pain; stop the same distance the other way.

Levels for each session must be known before it starts: from backend/data/options/{SYM}_levels.csv
(date, call_wall, put_wall, flip, max_pain, gex) exported from your options data, or (SIM) from a
synthetic chain rebuilt every morning around the prior close. The synthetic chain clusters open
interest at round strikes, as real chains do, but carries no real dealer positioning.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from .. import universe
from ..data import intraday
from ..intraday import research as ir
from ..intraday.extremes import _bars5
from . import chains
from .analytics import default_mode

RULES = {
    "wall_fade": ("Wall fade (long gamma)", "Reversion"),
    "wall_break": ("Wall break (short gamma)", "Breakout"),
    "flip_cross": ("Gamma-flip cross", "Regime"),
    "pin": ("Expiry pin to max pain", "Expiry"),
}


def _gamma(F, K, T, iv):
    sT = iv * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * iv * iv * T) / sT
    return np.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (F * sT)


def levels_for(chain: pd.DataFrame, F: float, today: date, mode: str, mult: float) -> dict:
    df = chain[(chain["oi"] > 0)]
    T = np.array([max((e - today).days, 0.5) / 365 for e in df["expiry"]])
    K, iv, oi = df["strike"].to_numpy(float), df["iv"].to_numpy(float), df["oi"].to_numpy(float)
    is_call = (df["type"] == "C").to_numpy()
    sign = np.where(is_call, 1.0 if mode == "standard" else -1.0, -1.0)
    grid = np.linspace(F * 0.9, F * 1.1, 41)
    prof = np.array([float(np.sum(sign * _gamma(S, K, T, iv) * oi * mult * S * S * 0.01)) for S in grid])
    flip = None
    for i in range(1, len(grid)):
        if np.sign(prof[i]) != np.sign(prof[i - 1]):
            cand = grid[i - 1] - prof[i - 1] * (grid[i] - grid[i - 1]) / (prof[i] - prof[i - 1])
            if flip is None or abs(cand - F) < abs(flip - F):
                flip = float(cand)
    total = float(np.sum(sign * _gamma(F, K, T, iv) * oi * mult * F * F * 0.01))
    calls = df[is_call].groupby("strike")["oi"].sum()
    puts = df[~is_call].groupby("strike")["oi"].sum()
    ca, pb = calls[calls.index >= F], puts[puts.index <= F]
    front = min(df["expiry"])
    g = df[df["expiry"] == front]
    ks = np.unique(g["strike"])
    gc, gp = g[g["type"] == "C"], g[g["type"] == "P"]
    pain = [float((np.maximum(k - gc["strike"], 0) * gc["oi"]).sum() + (np.maximum(gp["strike"] - k, 0) * gp["oi"]).sum()) for k in ks]
    return {"call_wall": float(ca.idxmax()) if len(ca) else None, "put_wall": float(pb.idxmax()) if len(pb) else None,
            "flip": flip, "max_pain": float(ks[int(np.argmin(pain))]) if len(ks) else None, "gex": total,
            "front_days": (front - today).days}


def history(md, sym: str, dates: list[date]) -> tuple[pd.DataFrame, str]:
    """Levels known before each session (index = session date)."""
    p = chains.DATA_DIR / f"{sym}_levels.csv"
    if p.exists():
        g = pd.read_csv(p)
        g.columns = [c.lower() for c in g.columns]
        g.index = pd.to_datetime(g["date"]).dt.normalize()
        g = g.drop(columns=["date"]).sort_index().shift(1)          # yesterday's levels for today's session
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
        g = g.reindex(idx, method="ffill")
        if "front_days" not in g:
            g["front_days"] = 99
        return g, f"CSV ({p.name})"
    daily = md.daily[sym].copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    daily = daily.set_index("date")
    vol = np.log(daily["close"]).diff().rolling(60, min_periods=20).std() * math.sqrt(252)
    mult = universe.get(sym).point_value
    rows = {}
    for d in dates:
        ts = pd.Timestamp(d)
        prior = daily.loc[:ts - pd.Timedelta(days=1)]
        if prior.empty:
            continue
        F = float(prior["close"].iloc[-1])
        av = float(vol.loc[:ts - pd.Timedelta(days=1)].iloc[-1]) if len(prior) > 20 else 0.3
        ch = chains.simulate(sym, F, av if np.isfinite(av) else 0.3, d, md.settings.sim_seed)
        rows[ts] = levels_for(ch, F, d, default_mode(sym, d), mult)
    return pd.DataFrame.from_dict(rows, orient="index"), "SIM: synthetic chain rebuilt each morning (round-strike open interest, no real positioning)"


def _session_trades(s, lv: dict, u: float, rules=("wall_fade", "wall_break", "flip_cross", "pin")) -> dict:
    """Trades (side, entry, exit) per rule for one session. u = one daily sigma in price."""
    out = {r: None for r in RULES}
    m, h, l, c, o = s["m"], s["h"], s["l"], s["c"], s["o"]
    T = float(m[-1])
    b5 = _bars5(s)
    cw, pw, fl, mp = lv.get("call_wall"), lv.get("put_wall"), lv.get("flip"), lv.get("max_pain")
    gex = lv.get("gex") or 0.0
    pos_gamma = gex > 0 and (fl is None or o[0] > fl)
    fin = lambda x: x is not None and np.isfinite(x)  # noqa: E731

    def run(side, i0, entry, stop, tgt):
        for j in range(i0 + 1, len(c)):
            if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
                return side, entry, stop
            if (side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt):
                return side, entry, tgt
        return side, entry, c[-1]

    late = T - 30
    # --- wall fade (positive gamma)
    if "wall_fade" in rules and pos_gamma:
        touched = {1: False, -1: False}
        for n, (i, bh, bl, bc) in enumerate(b5):
            if m[i] > late:
                break
            if fin(cw) and bh >= cw - 0.05 * u:
                touched[-1] = True
            if fin(pw) and bl <= pw + 0.05 * u:
                touched[1] = True
            hi, lo = h[: i + 1].max(), l[: i + 1].min()
            if touched[-1] and bc < cw - 0.05 * u and fin(cw):
                stop = max(hi, cw) + 0.1 * u
                risk = stop - bc
                if risk > 0:
                    out["wall_fade"] = run(-1, i, bc, stop, bc - 1.5 * risk)
                break
            if touched[1] and bc > pw + 0.05 * u and fin(pw):
                stop = min(lo, pw) - 0.1 * u
                risk = bc - stop
                if risk > 0:
                    out["wall_fade"] = run(1, i, bc, stop, bc + 1.5 * risk)
                break
    # --- wall break (negative gamma)
    if "wall_break" in rules and not pos_gamma:
        for i, bh, bl, bc in b5:
            if m[i] > late:
                break
            if fin(pw) and bc < pw - 0.1 * u and o[0] > pw:
                stop = pw + 0.15 * u
                out["wall_break"] = run(-1, i, bc, stop, bc - 2 * (stop - bc))
                break
            if fin(cw) and bc > cw + 0.1 * u and o[0] < cw:
                stop = cw - 0.15 * u
                out["wall_break"] = run(1, i, bc, stop, bc + 2 * (bc - stop))
                break
    # --- flip cross
    if "flip_cross" in rules and fin(fl) and abs(o[0] - fl) < 1.5 * u:
        start = 1 if o[0] > fl else -1
        for i, bh, bl, bc in b5:
            if m[i] < 15:
                continue
            if m[i] > late:
                break
            if (bc - fl) * start < -0.1 * u:
                side = -start
                stop = fl + start * 0.2 * u
                risk = abs(bc - stop)
                if risk > 0:
                    out["flip_cross"] = run(side, i, bc, stop, bc + side * 2 * risk)
                break
    # --- expiry pin
    if "pin" in rules and fin(mp) and lv.get("front_days") is not None and 0 <= lv["front_days"] <= 2:
        k = int(np.searchsorted(m, T / 2, side="right")) - 1
        if 0 <= k < len(c) - 5:
            dist = mp - c[k]
            if abs(dist) > 0.4 * u:
                side = 1 if dist > 0 else -1
                out["pin"] = run(side, k, c[k], c[k] - side * abs(dist), mp)
    return out


_BT: dict = {}


def backtest(md, symbol: str, commission: float = 2.5, slip_ticks: float = 1.0) -> dict:
    import time
    key = (symbol, commission, slip_ticks, len(md.daily[universe.get(symbol).data_symbol]))
    hit = _BT.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    out = _backtest(md, symbol, commission, slip_ticks)
    _BT[key] = (time.time(), out)
    return out


def _backtest(md, symbol: str, commission: float, slip_ticks: float) -> dict:
    c = universe.get(symbol)
    sym = c.data_symbol
    pv, tv = c.point_value, c.tick_value
    rt = 2 * (commission + slip_ticks * tv)
    df, src = intraday.bars(md, sym)
    sessions = ir.prepare_sessions(df, sym)
    dates = [s["date"] for s in sessions]
    lv, lsrc = history(md, sym, dates)
    daily = md.daily[sym].copy()
    daily.index = pd.to_datetime(daily["date"]).dt.normalize()
    sd = np.log(daily["close"]).diff().rolling(20, min_periods=10).std().shift(1)
    S = len(sessions)
    split = int(S * 0.6)
    per = {r: {"idx": [], "pnl": [], "side": []} for r in RULES}
    regime_rows = {"pos": [], "neg": []}
    for si, s in enumerate(sessions):
        ts = pd.Timestamp(s["date"])
        if ts not in lv.index or ts not in sd.index or not np.isfinite(sd.loc[ts]) or s["prev_close"] is None:
            continue
        row = {k: (None if pd.isna(v) else float(v)) for k, v in lv.loc[ts].items()}
        u = float(sd.loc[ts]) * s["prev_close"]
        # does the session behave as the regime says? range relative to a normal day
        rng = (s["h"].max() - s["l"].min()) / u
        pos = (row.get("gex") or 0) > 0 and (row.get("flip") is None or s["o"][0] > row["flip"])
        regime_rows["pos" if pos else "neg"].append(rng)
        for r, t in _session_trades(s, row, u).items():
            if t:
                per[r]["idx"].append(si); per[r]["side"].append(t[0]); per[r]["pnl"].append((t[2] - t[1]) * t[0] * pv - rt)
    rules_out = []
    all_idx, all_pnl = [], []
    for r, (name, fam) in RULES.items():
        idx, pnl, side = np.array(per[r]["idx"], int), np.array(per[r]["pnl"]), np.array(per[r]["side"])
        all_idx += list(idx); all_pnl += list(pnl)
        cum = np.cumsum(pnl) if len(pnl) else np.array([])
        rules_out.append({"key": r, "name": name, "family": fam, "all": ir._stats(pnl, tv), "is": ir._stats(pnl[idx < split], tv),
                          "oos": ir._stats(pnl[idx >= split], tv), "longs": ir._stats(pnl[side > 0], tv), "shorts": ir._stats(pnl[side < 0], tv),
                          "equity": [{"time": str(sessions[i]["date"]), "value": round(float(v), 0)} for i, v in zip(idx, cum)]})
    order = np.argsort(all_idx, kind="stable")
    comb = np.array(all_pnl)[order] if all_pnl else np.array([])
    comb_idx = np.array(all_idx, int)[order] if all_idx else np.array([], int)
    ccum = np.cumsum(comb) if len(comb) else np.array([])
    rg = lambda a: {"n": len(a), "avg_range_sd": round(float(np.mean(a)), 2) if a else None}  # noqa: E731
    return {"symbol": sym, "source": src, "levels_source": lsrc, "sessions": S, "rt_cost": round(rt, 2),
            "split": str(sessions[split]["date"]) if S > split else None, "rules": rules_out,
            "combined": {"all": ir._stats(comb, tv), "oos": ir._stats(comb[comb_idx >= split], tv) if len(comb) else {"n": 0},
                         "equity": [{"time": str(sessions[i]["date"]), "value": round(float(v), 0)} for i, v in zip(comb_idx, ccum)]},
            "regime_check": {"positive": rg(regime_rows["pos"]), "negative": rg(regime_rows["neg"])}}


def playbook(md, symbol: str, mode: str | None = None) -> dict:
    """Today's levels and the concrete orders each rule implies."""
    from .analytics import analyse
    chain, meta = chains.load(md, symbol)
    a = analyse(chain, meta, mode)
    sym = a["symbol"]
    c = universe.get(sym)
    rnd = lambda v: None if v is None else round(round(v / c.tick_size) * c.tick_size, 6)  # noqa: E731
    daily = md.daily[sym]
    u = float(np.log(daily["close"]).diff().tail(20).std() * daily["close"].iloc[-1])
    ses = md.sessions[sym]
    F = float(ses.last)
    cw, pw, fl = a["call_wall"], a["put_wall"], a["flip"]
    mp = (a["front"] or {}).get("max_pain")
    pos = a["regime"] == "POSITIVE" and (fl is None or F > fl)
    plays = []
    if pos:
        if cw:
            plays.append({"rule": "wall_fade", "side": "SHORT", "trigger": f"price tags {rnd(cw)} (call wall), then a 5-min bar closes below {rnd(cw - 0.05 * u)}",
                          "entry": rnd(cw - 0.05 * u), "stop": rnd(cw + 0.1 * u), "target": rnd(cw - 0.05 * u - 1.5 * 0.15 * u),
                          "distance_sd": round((cw - F) / u, 2), "why": "Dealers long gamma sell into strength near the biggest call strike."})
        if pw:
            plays.append({"rule": "wall_fade", "side": "LONG", "trigger": f"price tags {rnd(pw)} (put wall), then a 5-min bar closes above {rnd(pw + 0.05 * u)}",
                          "entry": rnd(pw + 0.05 * u), "stop": rnd(pw - 0.1 * u), "target": rnd(pw + 0.05 * u + 1.5 * 0.15 * u),
                          "distance_sd": round((pw - F) / u, 2), "why": "Dealers long gamma buy weakness near the biggest put strike."})
    else:
        if pw:
            plays.append({"rule": "wall_break", "side": "SHORT", "trigger": f"5-min close below {rnd(pw - 0.1 * u)} (through the put wall)",
                          "entry": rnd(pw - 0.1 * u), "stop": rnd(pw + 0.15 * u), "target": rnd(pw - 0.1 * u - 2 * 0.25 * u),
                          "distance_sd": round((pw - F) / u, 2), "why": "Short gamma: dealers sell as price falls, so a put-wall break can accelerate."})
        if cw:
            plays.append({"rule": "wall_break", "side": "LONG", "trigger": f"5-min close above {rnd(cw + 0.1 * u)} (through the call wall)",
                          "entry": rnd(cw + 0.1 * u), "stop": rnd(cw - 0.15 * u), "target": rnd(cw + 0.1 * u + 2 * 0.25 * u),
                          "distance_sd": round((cw - F) / u, 2), "why": "Short gamma: dealers buy as price rises; call-side squeezes run."})
    if fl:
        side = "SHORT" if F > fl else "LONG"
        lvl = fl - 0.1 * u if F > fl else fl + 0.1 * u
        plays.append({"rule": "flip_cross", "side": side, "trigger": f"5-min close {'below' if F > fl else 'above'} {rnd(lvl)} (through the flip)",
                      "entry": rnd(lvl), "stop": rnd(fl + (0.2 if F > fl else -0.2) * u),
                      "target": rnd(lvl + (-1 if F > fl else 1) * 2 * 0.3 * u), "distance_sd": round((fl - F) / u, 2),
                      "why": "Crossing the flip changes how dealers hedge: from damping moves to chasing them (or back)."})
    fd = (a["front"] or {}).get("days")
    if mp and fd is not None and fd <= 2 and abs(mp - F) > 0.4 * u:
        side = "LONG" if mp > F else "SHORT"
        plays.append({"rule": "pin", "side": side, "trigger": f"after mid-session, if still > {rnd(0.4 * u)} away from max pain",
                      "entry": rnd(F), "stop": rnd(F - (1 if mp > F else -1) * abs(mp - F)), "target": rnd(mp),
                      "distance_sd": round((mp - F) / u, 2), "why": f"Front expiry in {fd} day(s): gamma is concentrated at the big strikes."})
    plays.sort(key=lambda p: abs(p["distance_sd"]))
    return {"symbol": sym, "price": F, "source": a["source"], "mode": a["mode"], "regime": "POSITIVE" if pos else "NEGATIVE",
            "total_gex": a["total_gex"], "call_wall": cw, "put_wall": pw, "flip": fl, "max_pain": mp, "front_days": fd,
            "expected_move": (a["front"] or {}).get("expected_move"), "sd_day": rnd(u), "plays": plays,
            "range_hint": [rnd(pw), rnd(cw)] if pos and pw and cw else None}


_PB: dict = {}


def quick_levels(md, symbol: str) -> dict | None:
    """Cached (15 min) playbook levels for the signal engine."""
    import time
    hit = _PB.get(symbol)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    try:
        p = playbook(md, symbol)
        out = {k: p[k] for k in ("regime", "call_wall", "put_wall", "flip", "max_pain", "sd_day", "source")}
        if md.settings.live and p["source"] == "SIM":
            out = None                                   # never trade live on a synthetic option chain
    except Exception:  # noqa: BLE001
        out = None
    _PB[symbol] = (time.time(), out)
    return out
