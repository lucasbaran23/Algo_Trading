"""Trade-signal lifecycle: generate -> PENDING -> ACTIVE -> (TP1, stop to breakeven) -> CLOSED.

Rules
* One open signal per market and setup; a 30-minute cool-down after it closes.
* PENDING: a stop entry triggers when price trades through the entry, a limit entry when price
  touches it. If price hits the stop before the entry fills, the setup is INVALIDATED.
  Unfilled signals EXPIRE.
* ACTIVE: stop -> STOPPED. TP1 -> half is taken and the stop moves to breakeven. TP2 -> TARGET.
  Intraday signals still open at expiry close at the market (TIME).
* Result in R (risk multiples): 0.5 × R at TP1 + 0.5 × R of the final exit when TP1 was hit,
  otherwise R of the exit.
* Every transition is an event: pushed to the UI, the alert feed, and (if configured) Discord /
  Telegram / webhook. Signals persist to disk, so the track record survives restarts.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from .. import universe
from ..daytrade import analytics
from . import notify, setups

log = logging.getLogger(__name__)

MICRO = {"ES": "MES", "NQ": "MNQ", "YM": "MYM", "CL": "MCL", "GC": "MGC", "NG": "MNG"}
OPEN = ("PENDING", "ACTIVE")


def _fmt(sym, v):
    t = universe.get(sym).tick_size
    dec = len(f"{t:.10f}".rstrip("0").split(".")[1])
    return f"{v:,.{dec}f}"


class SignalManager:
    def __init__(self, cache_dir: Path):
        self.path = Path(cache_dir) / "trade_signals.json"
        self.signals: dict[str, dict] = {}
        self._cooldown: dict[tuple, float] = {}
        self._regime: dict[str, tuple[str, dict]] = {}
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.signals = {s["id"]: s for s in data}
        except Exception:  # noqa: BLE001 - no file yet / unreadable: start fresh
            self.signals = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            keep = sorted(self.signals.values(), key=lambda s: s["created"])[-500:]
            self.path.write_text(json.dumps(keep))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not save signals: %s", exc)

    # ------------------------------------------------------------------ generation
    def _regime_for(self, sym: str, daily) -> dict:
        key = str(daily["date"].iloc[-1])
        hit = self._regime.get(sym)
        if hit and hit[0] == key:
            return hit[1]
        reg = analytics.regime(daily.iloc[:-1])
        self._regime[sym] = (key, reg)
        return reg

    def generate(self, hub) -> list[dict]:
        """Scan every market for new setups. Returns 'created' events."""
        events = []
        now = time.time()
        now_et = setups.now_et()
        news_scores = hub.engine.news_scores or {}
        recent = [it for it in hub.news.query(min_stars=3, limit=60) if now - it["ts"] < 15 * 60]
        for sym in universe.DATA_SYMBOLS:
            snap = hub.engine.snapshots.get(sym)
            if not snap:
                continue
            c = universe.get(sym)
            daily = hub.md.daily[sym]
            ses = hub.md.sessions[sym]
            lv = snap["levels"]
            tick = c.tick_size
            ctx = {
                "symbol": sym, "sector": c.sector, "tick": tick, "last": ses.last,
                "rnd": lambda v, t=tick: round(round(v / t) * t, 6),
                "adr": setups.adr(daily), "atr": float(lv["atr"]), "prev_close": float(daily.iloc[-2].close),
                "levels": {**lv, "vwap": ses.vwap},
                "session": {"high": ses.high, "low": ses.low, "open": ses.open, "bars": list(ses.bars)},
                "regime": self._regime_for(sym, daily), "day_score": snap["day"]["score"],
                "swing_score": snap["swing"]["score"], "swing_bias": snap["swing"]["bias"],
                "cot_state": snap["cot"]["state"], "now_et": now_et,
                "news_items": [{"title": it["title"], "direction": s["direction"]} for it in recent
                               for s in it["signals"] if s["symbol"] == sym and s["stars"] == 3],
                "natgas": self._ng_state(hub) if sym == "NG" else None,
            }
            for plan in setups.detect(ctx):
                key = (sym, plan["setup"], plan["side"] if plan["setup"] == "orb" else 0)
                if any(s["symbol"] == sym and s["setup"] == plan["setup"] and s["status"] in OPEN and
                       (plan["setup"] != "orb" or s["side"] == plan["side"]) for s in self.signals.values()):
                    continue
                if now - self._cooldown.get(key, 0) < 1800:
                    continue
                sig = self._create(hub, sym, plan, snap, news_scores.get(sym), ctx["regime"], ctx["natgas"])
                if sig is None:
                    continue
                events.append(self._event("created", sig))
                if sig["status"] == "ACTIVE":           # market entries are live immediately
                    events.append(self._event("activated", sig))
        if events:
            self.save()
        return events

    def _ng_state(self, hub) -> dict | None:
        try:
            from ..natgas.dynamics import quick_state
            return quick_state(hub.md, hub.md.settings)
        except Exception:  # noqa: BLE001
            return None

    def _create(self, hub, sym, plan, snap, news, reg, ng=None) -> dict | None:
        side0 = plan["side"]
        if ng and ng["policy"] and side0 < 0 and plan["horizon"].startswith("Swing"):
            return None                                  # winter policy: no swing shorts in natural gas
        c = universe.get(sym)
        now = time.time()
        side = plan["side"]
        risk_pts = abs(plan["entry"] - plan["stop"])
        stop_ticks = max(1, int(round(risk_pts / c.tick_size)))
        # confidence: independent confirmations, 1-3 stars
        conf, why = 1, []
        if (side > 0 and snap["day"]["score"] > 15) or (side < 0 and snap["day"]["score"] < -15) or plan["horizon"].startswith("Swing"):
            conf += 1
            why.append("intraday composite agrees")
        reg_dir = {"UP": 1, "DOWN": -1}.get(reg["direction"], 0)
        if (plan["style"] in ("Breakout", "Trend", "Momentum", "Swing") and reg_dir == side) or \
                (plan["style"] == "Reversion" and reg["kind"] == "range"):
            conf += 1
            why.append("regime fits the setup")
        if news and news.get("count") and news["score"] * side > 20:
            conf += 1
            why.append("news flow agrees")
        policy_note = None
        if ng and ng["policy"]:
            if side < 0:
                conf -= 1
                why.append("winter policy: short downgraded")
                policy_note = "Winter policy: shorts are intraday-only at HALF size (upside spikes are bigger than drops)"
            elif ng["bias"] == "BULLISH" or (ng["rev_hdd"] or 0) >= 10:
                conf += 1
                why.append("colder forecast")
        conf = max(1, min(3, conf))
        size = hub.prop.risk_size(sym, stop_ticks, 20, hub.prices())
        micro = None
        if sym in MICRO:
            micro = hub.prop.risk_size(MICRO[sym], stop_ticks, 20, hub.prices())
            micro["symbol"] = MICRO[sym]
        if policy_note:
            size["contracts"] = size["contracts"] // 2
            if micro:
                micro["contracts"] = micro["contracts"] // 2
            plan = {**plan, "reasons": plan["reasons"] + [policy_note]}
        rr1 = abs(plan["tp1"] - plan["entry"]) / risk_pts
        rr2 = abs(plan["tp2"] - plan["entry"]) / risk_pts
        status = "ACTIVE" if plan["entry_type"] == "market" else "PENDING"
        sig = {
            "id": uuid.uuid4().hex[:10], "symbol": sym, "name_full": c.name, **{k: plan[k] for k in (
                "setup", "name", "style", "timeframe", "horizon", "grade", "side", "entry_type", "entry", "stop", "tp1",
                "tp2", "reasons", "group")},
            "direction": "LONG" if side > 0 else "SHORT", "status": status, "outcome": None,
            "created": now, "expires": now + plan["expires_in"], "filled": now if status == "ACTIVE" else None,
            "fill": plan["entry"] if status == "ACTIVE" else None, "closed": None, "exit": None,
            "stop_initial": plan["stop"], "tp1_hit": False, "r": None,
            "risk_ticks": stop_ticks, "risk_usd": round(stop_ticks * c.tick_value, 2),
            "rr1": round(rr1, 2), "rr2": round(rr2, 2), "confidence": conf, "confidence_why": why,
            "size": {"contracts": size["contracts"], "risk_per_contract": size["risk_per_contract"],
                     "micro": micro and {"symbol": micro["symbol"], "contracts": micro["contracts"]}},
            "price_at_signal": hub.md.sessions[sym].last,
        }
        self.signals[sig["id"]] = sig
        return sig

    # ------------------------------------------------------------------ lifecycle
    def on_prices(self, prices: dict[str, float]) -> list[dict]:
        events, now = [], time.time()
        for s in list(self.signals.values()):
            if s["status"] not in OPEN:
                continue
            px = prices.get(s["symbol"])
            if px is None:
                continue
            side = s["side"]
            if s["status"] == "PENDING":
                beyond_stop = (px - s["stop"]) * side <= 0
                if s["entry_type"] == "stop":
                    trig = (px - s["entry"]) * side >= 0
                    if beyond_stop:                          # moved away through the stop: setup failed, never filled
                        events.append(self._close(s, "CANCELLED", "INVALIDATED", None, now))
                        continue
                else:
                    trig = (px - s["entry"]) * side <= 0     # limit touched (a gap through the stop still fills first)
                if trig:
                    s.update(status="ACTIVE", filled=now, fill=s["entry"])
                    events.append(self._event("activated", s))
                    for o in self.signals.values():           # OCO: cancel the other side
                        if o is not s and s["group"] and o["group"] == s["group"] and o["status"] == "PENDING":
                            events.append(self._close(o, "CANCELLED", "OCO", None, now))
                    if beyond_stop:                          # filled and stopped in the same tick: an honest -1R
                        events.append(self._close(s, "CLOSED", "STOPPED", s["stop"], now))
                elif now > s["expires"]:
                    events.append(self._close(s, "EXPIRED", "NOT FILLED", None, now))
                continue
            # ACTIVE
            if (px - s["stop"]) * side <= 0:
                events.append(self._close(s, "CLOSED", "BREAKEVEN" if s["tp1_hit"] else "STOPPED", s["stop"], now))
            elif (px - s["tp2"]) * side >= 0:
                events.append(self._close(s, "CLOSED", "TARGET", s["tp2"], now))
            elif not s["tp1_hit"] and (px - s["tp1"]) * side >= 0:
                s["tp1_hit"] = True
                s["stop"] = s["fill"]                        # move the stop to breakeven
                events.append(self._event("tp1", s))
            elif now > s["expires"]:
                events.append(self._close(s, "CLOSED", "TIME", px, now))
        if events:
            self.save()
        return events

    def _close(self, s, status, outcome, exit_px, now) -> dict:
        s.update(status=status, outcome=outcome, closed=now, exit=exit_px)
        risk = abs(s["fill"] - s["stop_initial"]) if s["fill"] is not None else None
        if exit_px is not None and risk:
            r_exit = (exit_px - s["fill"]) * s["side"] / risk
            r_tp1 = (s["tp1"] - s["fill"]) * s["side"] / risk
            s["r"] = round(0.5 * r_tp1 + 0.5 * r_exit if s["tp1_hit"] else r_exit, 2)
        self._cooldown[(s["symbol"], s["setup"], s["side"] if s["setup"] == "orb" else 0)] = now
        return self._event("closed", s)

    def dismiss(self, sid: str) -> dict | None:
        s = self.signals.get(sid)
        if not s or s["status"] not in OPEN:
            return None
        ev = self._close(s, "CANCELLED", "DISMISSED", None, time.time())
        self.save()
        return ev

    # ------------------------------------------------------------------ events & notifications
    def _event(self, kind: str, s: dict) -> dict:
        f = lambda v: _fmt(s["symbol"], v)  # noqa: E731
        head = {"created": "NEW SIGNAL", "activated": "SIGNAL ACTIVATED", "tp1": "TP1 HIT", "closed": "SIGNAL CLOSED"}[kind]
        if kind == "created":
            how = {"stop": "on a break of", "limit": "at", "market": "now at"}[s["entry_type"]]
            text = (f"{head}: {s['direction']} {s['symbol']} {how} {f(s['entry'])} · SL {f(s['stop'])} · "
                    f"TP1 {f(s['tp1'])} · TP2 {f(s['tp2'])} · {s['name']} ({s['timeframe']}, {s['horizon']}) · {'★' * s['confidence']}")
        elif kind == "activated":
            text = (f"{head}: {s['direction']} {s['symbol']} filled {f(s['fill'])} · SL {f(s['stop'])} · "
                    f"TP1 {f(s['tp1'])} · TP2 {f(s['tp2'])} · {s['name']}")
        elif kind == "tp1":
            text = f"{head}: {s['symbol']} {s['direction']} reached {f(s['tp1'])}. Take half, stop to breakeven {f(s['stop'])}"
        else:
            r = f" · {s['r']:+.2f}R" if s["r"] is not None else ""
            text = f"{head}: {s['symbol']} {s['direction']} {s['outcome']}{' at ' + f(s['exit']) if s['exit'] is not None else ''}{r} · {s['name']}"
        notify.push(kind, text, {"event": kind, "signal": s})
        return {"event": kind, "text": text, "signal": dict(s)}

    # ------------------------------------------------------------------ queries
    def list(self, status: str | None = None, symbol: str | None = None, limit: int = 200) -> list[dict]:
        out = sorted(self.signals.values(), key=lambda s: s["created"], reverse=True)
        if status == "open":
            out = [s for s in out if s["status"] in OPEN]
        elif status == "closed":
            out = [s for s in out if s["status"] not in OPEN]
        if symbol:
            sym = universe.get(symbol).data_symbol
            out = [s for s in out if s["symbol"] == sym]
        return out[:limit]

    def stats(self) -> dict:
        done = [s for s in self.signals.values() if s["r"] is not None]
        def agg(rows):
            if not rows:
                return {"n": 0}
            rs = [s["r"] for s in rows]
            return {"n": len(rs), "win_pct": round(100 * sum(r > 0 for r in rs) / len(rs), 1),
                    "avg_r": round(sum(rs) / len(rs), 2), "total_r": round(sum(rs), 2)}
        by = {}
        for s in done:
            by.setdefault(s["name"], []).append(s)
        counts = {}
        for s in self.signals.values():
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        return {"overall": agg(done), "by_setup": {k: agg(v) for k, v in sorted(by.items())}, "counts": counts,
                "channels": notify.channels()}
