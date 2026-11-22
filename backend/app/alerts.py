"""Alert engine: built-in quant/quantamental triggers plus user-defined rules."""
from __future__ import annotations

import itertools
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field

SEVERITY_ORDER = {"INFO": 0, "WARN": 1, "CRITICAL": 2}


@dataclass
class Alert:
    id: str
    ts: float
    symbol: str
    kind: str        # COT / S&D / SIGNAL / LEVEL / VOL / FORECAST / PROP / USER
    severity: str    # INFO / WARN / CRITICAL
    title: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UserRule:
    """e.g. {"symbol": "CL", "field": "last", "op": ">", "value": 70}

    `field` can be any key of the watchlist summary row (last, chg_pct, swing, day,
    cot_index, sd_z, p_up_5d ...).
    """
    symbol: str
    field: str
    op: str
    value: float
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    once: bool = True
    active: bool = True


OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}


class AlertEngine:
    def __init__(self, cooldown_s: float = 3600, maxlen: int = 500):
        self.cooldown_s = cooldown_s
        self.history: deque[Alert] = deque(maxlen=maxlen)
        self.rules: dict[str, UserRule] = {}
        self._last_fired: dict[str, float] = {}
        self._prev_state: dict[str, dict] = {}
        self._counter = itertools.count()

    # ------------------------------------------------------------------ helpers
    def _fire(self, key: str, symbol: str, kind: str, severity: str, title: str, detail: str,
              cooldown: float | None = None) -> Alert | None:
        now = time.time()
        cd = self.cooldown_s if cooldown is None else cooldown
        if now - self._last_fired.get(key, 0) < cd:
            return None
        self._last_fired[key] = now
        a = Alert(f"{int(now)}-{next(self._counter)}", now, symbol, kind, severity, title, detail)
        self.history.appendleft(a)
        return a

    def add_rule(self, rule: UserRule) -> UserRule:
        if rule.op not in OPS:
            raise ValueError(f"op must be one of {list(OPS)}")
        self.rules[rule.id] = rule
        return rule

    def remove_rule(self, rule_id: str) -> None:
        self.rules.pop(rule_id, None)

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, snapshots: dict[str, dict], rows: dict[str, dict]) -> list[Alert]:
        new: list[Alert] = []
        add = lambda a: a and new.append(a)  # noqa: E731
        for sym, s in snapshots.items():
            prev = self._prev_state.get(sym, {})
            cot, sd, lv, q = s["cot"], s["supply_demand"], s["levels"], s["quote"]

            # --- COT positioning extremes and large weekly shifts
            idx = cot["mm_index_3y"]
            if idx >= 90:
                add(self._fire(f"{sym}:cot_hi:{cot['report_date']}", sym, "COT", "WARN",
                               f"{sym} managed money CROWDED LONG",
                               f"MM COT index {idx:.0f}/100 (3y). Net {cot['mm_net']:,}. Contrarian bearish risk.",
                               cooldown=7 * 86400))
            elif idx <= 10:
                add(self._fire(f"{sym}:cot_lo:{cot['report_date']}", sym, "COT", "WARN",
                               f"{sym} managed money CROWDED SHORT",
                               f"MM COT index {idx:.0f}/100 (3y). Net {cot['mm_net']:,}. Short-covering risk.",
                               cooldown=7 * 86400))
            if abs(cot["mm_chg_z"]) >= 2:
                add(self._fire(f"{sym}:cot_chg:{cot['report_date']}", sym, "COT", "INFO",
                               f"{sym} large spec repositioning",
                               f"MM net changed {cot['mm_net_chg']:+,} ({cot['mm_chg_z']:+.1f}σ week over week).",
                               cooldown=7 * 86400))

            # --- Supply & demand imbalance
            if sd:
                if abs(sd["dev_z"]) >= 1.25:
                    tight = sd["dev_z"] < 0
                    add(self._fire(f"{sym}:sd_dev:{sd['date']}", sym, "S&D", "WARN",
                                   f"{sym} inventories {'TIGHT' if tight else 'LOOSE'} vs 5y",
                                   f"Stocks {sd['stocks']:,.1f} vs 5y avg {sd['five_yr_avg']:,.1f} "
                                   f"({sd['dev_pct']:+.1f}%, {sd['dev_z']:+.1f}σ). "
                                   f"{'Bullish' if tight else 'Bearish'} balance.",
                                   cooldown=7 * 86400))
                if abs(sd["surprise_z"]) >= 1.5:
                    draw = sd["surprise_z"] < 0
                    add(self._fire(f"{sym}:sd_surp:{sd['date']}", sym, "S&D", "CRITICAL",
                                   f"{sym} inventory {'DRAW' if draw else 'BUILD'} surprise",
                                   f"Weekly change {sd['weekly_chg']:+,.1f} vs normal {sd['normal_chg']:+,.1f} "
                                   f"({sd['surprise_z']:+.1f}σ).",
                                   cooldown=7 * 86400))

            # --- Composite regime changes
            sw = s["swing"]["score"]
            pb = prev.get("swing_bias")
            if pb and pb != s["swing"]["bias"] and "STRONG" in s["swing"]["bias"]:
                add(self._fire(f"{sym}:swing:{s['swing']['bias']}", sym, "SIGNAL", "CRITICAL",
                               f"{sym} swing composite → {s['swing']['bias']}",
                               f"Score {sw:+.0f}, agreement {s['swing']['agreement']}. "
                               f"Quant {s['quant_score']:+.0f} / Fundamental {s['fundamental_score']:+.0f}."))
            db = prev.get("day_bias")
            if db and db != s["day"]["bias"] and s["day"]["bias"] != "NEUTRAL":
                add(self._fire(f"{sym}:day:{s['day']['bias']}", sym, "SIGNAL", "INFO",
                               f"{sym} intraday bias → {s['day']['bias']}",
                               f"Day score {s['day']['score']:+.0f}. {lv['bias']} (VWAP {lv['vwap']}).",
                               cooldown=900))

            # --- Price crossing key intraday levels
            last, pl = q["last"], prev.get("last")
            if pl is not None:
                for name in ("r1", "s1", "r2", "s2", "prev_high", "prev_low"):
                    lvl = lv[name]
                    if (pl < lvl <= last) or (pl > lvl >= last):
                        direction = "above" if last >= lvl else "below"
                        # one alert per level per cooldown, whichever way it crosses (avoids chop spam)
                        add(self._fire(f"{sym}:lvl:{name}", sym, "LEVEL", "INFO",
                                       f"{sym} crossed {direction} {name.upper()} {lvl}",
                                       f"Last {last}. ATR {lv['atr']:.4g} ({lv['atr_ticks']} ticks).",
                                       cooldown=1800))

            # --- Volatility regime
            vr = s["detail"]["vol"]["regime"]
            if prev.get("vol_regime") and vr != prev["vol_regime"] and vr == "HIGH":
                add(self._fire(f"{sym}:vol", sym, "VOL", "WARN", f"{sym} volatility regime HIGH",
                               f"20d realised vol at {s['detail']['vol']['percentile']:.0f}th pct of 1y. Size down."))

            self._prev_state[sym] = {
                "swing_bias": s["swing"]["bias"], "day_bias": s["day"]["bias"],
                "last": last, "vol_regime": vr,
            }

        # --- User rules
        for rule in list(self.rules.values()):
            if not rule.active:
                continue
            row = rows.get(rule.symbol.upper())
            if not row or rule.field not in row or row[rule.field] is None:
                continue
            if OPS[rule.op](float(row[rule.field]), rule.value):
                a = self._fire(f"user:{rule.id}", rule.symbol, "USER", "CRITICAL",
                               f"{rule.symbol} {rule.field} {rule.op} {rule.value}",
                               f"Now {row[rule.field]}. {rule.note}".strip(), cooldown=300)
                if a:
                    new.append(a)
                    if rule.once:
                        rule.active = False
        return new

    def push_external(self, symbol: str, kind: str, severity: str, title: str, detail: str,
                      key: str | None = None, cooldown: float = 300) -> Alert | None:
        return self._fire(key or f"{kind}:{title}", symbol, kind, severity, title, detail, cooldown)
