"""Setup detectors: turn the terminal's analytics into concrete trade plans.

Each detector looks at one market and may return trade-plan dicts with:
  setup, name, style, timeframe, horizon, grade, side (1 / -1), entry_type (stop / limit / market),
  entry, stop, tp1, tp2, expires_in (seconds), reasons [..], group (OCO siblings share one).
Prices are rounded to the tick. Risk sizing, confidence and lifecycle live in the manager.

Evidence grades follow the research screen: A = peer-reviewed and replicated, B = published
but narrower or contested, C = practitioner rule (useful structure, no proven edge).
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from ..daytrade import calendar as cal

ET = ZoneInfo("America/New_York")


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _plan(ctx, setup, name, style, timeframe, horizon, grade, side, entry_type, entry, stop, tp1=None, tp2=None,
          expires_in=4 * 3600, reasons=(), group=None, r_mult=(1.0, 2.0)):
    rnd = ctx["rnd"]
    entry, stop = rnd(entry), rnd(stop)
    risk = abs(entry - stop)
    if risk < 2 * ctx["tick"]:
        return None
    last = ctx["last"]
    # The plan must be live when issued: price between the stop and the entry, on the correct side.
    if entry_type == "stop" and not ((entry - last) * side > 0 and (last - stop) * side > 0):
        return None
    if entry_type == "limit" and not ((last - entry) * side > 0):
        return None
    tp1 = rnd(tp1 if tp1 is not None else entry + side * r_mult[0] * risk)
    tp2 = rnd(tp2 if tp2 is not None else entry + side * r_mult[1] * risk)
    if (tp1 - entry) * side <= 0 or (tp2 - tp1) * side < 0:
        return None
    return {"setup": setup, "name": name, "style": style, "timeframe": timeframe, "horizon": horizon, "grade": grade,
            "side": side, "entry_type": entry_type, "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
            "expires_in": expires_in, "reasons": list(reasons), "group": group}


def detect(ctx: dict) -> list[dict]:
    """ctx: symbol, sector, last, tick, rnd, adr, atr, levels, session, regime, day_score, swing_score,
    swing_bias, cot_state, news (score dict or None), news_items (recent ★★★ for this market), now_et."""
    out: list[dict] = []
    last, adr, lv, ses, reg = ctx["last"], ctx["adr"], ctx["levels"], ctx["session"], ctx["regime"]
    if not adr or adr <= 0:
        return out
    trend_dir = 1 if reg["direction"] == "UP" else -1 if reg["direction"] == "DOWN" else 0
    day = ctx["day_score"]

    # 1. Prior-day high/low breakout (trend days): stop order beyond yesterday's extreme, in the trend's direction
    if reg["kind"] == "trend" and trend_dir:
        lvl = lv["prev_high"] if trend_dir > 0 else lv["prev_low"]
        untouched = (ses["high"] < lvl) if trend_dir > 0 else (ses["low"] > lvl)
        if untouched and abs(lvl - last) <= 0.6 * adr:
            e = lvl + trend_dir * ctx["tick"]
            p = _plan(ctx, "pdh_pdl", "Prior-day high/low breakout", "Breakout", "15m", "Intraday", "C", trend_dir, "stop",
                      e, e - trend_dir * 0.3 * adr,
                      reasons=[f"Market is {reg['label'].lower()} (ADX {reg['adx']:.0f}): breakouts tend to follow through",
                               f"Yesterday's {'high' if trend_dir > 0 else 'low'} {ctx['rnd'](lvl)} not yet taken today",
                               "Stop 0.3 × average daily range beyond the entry; take half at 1R, trail the rest to 2R"])
            if p:
                out.append(p)

    # 2. VWAP pullback in a trend: limit order back at VWAP when price is extended in the trend direction
    vw = lv["vwap"]
    if reg["kind"] == "trend" and trend_dir and trend_dir * day > 15:
        ext = (last - vw) * trend_dir
        if 0.12 * adr < ext < 0.6 * adr:
            p = _plan(ctx, "vwap_pullback", "VWAP pullback in trend", "Trend", "5m", "Intraday", "C", trend_dir, "limit",
                      vw, vw - trend_dir * 0.25 * adr,
                      tp1=(ses["high"] if trend_dir > 0 else ses["low"]),
                      expires_in=2 * 3600,
                      reasons=[f"Trend {reg['direction'].lower()} and intraday score {day:+.0f} agree",
                               f"Price is {ext / adr:.2f} ADR {'above' if trend_dir > 0 else 'below'} VWAP: wait for the pullback, don't chase",
                               "First target: retest of today's extreme"])
            if p and (p["tp1"] - p["entry"]) * trend_dir >= 0.6 * abs(p["entry"] - p["stop"]):
                out.append(p)

    # 3. Range fade at S1 / R1 (range-bound markets): limit order at the nearer edge, target the pivot
    if reg["kind"] == "range":
        s1, r1, pv = lv["s1"], lv["r1"], lv["pivot"]
        if s1 < last < r1:
            if last - s1 <= r1 - last:
                p = _plan(ctx, "range_fade", "Range fade at S1", "Reversion", "15m", "Intraday", "C", 1, "limit",
                          s1, s1 - 0.5 * (pv - s1), tp1=pv, tp2=r1,
                          reasons=[f"Range-bound market (ADX {reg['adx']:.0f}, choppiness {reg['chop']:.0f}): fade the edges",
                                   f"Buy S1 {ctx['rnd'](s1)}, target the pivot {ctx['rnd'](pv)}, then R1"])
            else:
                p = _plan(ctx, "range_fade", "Range fade at R1", "Reversion", "15m", "Intraday", "C", -1, "limit",
                          r1, r1 + 0.5 * (r1 - pv), tp1=pv, tp2=s1,
                          reasons=[f"Range-bound market (ADX {reg['adx']:.0f}, choppiness {reg['chop']:.0f}): fade the edges",
                                   f"Sell R1 {ctx['rnd'](r1)}, target the pivot {ctx['rnd'](pv)}, then S1"])
            if p:
                out.append(p)

    # 4. Opening range breakout (15-minute range of the current session), both sides as OCO
    bars = ses["bars"]
    if ctx["sector"] in ("Indices", "Energy", "Metals") and len(bars) >= 16:
        first = bars[:15]
        orh, orl = max(b[2] for b in first), min(b[3] for b in first)
        rng = orh - orl
        later = bars[15:]
        broken = any(b[2] > orh or b[3] < orl for b in later)
        if not broken and 0.05 * adr < rng < 0.6 * adr:
            grp = f"orb:{ctx['symbol']}:{int(first[0][0])}"
            stop_d = min(rng, 0.35 * adr)
            for side, lvl in ((1, orh), (-1, orl)):
                e = lvl + side * ctx["tick"]
                p = _plan(ctx, "orb", "Opening range breakout (15m)", "Breakout", "15m", "Intraday", "B", side, "stop",
                          e, e - side * stop_d, group=grp,
                          reasons=[f"15-minute opening range {ctx['rnd'](orl)}–{ctx['rnd'](orh)} ({rng / adr:.2f} ADR)",
                                   "Research: ORB studied in crude oil futures and U.S. stocks; costs eat much of the edge",
                                   "One side only: the other order is cancelled when this one fills (OCO)"])
                if p:
                    out.append(p)

    # 5. Market intraday momentum into the close (last 30 minutes of the regular session)
    now = ctx["now_et"]
    if ctx["sector"] in ("Indices", "Energy", "Metals") and now.weekday() < 5:
        o_s, c_s = (_hm(x) for x in cal.session_window(ctx["sector"], ctx["symbol"]))
        mins = now.hour * 60 + now.minute
        if c_s - 30 <= mins < c_s - 25 and ctx["prev_close"]:
            r = last / ctx["prev_close"] - 1
            if abs(r) > 0.0005:
                side = 1 if r > 0 else -1
                p = _plan(ctx, "intraday_momentum", "Intraday momentum into the close", "Momentum", "30m", "Intraday", "A",
                          side, "market", last, last - side * 0.15 * adr, expires_in=30 * 60, r_mult=(1.0, 1.5),
                          reasons=[f"Day is {'up' if side > 0 else 'down'} {r * 100:+.2f}% since yesterday's close",
                                   "Research (Gao et al. 2018; Baltussen et al. 2021): the last half-hour tends to follow the day's direction",
                                   "Exit by the close whatever happens"])
                if p:
                    out.append(p)

    # 6. Swing trend with fundamental confirmation (daily)
    bias = ctx["swing_bias"]
    if bias in ("STRONG BUY", "STRONG SELL"):
        side = 1 if bias == "STRONG BUY" else -1
        crowded = (side > 0 and ctx["cot_state"] == "CROWDED LONG") or (side < 0 and ctx["cot_state"] == "CROWDED SHORT")
        if not crowded:
            atr = ctx["atr"]
            p = _plan(ctx, "swing_trend", "Swing trend + fundamentals", "Swing", "Daily", "Swing (days)", "A", side, "limit",
                      last - side * 0.25 * atr, last - side * 2.25 * atr, r_mult=(1.0, 2.0), expires_in=3 * 86400,
                      reasons=[f"Swing composite {ctx['swing_score']:+.0f} ({bias}): trend, COT and supply/demand agree",
                               f"Speculators not crowded on this side (COT: {ctx['cot_state'].lower()})",
                               "Research: time-series momentum is one of the best-documented futures effects (Moskowitz, Ooi & Pedersen 2012)",
                               "Enter on a 0.25-ATR pullback; 2-ATR stop"])
            if p:
                out.append(p)

    # 7b. Natural gas: colder forecast revision in winter (weather-driven long)
    ng = ctx.get("natgas")
    if ctx["symbol"] == "NG" and ng and ng["policy"] and (ng["rev_hdd"] or 0) >= 10 and day >= -10:
        vw = lv["vwap"]
        entry_type, e = ("limit", vw) if last > vw + 0.05 * adr else ("market", last)
        p = _plan(ctx, "ng_weather", "Cold forecast revision (winter long)", "Weather", "15m", "Intraday", "B", 1, entry_type,
                  e, e - 0.3 * adr, expires_in=3 * 3600,
                  reasons=[f"+{ng['rev_hdd']:.0f} HDD added to the 14-day forecast since the previous run: more heating demand",
                           f"Next 14 days {ng['next14_pct']:+.0f}% vs normal; winter call skew means colder revisions move price harder than warmer ones",
                           "Winter policy: longs preferred ('long and wrong beats short and dead')"])
        if p:
            out.append(p)

    # 8. Sigma-extreme fade: the move from yesterday's close is >= 2.5 sigma for this month and time of day,
    #    and a 5-minute bar has just closed back beyond the previous 5-minute bar (confirmation)
    sg = ctx.get("sigma")
    if sg and sg["in_window"] and abs(sg["z"]) >= 2.5 and len(ses["bars"]) >= 10:
        side = -1 if sg["z"] > 0 else 1
        b = ses["bars"][-10:]
        prev5, last5 = b[:5], b[5:]
        ph, pl = max(x[2] for x in prev5), min(x[3] for x in prev5)
        conf = (side > 0 and last5[-1][4] > ph) or (side < 0 and last5[-1][4] < pl)
        if conf:
            ext = ses["low"] if side > 0 else ses["high"]
            u = sg["sd_px"]
            p = _plan(ctx, "sigma_fade", f"{abs(sg['z']):.1f}σ extreme fade", "Reversion", "5m", "Intraday", "C", side, "market",
                      last, ext - side * 0.1 * u, tp1=ext + side * 0.5 * abs(sg["pc"] - ext), tp2=sg["pc"], expires_in=90 * 60,
                      reasons=[f"Move from yesterday's close is {sg['z']:+.1f}σ vs this month's history at this time of day",
                               f"Confirmation: the last 5-minute bar closed {'above' if side > 0 else 'below'} the previous one's {'high' if side > 0 else 'low'}",
                               "Stop beyond today's extreme; first target halfway back to yesterday's close, second at yesterday's close",
                               "Evidence: mixed. Extreme moves can keep going (news, weather). Check the backtest on the LEVELS & σ tab"])
            if p:
                out.append(p)

    # 10. High-volatility push into a key level + RSI divergence on 5-minute bars, confirmed
    vd = ctx.get("voldiv")
    if vd:
        side, u = vd["side"], vd["sd_px"]
        p = _plan(ctx, "vol_div", f"Vol push + {vd['level']} + RSI divergence", "Reversal", "5m", "Intraday", "C", side, "market",
                  last, vd["ext"] - side * 0.1 * u, r_mult=(1.0, 2.0), expires_in=90 * 60,
                  reasons=[f"Fast {'drop' if side > 0 else 'rally'} into the {vd['level']} ({ctx['rnd'](vd['level_px'])}): {abs(vd['z']):.1f}σ leg",
                           f"RSI {'bullish' if side > 0 else 'bearish'} divergence: new {'low' if side > 0 else 'high'} with RSI {vd['rsi_now']:.0f} vs {vd['rsi_prior']:.0f} at the earlier swing",
                           f"Confirmed: a 5-minute close {'above' if side > 0 else 'below'} the divergence bar",
                           "Stop beyond the extreme; take half at 1R, the rest at 2R or VWAP"])
        if p:
            out.append(p)

    # 9. Options levels: wall fade (dealers long gamma), wall break / flip cross (short gamma)
    ol = ctx.get("optlv")
    if ol and ol.get("sd_day"):
        u = ol["sd_day"]
        cw, pw = ol.get("call_wall"), ol.get("put_wall")
        if ol["regime"] == "POSITIVE":
            if cw and cw - 0.05 * u < last < cw + 0.1 * u:
                p = _plan(ctx, "opt_wall", "Call-wall fade (long gamma)", "Options levels", "5m", "Intraday", "C", -1, "stop",
                          cw - 0.05 * u, max(ses["high"], cw) + 0.1 * u, r_mult=(1.0, 1.5), expires_in=2 * 3600,
                          reasons=[f"Price is at the call wall {ctx['rnd'](cw)}; dealers are modelled long gamma, so they sell strength here",
                                   "Sell only if a bar closes back below the wall (the stop order is the confirmation)",
                                   f"Levels from {ol['source']}"])
                if p:
                    out.append(p)
            if pw and pw - 0.1 * u < last < pw + 0.05 * u:
                p = _plan(ctx, "opt_wall", "Put-wall fade (long gamma)", "Options levels", "5m", "Intraday", "C", 1, "stop",
                          pw + 0.05 * u, min(ses["low"], pw) - 0.1 * u, r_mult=(1.0, 1.5), expires_in=2 * 3600,
                          reasons=[f"Price is at the put wall {ctx['rnd'](pw)}; dealers are modelled long gamma, so they buy weakness here",
                                   "Buy only if a bar closes back above the wall", f"Levels from {ol['source']}"])
                if p:
                    out.append(p)
        else:
            if pw and pw - 0.1 * u < last < pw + 0.15 * u:
                p = _plan(ctx, "opt_wall", "Put-wall break (short gamma)", "Options levels", "5m", "Intraday", "C", -1, "stop",
                          pw - 0.1 * u, pw + 0.15 * u, r_mult=(1.0, 2.0), expires_in=2 * 3600,
                          reasons=[f"Dealers are modelled short gamma: a break of the put wall {ctx['rnd'](pw)} can make them sell more",
                                   f"Levels from {ol['source']}"])
                if p:
                    out.append(p)
            if cw and cw - 0.15 * u < last < cw + 0.1 * u:
                p = _plan(ctx, "opt_wall", "Call-wall break (short gamma)", "Options levels", "5m", "Intraday", "C", 1, "stop",
                          cw + 0.1 * u, cw - 0.15 * u, r_mult=(1.0, 2.0), expires_in=2 * 3600,
                          reasons=[f"Dealers are modelled short gamma: a break above the call wall {ctx['rnd'](cw)} can force them to buy",
                                   f"Levels from {ol['source']}"])
                if p:
                    out.append(p)

    # 7. News catalyst: a fresh ★★★ headline whose direction agrees with the intraday tape
    for it in ctx["news_items"]:
        side = {"LONG": 1, "SHORT": -1}.get(it["direction"], 0)
        if side and side * day >= 0:
            p = _plan(ctx, "news", "News catalyst (★★★)", "Event", "5m", "Intraday", "B", side, "market",
                      last, last - side * 0.3 * adr, expires_in=2 * 3600,
                      reasons=[f"★★★ {it['title'][:110]}", f"Headline is {it['direction']} for {ctx['symbol']} and the intraday score ({day:+.0f}) doesn't disagree",
                               "Event-driven: moves can reverse fast, so take half at 1R"])
            if p:
                out.append(p)
            break
    return out


def now_et() -> datetime:
    return datetime.now(ET)


def adr(daily, n: int = 20) -> float:
    d = daily.iloc[:-1].tail(n)
    v = float((d["high"] - d["low"]).mean())
    return v if math.isfinite(v) else 0.0
