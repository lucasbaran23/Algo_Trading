"""Day-Trade Insider: "what do I need to know today to day trade this market?"

Assembles one briefing per market from the market data, signal engine, news service
and prop account: key levels, volume profile, regime, expected range, catalysts,
ranked playbook and a risk plan, plus a short game plan written in plain language.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .. import universe
from . import analytics, calendar, playbook

ET = ZoneInfo("America/New_York")
MICRO = {"ES": "MES", "NQ": "MNQ", "YM": "MYM", "CL": "MCL", "GC": "MGC", "NG": "MNG"}
_BT_CACHE: dict[tuple, tuple[float, dict]] = {}

TIME_OF_DAY = {
    "Indices": ["09:30-09:45 ET: opening drive, the widest spreads and fastest moves. Let the opening range form unless trading the open itself.",
                "11:30-13:30 ET: lunch lull. Ranges compress and breakouts fail more often, so cut size or stand aside.",
                "15:00-16:00 ET: closing flows and MOC imbalances. Trends often extend into the close."],
    "Energy": ["09:00-09:30 ET: pit-era open, when volume steps up. Watch the first test of the overnight high/low.",
               "On EIA Wednesdays (NG Thursdays) 10:30 ET: no new positions 2 minutes before. Wait 5-15 minutes for the dust to settle.",
               "14:30 ET settlement: liquidity drops after it, so avoid holding into the thin post-settle trade."],
    "Metals": ["08:20-08:30 ET: COMEX open, often overlapping 08:30 macro data (CPI/NFP days are the big ones).",
               "10:00-11:30 ET: London afternoon / NY overlap, the most liquid window.",
               "13:30 ET settlement: after-hours gold is thinner and moves on dollar/yields."],
    "Grains": ["09:30 ET open: overnight news (weather, export sales) is priced in the first 15 minutes.",
               "12:00 ET on report days (WASDE, Acreage, Stocks): limit-type moves are possible. Be flat before.",
               "14:20 ET close: positions square into the close, so the last 15 minutes can be choppy."],
    "Softs": ["ICE softs open early (03:30-04:45 ET): much of the move happens in the European morning.",
              "Liquidity thins after 12:00 ET: use limit orders, as slippage is high in thin markets."],
    "Livestock": ["09:30 ET open: cash-trade expectations drive the morning.",
                  "Thin markets: use limit orders, and watch daily price limits on USDA report days."],
}


def _round_step(price: float) -> float:
    mag = 10 ** math.floor(math.log10(max(price, 1e-9)))
    for f in (0.5, 1.0, 2.5, 5.0):
        if price / (f * mag) < 40:
            return f * mag / 10 if price / (f * mag) < 4 else f * mag
    return mag


def _bt(sym: str, df: pd.DataFrame, c, cost: float) -> dict:
    key = (sym, len(df), str(df["date"].iloc[-1]))
    hit = _BT_CACHE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    res = playbook.backtest_setups(df, c.point_value, c.tick_size, cost)
    _BT_CACHE[key] = (time.time(), res)
    return res


def build(hub, symbol: str, account=None) -> dict:
    acct = account if account is not None else hub.prop     # whose prop account sizes the plan
    c = universe.get(symbol)
    sym = c.data_symbol
    md, snap = hub.md, hub.engine.snapshots[sym]
    tick, tv = c.tick_size, c.tick_value
    rnd = lambda v: round(round(v / tick) * tick, 6)  # noqa: E731

    # ------------------------------------------------------------------ today vs prior session
    df = md.daily[sym].copy().reset_index(drop=True)
    ses = md.sessions[sym]
    today = df.iloc[-1]
    prev = df.iloc[-2]
    t_open = float(today.open)
    t_high = max(float(today.high), ses.high)
    t_low = min(float(today.low), ses.low)
    last = ses.last
    df.loc[df.index[-1], ["high", "low", "close"]] = [t_high, t_low, last]
    week = df.iloc[-6:-1]
    atr = float(snap["levels"]["atr"])
    now_et = datetime.now(ET)
    day = calendar.next_session_date(now_et)
    s_open, s_close = calendar.session_window(c.sector, sym)
    hhmm = now_et.strftime("%H:%M")
    live_day = day == now_et.date()
    status = ("OPEN" if live_day and s_open <= hhmm < s_close else
              "PRE-MARKET" if live_day and hhmm < s_open else "CLOSED")

    # ------------------------------------------------------------------ analytics
    reg = analytics.regime(df.iloc[:-1])
    rng = analytics.ranges(df, t_high, t_low, t_open, last)
    prof = analytics.volume_profile(df, tick)
    wd = analytics.weekday_stats(df.iloc[:-1], day.weekday()) if day.weekday() < 5 else {}
    mon = analytics.month_stats(df.iloc[:-1], day.month)
    lv = snap["levels"]

    # ------------------------------------------------------------------ level ladder
    step = _round_step(last)
    rn_dn = math.floor(last / step) * step
    raw_levels = [
        (float(prev.high), "Prior-day high (PDH)", "pd"), (float(prev.low), "Prior-day low (PDL)", "pd"),
        (float(prev.close), "Prior-day close / settle", "pd"), (t_open, "Today's open", "session"),
        (t_high, "Today's high", "session"), (t_low, "Today's low", "session"),
        (float(lv["vwap"]), "VWAP", "vwap"),
        (lv["r2"], "R2", "pivot"), (lv["r1"], "R1", "pivot"), (lv["pivot"], "Pivot", "pivot"),
        (lv["s1"], "S1", "pivot"), (lv["s2"], "S2", "pivot"),
        (float(week["high"].max()), "Prior 5-day high", "swing"), (float(week["low"].min()), "Prior 5-day low", "swing"),
        (float(df["high"].iloc[-21:-1].max()), "20-day high", "swing"), (float(df["low"].iloc[-21:-1].min()), "20-day low", "swing"),
        (rn_dn, "Round number", "round"), (rn_dn + step, "Round number", "round"),
    ]
    if prof:
        raw_levels += [(prof["poc"], "Volume POC (20d)", "profile"), (prof["vah"], "Value-area high", "profile"),
                       (prof["val"], "Value-area low", "profile")]
    opt = None
    try:
        from ..options import analytics as oa, chains as och
        if sym in och.SUPPORTED:
            ch, meta = och.load(md, sym)
            opt = oa.analyse(ch, meta)
            if md.settings.live and meta["source"] == "SIM":
                raise LookupError("synthetic chain: don't put made-up option levels on a live ladder")
            for k, name in (("call_wall", "Options call wall"), ("put_wall", "Options put wall"), ("flip", "Gamma flip")):
                if opt.get(k):
                    raw_levels.append((opt[k], name, "options"))
            if opt["front"].get("max_pain"):
                raw_levels.append((opt["front"]["max_pain"], "Max pain (front expiry)", "options"))
    except Exception:  # noqa: BLE001
        opt = None
    seen, levels = set(), []
    for p, name, kind in raw_levels:
        p = rnd(p)
        if (p, name) in seen or abs(p - last) > 2.5 * atr:
            continue
        seen.add((p, name))
        levels.append({"price": p, "label": name, "kind": kind, "side": "above" if p > last else "below",
                       "dist_ticks": int(round((p - last) / tick)), "dist_atr": round((p - last) / atr, 2) if atr else 0})
    levels.sort(key=lambda x: -x["price"])
    above = [x for x in levels if x["price"] > last]
    below = [x for x in levels if x["price"] <= last]
    r_near = above[-1] if above else None
    s_near = below[0] if below else None

    # ------------------------------------------------------------------ playbook
    cost = acct.rules.commission_per_side
    bt = _bt(sym, df, c, cost)
    ranked = playbook.rank(bt, reg["kind"], tv)
    x = playbook._ctx(df.iloc[:-1].reset_index(drop=True))
    tsetups = playbook.todays_setups(prev, t_open, atr, float(x["sma20"][-1]) if len(x["sma20"]) else last,
                                     float(x["sma20_prev"][-1]) if len(x["sma20_prev"]) else last, tick,
                                     bool((prev.high <= df.iloc[-3].high) and (prev.low >= df.iloc[-3].low)),
                                     bool((prev.high - prev.low) <= (df["high"] - df["low"]).iloc[-8:-1].min() + 1e-12))
    for r in ranked:
        r["today"] = tsetups.get(r["key"], {})

    # ------------------------------------------------------------------ catalysts
    events = calendar.for_market(day, sym)
    hol = calendar.holiday(now_et.date()) if not live_day else None
    news = [it for it in hub.news.query(sym, min_stars=2, limit=20) if time.time() - it["ts"] < 36 * 3600][:6]
    big_event = any(e["stars"] == 3 for e in events)

    # ------------------------------------------------------------------ risk plan
    vol = snap["detail"]["vol"]
    vp = vol["percentile"] or 50
    mult, why = 1.0, []
    if vp >= 80:
        mult = 0.5
        why.append(f"Volatility is HIGH ({vp:.0f}th percentile of the year): half size, wider stops")
    elif vp >= 60:
        mult = 0.75
        why.append(f"Volatility is elevated ({vp:.0f}th pct): trade 3/4 size")
    elif vp <= 20:
        why.append(f"Volatility is LOW ({vp:.0f}th pct): smaller ranges, so take profits sooner and expect fewer follow-through breakouts")
    if big_event:
        mult *= 0.75
        why.append("★★★ scheduled event today: size down 25% and be flat into the release")
    if reg["kind"] == "transition":
        mult *= 0.8
        why.append("No clear regime: size down 20% until the market picks a direction")
    if rng["adr_used_pct"] >= 100:
        why.append(f"Today has already traded {rng['adr_used_pct']:.0f}% of its average range: late-day continuation odds drop, so favour mean reversion")
    stop_ticks = max(4, int(round(0.25 * rng["adr20"] / tick)))
    st = acct.status(hub.prices())
    r = acct.rules
    budgets = [st["distance_to_breach"] * 0.15]
    if st["daily_loss_remaining"] is not None:
        budgets.append(st["daily_loss_remaining"] * 0.25)
    risk_budget = max(0.0, min(budgets)) * mult
    per_ct = stop_ticks * tv + 2 * (cost + tv)
    contracts = int(risk_budget // per_ct) if per_ct else 0
    micro_sym = MICRO.get(sym)
    micro = None
    if micro_sym:
        m = universe.get(micro_sym)
        per_m = stop_ticks * m.tick_value + 2 * (cost + m.tick_value)
        micro = {"symbol": micro_sym, "contracts": int(risk_budget // per_m) if per_m else 0, "risk_per_contract": round(per_m, 2)}
    daily_stop = round(min(r.daily_loss_limit * 0.5, st["distance_to_breach"] * 0.4) if r.daily_loss_limit else st["distance_to_breach"] * 0.4, 0)
    consistency_cap = round(r.consistency_pct / 100 * r.profit_target, 0) if r.consistency_pct and r.profit_target else None
    risk = {
        "size_multiplier": round(mult, 2), "reasons": why or ["Normal conditions: standard size"],
        "stop_ticks": stop_ticks, "stop_points": rnd(stop_ticks * tick), "stop_usd": round(stop_ticks * tv, 2),
        "target_ticks": stop_ticks * 2, "risk_budget": round(risk_budget, 0), "risk_per_contract": round(per_ct, 2),
        "contracts": contracts, "micro": micro,
        "use_micro": bool(micro and contracts < 1 and micro["contracts"] >= 1),
        "daily_stop": daily_stop, "max_trades": 3 if big_event or vp >= 80 else 4,
        "consistency_cap": consistency_cap,
        "rules": [
            f"Max risk per trade: ${risk_budget:,.0f} (25% of today's remaining loss allowance × size multiplier {mult:.2f})",
            f"Stop {stop_ticks} ticks (≈25% of the average daily range); first target 2R = {stop_ticks * 2} ticks",
            f"Walk away for the day at -${daily_stop:,.0f} or after 2 losses in a row",
            f"Max {3 if big_event or vp >= 80 else 4} trades today: overtrading is the #1 way evaluations are failed",
        ] + ([f"Consistency rule: stop trading for the day once up ~${consistency_cap:,.0f} ({r.consistency_pct:.0f}% of the ${r.profit_target:,.0f} target)"]
             if consistency_cap else []),
        "events": [f"{e['time']} ET {e['name']}: no new entries 2 min before; wait 5-15 min after for spreads to normalise"
                   for e in events if e["stars"] == 3 and e["time"]],
        "time_of_day": TIME_OF_DAY.get(c.sector, []),
        "account": {"name": r.name, "today_pnl": st["today_pnl"], "daily_loss_remaining": st["daily_loss_remaining"],
                    "distance_to_breach": st["distance_to_breach"]},
    }

    # ------------------------------------------------------------------ bias & game plan
    day_sc, swing_sc = snap["day"]["score"], snap["swing"]["score"]
    above_vwap = last > lv["vwap"]
    bias = ("LONG" if day_sc >= 20 and above_vwap else "SHORT" if day_sc <= -20 and not above_vwap else
            "LEAN LONG" if day_sc > 0 else "LEAN SHORT" if day_sc < 0 else "NEUTRAL")
    top = next((p for p in ranked if p["verdict"] in ("PREFERRED", "WORKS")), ranked[0])
    tick_dec = len(f"{tick:.10f}".rstrip("0").split(".")[1])          # 0.25 -> 2, 0.005 -> 3, 1.0 -> 0
    dp = lambda v: f"{rnd(v):,.{tick_dec}f}"  # noqa: E731
    plan = [
        f"{sym} is {reg['label'].lower()} (ADX {reg['adx']:.0f}, efficiency {reg['efficiency']:.2f}). {reg['tip']}",
        f"Bias: {bias}. Price is {'above' if above_vwap else 'below'} VWAP {dp(lv['vwap'])} and "
        f"{'above' if last > prev.high else 'below' if last < prev.low else 'inside'} yesterday's range "
        f"({dp(prev.low)}–{dp(prev.high)}). Day score {day_sc:+.0f}, swing score {swing_sc:+.0f}.",
        (f"Nearest resistance {dp(r_near['price'])} ({r_near['label']}, {r_near['dist_ticks']} ticks); "
         if r_near else "") + (f"nearest support {dp(s_near['price'])} ({s_near['label']}, {abs(s_near['dist_ticks'])} ticks)." if s_near else ""),
        f"Normal day = {dp(rng['adr20'])} range ({rng['adr20'] / tick:.0f} ticks). Used so far: {rng['adr_used_pct']:.0f}%. "
        f"If today is average: high ≈ {dp(rng['proj_high'])}, low ≈ {dp(rng['proj_low'])}.",
        (f"Catalyst: {', '.join(e['time'] + ' ' + e['name'] for e in events if e['stars'] >= 2)}." if any(e['stars'] >= 2 for e in events)
         else "No tier-1 scheduled report for this market today: technical levels should dominate."),
        (f"Best-fit setup for this regime: {top['name']} ({top['verdict'].lower()}: "
         f"{top[top['used']].get('win_pct', 0):.0f}% wins, {top[top['used']].get('avg', 0):+,.0f} $/trade over {top[top['used']].get('n', 0)} trades)."
         if top["verdict"] in ("PREFERRED", "WORKS") else
         "No playbook setup has shown a reliable edge in this regime for this market. Best option: trade very small, "
         "trade a different market, or sit out. Not trading is a position."),
        f"Risk: {risk['reasons'][0]}. Plan {contracts} {sym}" + (f" or {micro['contracts']} {micro_sym}" if micro else "")
        + f" with a {stop_ticks}-tick stop.",
    ]
    # ------------------------------------------------------------------ top-down + intraday research summary
    from ..intraday import research as ir, topdown as td
    try:
        tdn = td.topdown(md, symbol)
    except Exception:  # noqa: BLE001 - the briefing must not fail because of the intraday layer
        tdn = None
    try:
        res = ir.research(md, symbol, cost)
        ok = [x for x in res["strategies"] if x["verdict"] in ("EDGE", "PROMISING") and x["grade"] != "C"]
        research_line = (f"Intraday research ({res['source']} 1-min data): best validated strategy {ok[0]['name']} on the "
                         f"{ok[0]['best_tf']}-minute setting ({ok[0]['verdict'].lower()}, OOS t {ok[0]['oos'].get('t_stat', 0):.1f})."
                         if ok else f"Intraday research ({res['source']} 1-min data): no research strategy passes out-of-sample "
                         f"validation on this market's data. Lean on levels and discipline, not a system.")
    except Exception:  # noqa: BLE001
        res, research_line = None, ""
    if tdn:
        plan.insert(1, f"Top-down: {tdn['setup']} (alignment {tdn['alignment']:+.0f}). {tdn['plan']} {tdn['exec_note']}")
    natgas = None
    if sym == "NG":                                   # the MVP market gets its contract / seasonality / weather read
        try:
            from ..natgas import seasonality as ngs, weather as ngw
            natgas = ngs.outlook(md, hub.md.settings, now_et)
            wxs = ngw.weather(md, hub.md.settings)
            plan[0:0] = natgas["bullets"][:3] + [f"Weather ({wxs['source']}): {wxs['bias']}. {wxs['bias_why']}"]
        except Exception:  # noqa: BLE001
            natgas = None
    if research_line:
        plan.append(research_line)
    if opt:
        plan.insert(2 if sym == "NG" else 1,
                    f"Options ({opt['source']}): dealers modelled {opt['regime'].lower()} gamma ({opt['mode'].replace('_', ' ')}); "
                    + (f"flip {opt['flip']}, " if opt["flip"] else "") + f"call wall {opt['call_wall']}, put wall {opt['put_wall']}. "
                    + ("Expect pinning and mean reversion." if opt["regime"] == "POSITIVE" else "Moves can accelerate: respect breakouts.")
                    + (f" Front straddle ±{opt['front']['expected_move']} by {opt['front']['expiry']}." if opt.get("front") else ""))
    if sym == "NG":
        try:
            from ..natgas.dynamics import quick_state
            qs = quick_state(md, hub.md.settings)
            if qs["policy"]:
                plan.insert(1, "WINTER LONG BIAS: prefer longs. Shorts intraday only, at half size, with a warm-forecast or "
                               "storage confirmation. Upside spikes are bigger than drops (call skew)."
                            + (f" Forecast revision since the last run: {qs['rev_hdd']:+.0f} HDD." if qs["rev_hdd"] is not None else ""))
        except Exception:  # noqa: BLE001
            pass

    checklist = [
        "Mark PDH / PDL / settle and the value area on your chart",
        "Know today's report times, and be flat before ★★★ releases",
        "Let the first 5-15 minutes set the opening range before committing",
        "Is price accepting above or below VWAP? Trade with it, not against it",
        f"Size: {max(contracts, 0)} {sym}" + (f" / {micro['contracts']} {micro_sym}" if micro else "") + f", stop {stop_ticks} ticks",
        f"Daily max loss ${daily_stop:,.0f}: platform loss limit set?",
        "Review: did I follow the plan? Log the trades",
    ]

    return {
        "symbol": symbol, "data_symbol": sym, "name": c.name, "sector": c.sector, "tick": tick, "tick_value": tv,
        "prepared_for": day.isoformat(), "weekday": day.strftime("%A"), "status": status,
        "now_et": now_et.strftime("%a %H:%M ET"), "holiday": hol,
        "session": {"open": s_open, "close": s_close},
        "sources": md.sources.get(sym, {}),
        "price": {"last": last, "chg": snap["quote"]["chg"], "chg_pct": snap["quote"]["chg_pct"],
                  "today_open": t_open, "today_high": t_high, "today_low": t_low, "vwap": lv["vwap"],
                  "gap": t_open - float(prev.close), "gap_atr": round((t_open - float(prev.close)) / atr, 2) if atr else 0},
        "prev": {"open": float(prev.open), "high": float(prev.high), "low": float(prev.low), "close": float(prev.close),
                 "range": float(prev.high - prev.low), "date": str(pd.Timestamp(prev.date).date())},
        "bias": bias, "day_score": day_sc, "swing_score": swing_sc,
        "regime": reg, "ranges": rng, "profile": prof, "levels": levels,
        "seasonality": {"weekday": wd, "month": mon, "window_20d": snap["detail"]["seasonality"],
                        "p_up_5d": snap["forecast"]["horizons"]["5"]["p_up"]},
        "vol": vol, "cot": {"state": snap["cot"]["state"], "index": snap["cot"]["mm_index_3y"]},
        "events": events, "news": news,
        "playbook": {"rows": ranked, "days": bt["days"], "from": bt["first_date"], "to": bt["last_date"],
                     "rt_cost": round(bt["rt_cost"], 2), "regime": reg["kind"]},
        "risk": risk, "plan": plan, "checklist": checklist,
        "topdown": {k: tdn[k] for k in ("rows", "alignment", "setup", "exec_tf")} if tdn else None,
        "natgas": {k: natgas[k] for k in ("contract", "expected", "factors")} if natgas else None,
    }
