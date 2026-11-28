"""Deterministic risk tools for the Prop Copilot.

All numbers the copilot quotes come from these functions, never from the language model's own
arithmetic: account buffers, sizing, rule checks, the daily plan and the pass-probability simulation.
Each returns a compact JSON-able dict (kept small to save tokens).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .. import universe
from ..prop.rules import PropRules

FIRMS_PATH = Path(__file__).resolve().parents[2] / "data" / "firms.json"
MICRO = {"ES": "MES", "NQ": "MNQ", "YM": "MYM", "CL": "MCL", "GC": "MGC", "NG": "MNG"}


def firms() -> dict:
    return json.loads(FIRMS_PATH.read_text())["firms"]


def firm_rules(firm: str | None = None) -> dict:
    db = firms()
    if not firm:
        return {"firms": {k: {"name": v["name"], "plans": list(v["plans"])} for k, v in db.items()}}
    key = firm.lower().replace(" ", "").replace("-", "")
    hit = next((k for k in db if k in key or key in k or key in db[k]["name"].lower().replace(" ", "")), None)
    if not hit:
        return {"error": f"unknown firm '{firm}'", "known": list(db)}
    f = db[hit]
    return {"firm": hit, "name": f["name"], "asset_class": f["asset_class"], "source": f["source"], "general_rules": f["general"],
            "plans": f["plans"], "caution": "Templates are unverified unless verified=true: confirm on the firm's site."}


def rules_for(firm: str, plan: str) -> PropRules:
    f = firms()[firm]
    p = f["plans"][plan]
    return PropRules(name=f"{f['name']} {plan}", account_size=p["account_size"], profit_target=p["profit_target"],
                     daily_loss_limit=p["daily_loss_limit"], max_drawdown=p["max_drawdown"], drawdown_mode=p["drawdown_mode"],
                     max_contracts=p["max_contracts"], consistency_pct=p["consistency_pct"], min_trading_days=p["min_trading_days"])


# ---------------------------------------------------------------------- account
def account_status(hub, ws) -> dict:
    st = ws.prop.status(hub.prices())
    r = st["rules"]
    keep = ("equity", "today_pnl", "total_pnl", "threshold", "distance_to_breach", "daily_loss_remaining", "target_progress_pct",
            "trading_days", "best_day", "consistency_ratio", "consistency_ok", "open_contracts", "positions", "breached", "passed")
    out = {k: st[k] for k in keep}
    out["rules"] = {k: r[k] for k in ("name", "account_size", "profit_target", "daily_loss_limit", "max_drawdown", "drawdown_mode",
                                      "max_contracts", "consistency_pct", "min_trading_days", "commission_per_side")}
    if r["consistency_pct"] and r["profit_target"]:
        cap = r["consistency_pct"] / 100 * max(r["profit_target"], st["total_pnl"] if st["total_pnl"] > 0 else 0)
        out["max_profit_today_for_consistency"] = round(cap, 0)
    return out


# ---------------------------------------------------------------------- market
def market_brief(hub, ws, symbol: str) -> dict:
    from ..daytrade import insider
    c = universe.get(symbol)
    d = insider.build(hub, c.data_symbol if not c.parent else c.parent, ws.prop)
    risk = d.get("risk") or {}
    return {
        "symbol": symbol, "price": d["price"], "prev_close": d.get("prev", {}).get("close") if isinstance(d.get("prev"), dict) else None,
        "tick_size": c.tick_size, "tick_value": c.tick_value, "status": d.get("status"),
        "bias": d.get("bias"), "day_score": d.get("day_score"), "swing_score": d.get("swing_score"),
        "regime": {k: d["regime"].get(k) for k in ("label", "kind", "direction", "adx")} if d.get("regime") else None,
        "ranges": d.get("ranges"), "volatility": d.get("vol"),
        "cot": (d.get("cot") or {}).get("state") if isinstance(d.get("cot"), dict) else d.get("cot"),
        "levels_near": [{k: lv[k] for k in ("price", "label", "dist_ticks")} for lv in (d.get("levels") or [])[:8]],
        "events_today": d.get("events"), "news": [{k: n.get(k) for k in ("title", "direction", "stars")} for n in (d.get("news") or [])[:4]],
        "seasonality": d.get("seasonality") if not isinstance(d.get("seasonality"), dict) else
                       {k: v for k, v in d["seasonality"].items() if not isinstance(v, (list, dict))},
        "risk_template": {k: risk.get(k) for k in ("size_multiplier", "reasons", "stop_ticks", "stop_usd", "contracts", "micro",
                                                   "use_micro", "daily_stop", "max_trades", "consistency_cap")},
        "natgas_policy": (d.get("natgas") or {}).get("policy") if isinstance(d.get("natgas"), dict) else None,
    }


# ---------------------------------------------------------------------- sizing & checks
def size_position(hub, ws, symbol: str, stop_ticks: int, risk_pct_of_buffer: float = 20.0) -> dict:
    sym = symbol.upper()
    out = {"symbol": sym, "stop_ticks": stop_ticks, "risk_pct_of_buffer": risk_pct_of_buffer,
           "full": ws.prop.risk_size(sym, stop_ticks, risk_pct_of_buffer, hub.prices())}
    if sym in MICRO:
        out["micro"] = {"symbol": MICRO[sym], **ws.prop.risk_size(MICRO[sym], stop_ticks, risk_pct_of_buffer, hub.prices())}
    return out


def check_trade(hub, ws, symbol: str, side: str, qty: int, stop_ticks: int | None) -> dict:
    q = abs(int(qty)) * (1 if side.upper().startswith("B") else -1)
    return {"symbol": symbol.upper(), "side": side.upper(), "qty": abs(int(qty)), "stop_ticks": stop_ticks,
            **ws.prop.pre_trade_check(symbol.upper(), q, stop_ticks, hub.prices())}


def daily_plan(hub, ws, symbols: list[str]) -> dict:
    """Today's risk plan: one budget for the day, then per-market stop, size, trade count and no-trade windows."""
    acct = account_status(hub, ws)
    r = acct["rules"]
    buf = acct["distance_to_breach"]
    dl = acct["daily_loss_remaining"]
    # day budget: most of the daily limit if there is one, otherwise a quarter of the drawdown buffer
    day_budget = (0.6 * dl) if dl is not None else 0.25 * buf
    day_budget = max(0.0, min(day_budget, 0.5 * buf))
    rows = []
    for s in symbols[:4]:
        try:
            b = market_brief(hub, ws, s)
        except Exception as exc:  # noqa: BLE001
            rows.append({"symbol": s, "error": str(exc)[:120]})
            continue
        rt = b["risk_template"]
        mult = rt.get("size_multiplier") or 1.0
        per_trade = day_budget / 3 * mult
        stop = int(rt.get("stop_ticks") or 20)
        c = universe.get(s)
        per_contract = stop * c.tick_value + 2 * r["commission_per_side"]
        full = int(per_trade // per_contract) if per_contract else 0
        cap = int(r["max_contracts"])
        micro_sym = MICRO.get(s)
        micro = None
        if micro_sym:
            mc = universe.get(micro_sym)
            micro = min(int(per_trade // (stop * mc.tick_value + 2 * r["commission_per_side"])), cap * 10)
        rows.append({"symbol": s, "price": b["price"], "regime": (b["regime"] or {}).get("label"), "bias": b["bias"],
                     "stop_ticks": stop, "stop_usd_per_contract": round(per_contract, 2), "risk_per_trade_usd": round(per_trade, 0),
                     "contracts": min(full, cap), "micro_contracts": micro, "size_multiplier": mult,
                     "why_size": rt.get("reasons"), "events_today": b["events_today"], "natgas_policy": b.get("natgas_policy")})
    rules_text = [f"Stop for the day after losing ${day_budget:,.0f} or two losing trades in a row.",
                  "No new positions in the 5 minutes before and 15 after a starred release for that market."]
    if acct.get("max_profit_today_for_consistency"):
        rules_text.append(f"Consistency: avoid making more than ${acct['max_profit_today_for_consistency']:,.0f} today; "
                          "beyond that, a big day can delay the payout or the pass.")
    if acct["breached"]:
        rules_text.insert(0, f"ACCOUNT BREACHED: {acct['breached']}. No trading on this account.")
    return {"account": {k: acct[k] for k in ("equity", "distance_to_breach", "daily_loss_remaining", "today_pnl", "breached")},
            "rules_name": r["name"], "day_loss_budget_usd": round(day_budget, 0), "markets": rows, "day_rules": rules_text}


# ---------------------------------------------------------------------- pass probability
def pass_odds(hub, ws, win_rate: float, avg_win: float, avg_loss: float, trades_per_day: float,
              max_days: int = 30, sims: int = 4000, seed: int = 7) -> dict:
    """Monte Carlo of the evaluation from the account's current state under its own rules."""
    st = ws.prop.status(hub.prices())
    r = ws.prop.rules
    if not 0 < win_rate < 1:
        win_rate = win_rate / 100 if 1 < win_rate < 100 else 0.5
    rng = np.random.default_rng(seed)
    start_eq, hwm = st["equity"], ws.prop.hwm
    passed = failed = 0
    days_to_pass = []
    target = r.account_size + r.profit_target if r.profit_target else math.inf
    for _ in range(sims):
        eq, peak, days, best = start_eq, max(hwm, start_eq), st["trading_days"], st["best_day"]
        done = False
        for _d in range(max_days):
            day_start = eq
            n = rng.poisson(max(trades_per_day, 0.1))
            for _t in range(n):
                eq += avg_win if rng.random() < win_rate else -abs(avg_loss)
                eq -= 2 * r.commission_per_side
                if r.drawdown_mode == "trailing_intraday":
                    peak = max(peak, eq)
                thr = r.account_size - r.max_drawdown if r.drawdown_mode == "static" else peak - r.max_drawdown
                if r.trail_locks_at_start and r.drawdown_mode != "static":
                    thr = min(thr, r.account_size)
                if eq <= thr:
                    failed += 1
                    done = True
                    break
                if r.daily_loss_limit and eq - day_start <= -r.daily_loss_limit:
                    break
            if done:
                break
            if r.drawdown_mode == "trailing_eod":
                peak = max(peak, eq)
            if n:
                days += 1
            best = max(best, eq - day_start)
            prof = eq - r.account_size
            consistent = not r.consistency_pct or prof <= 0 or best <= r.consistency_pct / 100 * prof
            if eq >= target and days >= r.min_trading_days and consistent:
                passed += 1
                days_to_pass.append(_d + 1)
                done = True
                break
    ev = win_rate * avg_win - (1 - win_rate) * abs(avg_loss) - 2 * r.commission_per_side
    return {"rules": r.name, "sims": sims, "horizon_days": max_days, "pass_pct": round(100 * passed / sims, 1),
            "fail_pct": round(100 * failed / sims, 1), "unfinished_pct": round(100 * (sims - passed - failed) / sims, 1),
            "median_days_to_pass": int(np.median(days_to_pass)) if days_to_pass else None,
            "expectancy_per_trade_usd": round(ev, 2), "inputs": {"win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
                                                                 "trades_per_day": trades_per_day}}
