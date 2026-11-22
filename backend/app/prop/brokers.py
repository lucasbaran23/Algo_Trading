"""Broker / prop-platform adapters.

Most futures prop firms provision accounts on a handful of platforms (Tradovate,
Rithmic, ProjectX-based platforms, NinjaTrader, CQG...). The terminal talks to them
through one interface so the same order ticket and rule checks work everywhere.

* PaperBroker       - fully working, fills against the terminal's price feed,
                      supports bracket (stop/target) orders. Default.
* TradovateAdapter  - REST skeleton for the Tradovate API (demo/live).
* ProjectXAdapter   - REST skeleton for ProjectX Gateway API based platforms.

The two live adapters are written against the vendors' public API docs but have
NOT been certified against live endpoints: test on demo credentials first, and
note that many firms require vendor approval/registration for third-party apps.
Live routing is disabled unless LIVE_TRADING_ENABLED=true.
"""
from __future__ import annotations

import abc
import time
import uuid
from dataclasses import asdict, dataclass, field

import httpx

from .. import universe
from ..config import Settings

MONTH_CODES = "FGHJKMNQUVXZ"
LISTED_MONTHS = {
    "Indices": "HMUZ", "Energy": "FGHJKMNQUVXZ", "Metals": "GJMQVZ", "Grains": "HKNUZ", "Softs": "HKNUZ", "Livestock": "GJMQVZ",
}
OVERRIDES = {"ZS": "FHKNQUX", "SI": "HKNUZ", "HG": "HKNUZ", "PL": "FJNV", "KC": "HKNUZ", "SB": "HKNV",
             "CC": "HKNUZ", "CT": "HKNVZ", "HE": "GJKMNQVZ", "LE": "GJMQVZ"}


def front_month(symbol: str, today: time.struct_time | None = None, roll_days: int = 10) -> str:
    """Best-effort front contract code, e.g. CLF7. Verify against the exchange calendar."""
    t = today or time.gmtime()
    c = universe.get(symbol)
    months = OVERRIDES.get(c.data_symbol) or LISTED_MONTHS[c.sector]
    # Energy contracts expire the month before delivery; others roll ahead of first notice.
    offset = 1 if c.sector == "Energy" else 0
    y, m = t.tm_year, t.tm_mon + offset + (1 if t.tm_mday > 30 - roll_days else 0)
    for _ in range(24):
        if m > 12:
            m -= 12
            y += 1
        if MONTH_CODES[m - 1] in months:
            return f"{symbol}{MONTH_CODES[m - 1]}{y % 10}"
        m += 1
    raise RuntimeError("no listed month found")


@dataclass
class Order:
    symbol: str
    qty: int                    # signed: >0 buy, <0 sell
    type: str = "MARKET"        # MARKET | LIMIT | STOP
    price: float | None = None
    stop_ticks: int | None = None
    target_ticks: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "NEW"
    fill_price: float | None = None
    parent: str | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class BrokerAdapter(abc.ABC):
    name = "base"
    live = False

    @abc.abstractmethod
    def place(self, order: Order) -> Order: ...

    @abc.abstractmethod
    def cancel(self, order_id: str) -> None: ...

    @abc.abstractmethod
    def account_snapshot(self) -> dict: ...

    def on_prices(self, prices: dict[str, float]) -> list[Order]:
        """Called on every tick; returns orders filled this tick (paper only)."""
        return []


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self, get_price, on_fill, slippage_ticks: int = 1):
        self.get_price = get_price
        self.on_fill = on_fill
        self.slippage_ticks = slippage_ticks
        self.working: dict[str, Order] = {}
        self.log: list[Order] = []

    def _fill(self, o: Order, px: float) -> Order:
        c = universe.get(o.symbol)
        slip = self.slippage_ticks * c.tick_size * (1 if o.qty > 0 else -1) if o.type != "LIMIT" else 0
        o.fill_price = round(px + slip, 6)
        o.status = "FILLED"
        self.on_fill(o)
        self.log.append(o)
        return o

    def place(self, order: Order) -> Order:
        c = universe.get(order.symbol)
        last = self.get_price(order.symbol)
        if order.type == "MARKET":
            self._fill(order, last)
            sign = 1 if order.qty > 0 else -1
            if order.stop_ticks:
                self.working_add(Order(order.symbol, -order.qty, "STOP",
                                       round(order.fill_price - sign * order.stop_ticks * c.tick_size, 6),
                                       parent=order.id))
            if order.target_ticks:
                self.working_add(Order(order.symbol, -order.qty, "LIMIT",
                                       round(order.fill_price + sign * order.target_ticks * c.tick_size, 6),
                                       parent=order.id))
        else:
            order.status = "WORKING"
            self.working[order.id] = order
        return order

    def working_add(self, o: Order) -> None:
        o.status = "WORKING"
        self.working[o.id] = o

    def cancel(self, order_id: str) -> None:
        o = self.working.pop(order_id, None)
        if o:
            o.status = "CANCELLED"
            self.log.append(o)

    def flatten_brackets(self, symbol: str) -> None:
        for oid in [k for k, o in self.working.items() if o.symbol == symbol]:
            self.cancel(oid)

    def on_prices(self, prices: dict[str, float]) -> list[Order]:
        filled = []
        for oid, o in list(self.working.items()):
            px = prices.get(universe.get(o.symbol).data_symbol)
            if px is None:
                continue
            buy = o.qty > 0
            hit = (o.type == "LIMIT" and ((buy and px <= o.price) or (not buy and px >= o.price))) or (
                o.type == "STOP" and ((buy and px >= o.price) or (not buy and px <= o.price))
            )
            if hit:
                self.working.pop(oid)
                filled.append(self._fill(o, o.price if o.type == "LIMIT" else px))
                if o.parent:  # OCO: cancel the sibling bracket leg
                    for sid, s in list(self.working.items()):
                        if s.parent == o.parent:
                            self.cancel(sid)
        return filled

    def account_snapshot(self) -> dict:
        return {
            "broker": self.name,
            "working": [o.to_dict() for o in self.working.values()],
            "recent": [o.to_dict() for o in self.log[-30:]][::-1],
        }


class TradovateAdapter(BrokerAdapter):
    """Tradovate REST. Docs: https://api.tradovate.com"""

    name = "tradovate"
    live = True

    def __init__(self, s: Settings):
        self.base = f"https://{'live' if s.tradovate_env == 'live' else 'demo'}.tradovateapi.com/v1"
        self.s = s
        self.token: str | None = None
        self.account: dict | None = None

    def _auth(self) -> None:
        r = httpx.post(f"{self.base}/auth/accesstokenrequest", json={
            "name": self.s.tradovate_user, "password": self.s.tradovate_password,
            "appId": self.s.tradovate_app_id, "appVersion": "0.1",
            "cid": self.s.tradovate_cid, "sec": self.s.tradovate_secret,
        }, timeout=15)
        r.raise_for_status()
        self.token = r.json()["accessToken"]
        accts = self._get("/account/list")
        self.account = accts[0] if accts else None

    def _hdr(self) -> dict:
        if not self.token:
            self._auth()
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, path: str):
        r = httpx.get(self.base + path, headers=self._hdr(), timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        r = httpx.post(self.base + path, json=body, headers=self._hdr(), timeout=15)
        r.raise_for_status()
        return r.json()

    def place(self, order: Order) -> Order:
        body = {
            "accountSpec": self.account["name"], "accountId": self.account["id"],
            "action": "Buy" if order.qty > 0 else "Sell", "symbol": front_month(order.symbol),
            "orderQty": abs(order.qty), "orderType": order.type.title(), "isAutomated": True,
        }
        if order.type == "LIMIT":
            body["price"] = order.price
        if order.type == "STOP":
            body["stopPrice"] = order.price
        res = self._post("/order/placeorder", body)
        order.id = str(res.get("orderId", order.id))
        order.status = "SENT"
        return order

    def cancel(self, order_id: str) -> None:
        self._post("/order/cancelorder", {"orderId": int(order_id)})

    def account_snapshot(self) -> dict:
        snap = self._post("/cashBalance/getcashbalancesnapshot", {"accountId": self.account["id"]})
        return {"broker": self.name, "account": self.account, "cash": snap, "positions": self._get("/position/list")}


class ProjectXAdapter(BrokerAdapter):
    """ProjectX Gateway API (used by several futures prop platforms)."""

    name = "projectx"
    live = True

    def __init__(self, s: Settings):
        self.base = s.projectx_base_url.rstrip("/")
        self.s = s
        self.token: str | None = None
        self.account_id: int | None = None
        self._contracts: dict[str, str] = {}

    def _post(self, path: str, body: dict, auth: bool = True):
        hdr = {}
        if auth:
            if not self.token:
                self._auth()
            hdr = {"Authorization": f"Bearer {self.token}"}
        r = httpx.post(self.base + path, json=body, headers=hdr, timeout=15)
        r.raise_for_status()
        return r.json()

    def _auth(self) -> None:
        res = self._post("/api/Auth/loginKey", {"userName": self.s.projectx_user, "apiKey": self.s.projectx_api_key},
                         auth=False)
        self.token = res["token"]
        accts = self._post("/api/Account/search", {"onlyActiveAccounts": True}).get("accounts", [])
        self.account_id = accts[0]["id"] if accts else None

    def _contract_id(self, symbol: str) -> str:
        if symbol not in self._contracts:
            res = self._post("/api/Contract/search", {"searchText": symbol, "live": False})
            self._contracts[symbol] = res["contracts"][0]["id"]
        return self._contracts[symbol]

    def place(self, order: Order) -> Order:
        body = {
            "accountId": self.account_id, "contractId": self._contract_id(order.symbol),
            "type": {"LIMIT": 1, "MARKET": 2, "STOP": 4}[order.type],
            "side": 0 if order.qty > 0 else 1, "size": abs(order.qty),
        }
        if order.type == "LIMIT":
            body["limitPrice"] = order.price
        if order.type == "STOP":
            body["stopPrice"] = order.price
        res = self._post("/api/Order/place", body)
        order.id = str(res.get("orderId", order.id))
        order.status = "SENT"
        return order

    def cancel(self, order_id: str) -> None:
        self._post("/api/Order/cancel", {"accountId": self.account_id, "orderId": int(order_id)})

    def account_snapshot(self) -> dict:
        pos = self._post("/api/Position/searchOpen", {"accountId": self.account_id})
        return {"broker": self.name, "account_id": self.account_id, "positions": pos.get("positions", [])}


def make_live_adapter(s: Settings) -> BrokerAdapter | None:
    if not s.live_trading_enabled:
        return None
    if s.broker == "tradovate":
        return TradovateAdapter(s)
    if s.broker == "projectx":
        return ProjectXAdapter(s)
    return None
