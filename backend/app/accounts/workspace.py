"""Per-user trading workspace: one prop account (rules, balance, drawdown) and one paper broker.

Saved as JSON under <database folder>/users/<uid>/workspace.json after every fill, rule change and end of day,
so a user's account survives restarts. In AUTH_MODE=off there is a single workspace, uid 0.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ..prop.brokers import Order, PaperBroker
from ..prop.rules import Position, PropAccount, PropRules

log = logging.getLogger(__name__)


class Workspace:
    def __init__(self, uid: int, get_price, data_dir: Path, on_event=None):
        self.uid = uid
        self.path = Path(data_dir) / "users" / str(uid) / "workspace.json"
        self.prop = PropAccount()
        self.paper = PaperBroker(get_price, self._on_fill)
        self.flags: set[str] = set()
        self.on_event = on_event or (lambda ws, msg: None)
        self.load()

    # ------------------------------------------------------------------ fills
    def _on_fill(self, o: Order) -> None:
        net = self.prop.on_fill(o.symbol, o.qty, o.fill_price, __import__("time").time(), tag=o.type)
        pos = self.prop.positions.get(o.symbol)
        if pos and pos.qty == 0:
            self.paper.flatten_brackets(o.symbol)
        self.save()
        self.on_event(self, {"type": "fill", "order": o.to_dict(), "realized": net})

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        p = self.prop
        return {"rules": p.rules.to_dict(), "balance": p.balance, "hwm": p.hwm, "day": p.day,
                "day_start_balance": p.day_start_balance, "positions": {k: asdict(v) for k, v in p.positions.items()},
                "daily_pnl": p.daily_pnl, "trades": p.trades[-500:], "breached": p.breached}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_dict(), default=str))
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save workspace %s: %s", self.uid, exc)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            p = PropAccount(rules=PropRules(**d["rules"]))
            p.balance, p.hwm, p.day = d["balance"], d["hwm"], d["day"]
            p.day_start_balance = d["day_start_balance"]
            p.positions = {k: Position(**v) for k, v in d.get("positions", {}).items()}
            p.daily_pnl, p.trades, p.breached = d.get("daily_pnl", {}), d.get("trades", []), d.get("breached")
            self.prop = p
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load workspace %s (starting fresh): %s", self.uid, exc)

    def light(self, prices: dict[str, float]) -> dict:
        st = self.prop.status(prices)
        st.pop("trades", None)
        return st
