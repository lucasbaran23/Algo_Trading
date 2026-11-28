"""FastAPI server: REST + WebSocket streaming for the commodities terminal."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import universe
from .alerts import AlertEngine, UserRule
from .backtest import engine as bt
from .backtest.strategies import STRATEGIES
from .config import settings
from .data.store import MarketData
from .news.service import NewsService
from .tradesignals.manager import SignalManager
from .accounts import auth as acct_auth, store as acct_store, tiers as acct_tiers
from .accounts.workspace import Workspace
from .prop.brokers import Order, make_live_adapter
from .prop.rules import PRESETS, PropRules
from .signals import fundamental
from .signals.engine import SignalEngine

log = logging.getLogger("terminal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _clean(obj):
    """Make numpy/NaN values JSON-safe."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


class Hub:
    def __init__(self):
        self.md = MarketData(settings)
        self.engine = SignalEngine(self.md)
        self.alerts = AlertEngine()
        self.news = NewsService(settings)
        self.tsig = SignalManager(settings.cache_dir)
        self.workspaces: dict[int, Workspace] = {}
        self.live_broker = make_live_adapter(settings)
        self.clients: dict[WebSocket, int] = {}            # socket -> user id
        self.tasks: list[asyncio.Task] = []
        self._day = date.today().isoformat()

    # ------------------------------------------------------------------ per-user workspaces
    def ws(self, uid: int = 0) -> Workspace:
        w = self.workspaces.get(uid)
        if w is None:
            w = self.workspaces[uid] = Workspace(uid, self.md.last, acct_store.db_path().parent, self._ws_event)
        return w

    @property
    def prop(self):                      # the local / default account (single-user mode, research defaults)
        return self.ws(0).prop

    @property
    def paper(self):
        return self.ws(0).paper

    def _ws_event(self, w: Workspace, msg: dict) -> None:
        with contextlib.suppress(RuntimeError):  # no running loop (e.g. unit tests)
            asyncio.get_running_loop().create_task(self.send_user(w.uid, msg))

    # ------------------------------------------------------------------ lifecycle
    def boot(self) -> None:
        self.md.load()
        self.engine.warmup()
        self.news.seed()
        self.engine.news_scores = self.news.scores()
        self.engine.compute_all()
        self.alerts.evaluate(self.engine.snapshots, self.rows())

    def prices(self) -> dict[str, float]:
        return {s: ses.last for s, ses in self.md.sessions.items()}

    def rows(self) -> dict[str, dict]:
        rows = {s: self.engine.summary_row(s) for s in self.engine.snapshots}
        for c in universe.CONTRACTS:
            if c.parent and c.parent in rows:
                rows[c.symbol] = {**rows[c.parent], "symbol": c.symbol, "name": c.name}
        return rows

    # ------------------------------------------------------------------ broadcasting
    def queue(self, msg: dict) -> None:
        with contextlib.suppress(RuntimeError):  # no running loop (e.g. unit tests)
            asyncio.get_running_loop().create_task(self.broadcast(msg))

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(_clean(msg), default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    async def send_user(self, uid: int, msg: dict) -> None:
        data = json.dumps(_clean(msg), default=str)
        for ws, u in list(self.clients.items()):
            if u == uid:
                try:
                    await ws.send_text(data)
                except Exception:  # noqa: BLE001
                    self.clients.pop(ws, None)

    # ------------------------------------------------------------------ loops
    async def tick_loop(self) -> None:
        dt = settings.tick_interval_s
        while True:
            await asyncio.sleep(dt)
            try:
                self.md.tick(dt)
                prices = self.prices()
                for ev in self.tsig.on_prices(prices):
                    await self.signal_event(ev)
                today = date.today().isoformat()
                new_day = today != self._day
                self._day = today
                for w in list(self.workspaces.values()):
                    w.paper.on_prices(prices)
                    if new_day:
                        w.prop.end_of_day(prices, today)
                        w.flags.clear()
                        w.save()
                    w.prop.on_mark(prices)
                quotes = {s: self.md.quote(s) for s in universe.DATA_SYMBOLS}
                for ws, uid in list(self.clients.items()):
                    try:
                        await ws.send_text(json.dumps(_clean({"type": "tick", "quotes": quotes,
                                                              "prop": self.ws(uid).light(prices) if uid >= 0 else None}),
                                                   default=str))
                    except Exception:  # noqa: BLE001
                        self.clients.pop(ws, None)
            except Exception:  # noqa: BLE001
                log.exception("tick loop error")

    async def signal_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.signal_interval_s)
            try:
                self.engine.news_scores = self.news.scores()
                self.engine.compute_all()
                rows = self.rows()
                for ev in self.tsig.generate(self):
                    await self.signal_event(ev)
                new = self.alerts.evaluate(self.engine.snapshots, rows)
                await self.broadcast({"type": "signals", "rows": list(rows.values())})
                for a in new:
                    if a.owner is None:
                        await self.broadcast({"type": "alert", "alert": a.to_dict()})
                    else:
                        await self.send_user(a.owner, {"type": "alert", "alert": a.to_dict()})
                for w in list(self.workspaces.values()):          # account alerts go to their owner only
                    for a in self.prop_alerts(w):
                        await self.send_user(w.uid, {"type": "alert", "alert": a})
            except Exception:  # noqa: BLE001
                log.exception("signal loop error")

    async def signal_event(self, ev: dict) -> None:
        """Trade-signal lifecycle event -> websocket + alert feed."""
        s = ev["signal"]
        sev = {"created": "INFO", "activated": "CRITICAL", "tp1": "WARN", "closed": "WARN"}[ev["event"]]
        a = self.alerts.push_external(s["symbol"], "TRADE", sev, ev["text"][:160], f"{s['name']} · {s['timeframe']} · {s['horizon']}",
                                      key=f"tsig:{s['id']}:{ev['event']}", cooldown=0)
        await self.broadcast({"type": "tsignal", **ev})
        if a:
            await self.broadcast({"type": "alert", "alert": a.to_dict()})

    async def news_loop(self) -> None:
        """Poll news sources; push new classified items and 2-3 star alerts to every client."""
        while True:
            await asyncio.sleep(5)
            try:
                # live polling does blocking HTTP: keep it off the event loop
                fresh = await asyncio.to_thread(self.news.poll)
                if not fresh:
                    continue
                await self.broadcast({"type": "news", "items": fresh})
                for it in fresh:
                    for sig in it["signals"]:
                        if sig["stars"] < 2:
                            continue
                        a = self.alerts.push_external(
                            sig["symbol"], "NEWS", "CRITICAL" if sig["stars"] == 3 else "INFO",
                            f"{'★' * sig['stars']} {sig['symbol']} {sig['direction']}: {it['title']}"[:160],
                            f"{it['source']} · {', '.join(sig['reasons'][:3])}",
                            key=f"news:{it['id']}:{sig['symbol']}", cooldown=0)
                        if a:
                            await self.broadcast({"type": "alert", "alert": a.to_dict()})
            except Exception:  # noqa: BLE001
                log.exception("news loop error")

    def prop_status_light(self, uid: int = 0) -> dict:
        return self.ws(uid).light(self.prices())

    def prop_alerts(self, w: Workspace) -> list[dict]:
        st = w.prop.status(self.prices())
        r = w.prop.rules
        out = []

        def flag(key, sev, title, detail):
            if key in w.flags:
                return
            w.flags.add(key)
            out.append({"id": f"prop:{w.uid}:{key}:{self._day}", "ts": time.time(), "symbol": "PROP", "category": "PROP",
                        "severity": sev, "title": title, "detail": detail})

        if st["breached"]:
            flag("breach", "CRITICAL", "PROP ACCOUNT BREACHED", st["breached"])
        if st["distance_to_breach"] < 0.25 * r.max_drawdown:
            flag("dd25", "CRITICAL", "Within 25% of max drawdown",
                 f"Buffer ${st['distance_to_breach']:,.0f} to threshold ${st['threshold']:,.0f}.")
        if st["daily_loss_remaining"] is not None and r.daily_loss_limit and \
                st["daily_loss_remaining"] < 0.25 * r.daily_loss_limit:
            flag("dll25", "WARN", "Daily loss limit 75% used",
                 f"${st['daily_loss_remaining']:,.0f} left today. Consider stopping for the day.")
        if st["target_progress_pct"] and st["target_progress_pct"] >= 100 and not st["consistency_ok"]:
            flag("consistency", "WARN", "Target hit but consistency rule failing",
                 f"Best day is {st['consistency_ratio']:.0f}% of profit (max {r.consistency_pct:.0f}%).")
        if st["passed"]:
            flag("passed", "INFO", "Evaluation objectives met", "All tracked rules satisfied.")
        return out


hub = Hub()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.boot()
    hub.tasks = [asyncio.create_task(hub.tick_loop()), asyncio.create_task(hub.signal_loop()),
                 asyncio.create_task(hub.news_loop())]
    log.info("Terminal up. Data mode=%s, live routing=%s", settings.data_mode, bool(hub.live_broker))
    if settings.live:
        import threading
        from .data import databento
        threading.Thread(target=databento.auto_backfill, args=(hub.md, "NG"), name="databento-backfill", daemon=True).start()
    yield
    for t in hub.tasks:
        t.cancel()


app = FastAPI(title="Commodities Terminal API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def auth_and_tiers(request: Request, call_next):
    user = acct_auth.user_from(request.headers, request.cookies)
    request.state.user = user
    denied = acct_auth.gate(request.url.path, user)
    if denied:
        return JSONResponse(status_code=denied[0], content=denied[1])
    return await call_next(request)


def _user(request: Request) -> acct_store.User:
    u = request.state.user
    if u is None:
        raise HTTPException(401, {"message": "Sign in to continue.", "signin": True})
    return u


def _ws(request: Request) -> Workspace:
    return hub.ws(_user(request).id)


class AuthIn(BaseModel):
    email: str
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(acct_auth.COOKIE, token, max_age=acct_store.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=os.getenv("COOKIE_SECURE", "auto") == "on")


@app.post("/api/auth/signup")
async def signup(body: AuthIn, response: Response):
    if not acct_auth.auth_on():
        raise HTTPException(400, "Accounts are off in local mode (AUTH_MODE=off).")
    try:
        u = acct_store.create_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_cookie(response, acct_store.new_session(u.id))
    return {"user": u.public()}


@app.post("/api/auth/login")
async def login(body: AuthIn, response: Response):
    if not acct_auth.auth_on():
        raise HTTPException(400, "Accounts are off in local mode (AUTH_MODE=off).")
    u = acct_store.authenticate(body.email, body.password)
    if u is None:
        raise HTTPException(401, "Wrong email or password.")
    _set_cookie(response, acct_store.new_session(u.id))
    return {"user": u.public()}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    acct_store.end_session(acct_auth.token_from(request.headers, request.cookies))
    response.delete_cookie(acct_auth.COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    u = request.state.user
    tier = u.tier if u else "free"
    return {"auth_mode": "on" if acct_auth.auth_on() else "off", "user": u.public() if u else None, "tier": tier,
            "limits": acct_tiers.LIMITS[tier], "view_tiers": acct_tiers.VIEW_TIERS,
            "locked_views": [v for v, t in acct_tiers.VIEW_TIERS.items() if not acct_tiers.allows(tier, t)]}


@app.post("/api/billing/checkout")
async def billing_checkout(request: Request, tier: str):
    from .accounts import billing
    u = _user(request)
    if not acct_auth.auth_on():
        raise HTTPException(400, "Billing is for the hosted version (AUTH_MODE=on).")
    if not billing.configured():
        raise HTTPException(503, "Payments are not set up yet (STRIPE_* settings).")
    try:
        return {"url": await asyncio.to_thread(billing.checkout_url, u, tier)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/billing/portal")
async def billing_portal(request: Request):
    from .accounts import billing
    u = _user(request)
    try:
        return {"url": await asyncio.to_thread(billing.portal_url, u)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    from .accounts import billing
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET not set")
    payload = await request.body()
    try:
        event = billing.verify(payload, request.headers.get("stripe-signature", ""), secret)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = await asyncio.to_thread(billing.handle, event)
    log.info("stripe webhook %s: %s", event.get("type"), result)
    return {"ok": True, "result": result}


@app.get("/api/plans")
async def plans():
    from .accounts import billing
    return {"plans": acct_tiers.PLANS, "order": acct_tiers.TIERS, "limits": acct_tiers.LIMITS,
            "billing": billing.configured()}


def _sym(symbol: str) -> str:
    try:
        return universe.get(symbol).symbol
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------------- market & signals
@app.get("/api/health")
async def health():
    return {"ok": True, "data_mode": settings.data_mode, "loaded_at": hub.md.loaded_at,
            "live_routing": bool(hub.live_broker), "sources": hub.md.sources, "news": hub.news.status()}


@app.get("/api/universe")
async def get_universe():
    return [c.to_dict() for c in universe.CONTRACTS]


@app.get("/api/watchlist")
async def watchlist():
    return _clean(list(hub.rows().values()))


@app.get("/api/snapshot/{symbol}")
async def snapshot(symbol: str):
    sym = universe.get(_sym(symbol)).data_symbol
    snap = dict(hub.engine.snapshots[sym])
    snap["contract"] = universe.get(_sym(symbol)).to_dict()
    return _clean(snap)


@app.get("/api/history/{symbol}")
async def history(symbol: str, days: int = 260, adjusted: bool = False):
    """Daily bars. NG in live mode is back-adjusted internally for analytics; charts get the actual
    traded prices (with the roll gaps) unless adjusted=true."""
    sym = universe.get(_sym(symbol)).data_symbol
    df = hub.md.daily[sym].tail(days).copy()
    if not adjusted and "raw_close" in df:
        f = df["raw_close"] / df["close"]
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * f
    ses = hub.md.sessions[sym]
    df.loc[df.index[-1], ["close"]] = ses.last
    df.loc[df.index[-1], "high"] = max(df["high"].iloc[-1], ses.high)
    df.loc[df.index[-1], "low"] = min(df["low"].iloc[-1], ses.low)
    return _clean([
        {"time": str(r.date.date()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
         "volume": int(r.volume) if r.volume == r.volume else 0}
        for r in df.itertuples()
    ])


@app.get("/api/intraday/{symbol}")
async def intraday(symbol: str):
    sym = universe.get(_sym(symbol)).data_symbol
    return [{"time": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4]} for b in hub.md.sessions[sym].bars]


@app.get("/api/matrix")
async def matrix():
    out = []
    for s in universe.DATA_SYMBOLS:
        sn = hub.engine.snapshots[s]
        out.append({"symbol": s, "scores": sn["scores"], "quant": sn["quant_score"],
                    "fundamental": sn["fundamental_score"], "swing": sn["swing"]["score"],
                    "day": sn["day"]["score"], "bias": sn["swing"]["bias"]})
    return _clean(out)


@app.get("/api/cot")
async def cot_table():
    return _clean([{"symbol": s, "name": universe.get(s).name, "sector": universe.get(s).sector,
                    "source": hub.md.sources[s]["cot"], **hub.engine.snapshots[s]["cot"]}
                   for s in universe.DATA_SYMBOLS])


@app.get("/api/cot/{symbol}")
async def cot_history(symbol: str, weeks: int = 156):
    sym = universe.get(_sym(symbol)).data_symbol
    df = hub.md.cot[sym].tail(weeks + 156).reset_index(drop=True)
    out = []
    for i in range(len(df) - min(weeks, len(df)), len(df)):
        win = df.iloc[: i + 1]
        out.append({"date": str(df.date.iloc[i].date()), "mm_net": int(df.mm_net.iloc[i]),
                    "pm_net": int(df.pm_net.iloc[i]), "oi": int(df.open_interest.iloc[i]),
                    "mm_index": fundamental.cot_index(win.mm_net, 156)})
    return out


@app.get("/api/supply-demand")
async def sd_table():
    out = []
    for s in universe.DATA_SYMBOLS:
        sd = hub.engine.snapshots[s]["supply_demand"]
        if sd:
            out.append({"symbol": s, "name": universe.get(s).name, "source": hub.md.sources[s]["inventory"],
                        **{k: v for k, v in sd.items() if k != "history"}})
    return _clean(out)


# ---------------------------------------------------------------------- day-trade insider
@app.get("/api/insider/{symbol}")
def insider(symbol: str, request: Request):
    """Pre-session briefing for one market. Plain def: the first call per market backtests the playbook."""
    from .daytrade import insider as dt
    return _clean(dt.build(hub, _sym(symbol), _ws(request).prop))


# ---------------------------------------------------------------------- intraday research & top-down
@app.get("/api/intraday/research/{symbol}")
def intraday_research(symbol: str, slip_ticks: float = 1.0):
    """Research-backed intraday strategies: timeframe selection with OOS and walk-forward validation."""
    from .intraday import research as ir
    return _clean(ir.research(hub.md, _sym(symbol), hub.prop.rules.commission_per_side, slip_ticks))


@app.get("/api/intraday/topdown/{symbol}")
def intraday_topdown(symbol: str):
    from .intraday import topdown as td
    return _clean(td.topdown(hub.md, _sym(symbol)))


# ---------------------------------------------------------------------- natural gas desk
@app.get("/api/natgas/outlook")
def natgas_outlook():
    from .natgas import seasonality as ngs
    from .tradesignals.setups import now_et
    return _clean(ngs.outlook(hub.md, settings, now_et()))


@app.get("/api/natgas/seasonality")
def natgas_seasonality():
    from .natgas import seasonality as ngs
    out = {k: v for k, v in ngs.build(hub.md, settings).items() if not k.startswith("_")}
    return _clean(out)


@app.get("/api/natgas/weather")
def natgas_weather():
    from .natgas import weather as wx
    return _clean({k: v for k, v in wx.weather(hub.md, settings).items() if not k.startswith("_")})


@app.get("/api/natgas/dynamics")
def natgas_dynamics():
    from .natgas import dynamics as ngd
    return _clean(ngd.dynamics(hub.md, settings))


@app.get("/api/options/{symbol}")
def options(symbol: str, mode: str | None = None):
    """Options positioning, skew and dealer gamma (GEX) for NG, ES, NQ, CL, GC."""
    from .options import analytics as oa, chains
    sym = universe.get(_sym(symbol)).data_symbol
    if sym not in chains.SUPPORTED:
        raise HTTPException(404, f"No options analytics for {sym}. Supported: {', '.join(chains.SUPPORTED)}")
    if mode not in (None, "standard", "short_calls"):
        raise HTTPException(400, "mode must be standard or short_calls")
    chain, meta = chains.load(hub.md, sym)
    return _clean(oa.analyse(chain, meta, mode))


@app.get("/api/backtest/gammascalp")
def bt_gammascalp(symbol: str = "NG", dte: int = 5, side: int = 1, iv_mode: str = "rv_mult", iv_value: float = 1.1,
                  slip_ticks: float = 1.0, opt_spread_vol: float = 0.01):
    """Options gamma-scalping backtest: hedge-frequency sweep, implied-vol sweep (breakeven) and cycle detail."""
    from .options import chains, gammascalp as gs
    sym = universe.get(_sym(symbol)).data_symbol
    if sym not in chains.SUPPORTED:
        raise HTTPException(404, f"Supported: {', '.join(chains.SUPPORTED)}")
    if dte not in (1, 2, 5, 10, 20) or side not in (1, -1) or iv_mode not in ("rv_mult", "fixed", "csv"):
        raise HTTPException(400, "dte in 1,2,5,10,20; side 1 or -1; iv_mode rv_mult|fixed|csv")
    return _clean(gs.sweep(hub.md, sym, dte, iv_mode, iv_value, side, slip_ticks=slip_ticks, opt_spread_vol=opt_spread_vol,
                           commission=hub.prop.rules.commission_per_side))


@app.get("/api/backtest/gamma-regime")
def bt_gamma_regime(symbol: str = "ES"):
    from .options import chains, gammascalp as gs
    sym = universe.get(_sym(symbol)).data_symbol
    if sym not in chains.SUPPORTED:
        raise HTTPException(404, f"Supported: {', '.join(chains.SUPPORTED)}")
    return _clean(gs.regime_split(hub.md, sym, hub.prop.rules.commission_per_side))


@app.get("/api/strategy/sigma/{symbol}")
def strat_sigma(symbol: str, ref: str = "month", k: float = 2.5):
    from .intraday import extremes
    sym = universe.get(_sym(symbol)).data_symbol
    if ref not in extremes.REFS:
        raise HTTPException(400, f"ref must be one of {', '.join(extremes.REFS)}")
    if not 1.0 <= k <= 5.0:
        raise HTTPException(400, "k must be between 1 and 5")
    rules = hub.prop.rules
    return _clean({"today": extremes.today(hub.md, sym, ref, k),
                   "backtest": extremes.backtest(hub.md, sym, ref, rules.commission_per_side)})


@app.get("/api/strategy/voldiv/{symbol}")
def strat_voldiv(symbol: str, tf: int = 5, k: float = 1.5):
    from .intraday import voldiv
    sym = universe.get(_sym(symbol)).data_symbol
    if tf not in (5, 15):
        raise HTTPException(400, "tf must be 5 or 15")
    if not 0.5 <= k <= 4:
        raise HTTPException(400, "k must be between 0.5 and 4")
    bt = voldiv.backtest(hub.md, sym, hub.prop.rules.commission_per_side)
    ex_date = bt["full_setup_dates"][-1][0] if bt.get("full_setup_dates") else None
    return _clean({"today": voldiv.today(hub.md, sym, tf, k), "backtest": bt,
                   "example": voldiv.today(hub.md, sym, 5, k, on=ex_date) if ex_date else None})


@app.get("/api/strategy/optlevels/{symbol}")
def strat_optlevels(symbol: str, mode: str | None = None):
    from .options import chains, levels
    sym = universe.get(_sym(symbol)).data_symbol
    if sym not in chains.SUPPORTED:
        raise HTTPException(404, f"Supported: {', '.join(chains.SUPPORTED)}")
    if mode not in (None, "standard", "short_calls"):
        raise HTTPException(400, "mode must be standard or short_calls")
    return _clean({"playbook": levels.playbook(hub.md, sym, mode),
                   "backtest": levels.backtest(hub.md, sym, hub.prop.rules.commission_per_side)})


@app.get("/api/natgas/contracts")
def natgas_contracts():
    from .natgas import contracts as ngc
    from .tradesignals.setups import now_et
    from .daytrade import calendar as dcal
    day = dcal.next_session_date(now_et())
    nxt = []
    y, m = ngc.front_contract(day)
    for _ in range(6):
        nxt.append({"code": ngc.code(y, m), "month": ngc.MONTH_NAMES[m - 1], "year": y,
                    "last_trade": ngc.last_trade_date(y, m).isoformat()})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    from .natgas import curve
    return {"session": day.isoformat(), "info": ngc.contract_info(day), "strip": nxt,
            "curve": curve.strip(hub.md, 18, day), "rolls": hub.md.rolls.get("NG", [])[-6:]}


@app.get("/api/natgas/balance")
def natgas_balance():
    """Supply & demand balance: production, imports, LNG, Mexico, power burn, res/com, industrial (EIA STEO),
    plus the weekly weather-adjusted balance from EIA storage and real weather."""
    from .natgas import balance
    return _clean(balance.balance(hub.md))


@app.get("/api/natgas/eia")
def natgas_eia():
    """Daily power burn (EIA-930) and Henry Hub spot: needs a free EIA_API_KEY."""
    from .natgas import balance, eia_api
    out = {}
    try:
        steo_df = balance.steo(hub.md)[0]
    except Exception:  # noqa: BLE001
        steo_df = None
    for k, fn in (("power_burn", lambda: eia_api.power_burn(hub.md.settings, steo_df)), ("henry_hub", lambda: eia_api.henry_hub(hub.md.settings))):
        try:
            out[k] = fn()
        except Exception as exc:  # noqa: BLE001
            out[k] = {"available": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
    hh = out.get("henry_hub", {})
    if hh.get("available"):
        fut = float(hub.md.sessions["NG"].last)
        out["henry_hub"]["basis_vs_front"] = round(hh["last"] - fut, 3)
    return _clean(out)


# ---------------------------------------------------------------------- prop copilot (premium)
_COPILOT = None


def _copilot():
    global _COPILOT
    if _COPILOT is None:
        from .copilot.agent import Copilot
        _COPILOT = Copilot()
    return _COPILOT


def _copilot_quota(u) -> dict:
    period = date.today().strftime("%Y-%m")
    limit = acct_tiers.LIMITS[u.tier]["copilot_month"] if acct_auth.auth_on() else None
    used = acct_store.usage(u.id, "copilot", period) if acct_auth.auth_on() else 0
    return {"period": period, "used": used, "limit": limit, "remaining": None if limit is None else max(0, limit - used)}


@app.get("/api/copilot/state")
async def copilot_state(request: Request):
    from .copilot import tools as T
    u, w = _user(request), _ws(request)
    has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    return _clean({"firms": T.firm_rules()["firms"], "account": T.account_status(hub, w), "quota": _copilot_quota(u),
                   "ai_configured": has_key, "model": os.getenv("COPILOT_MODEL", "claude-opus-5-5")})


@app.get("/api/copilot/plan")
def copilot_plan(request: Request, symbols: str = "NG"):
    """The deterministic daily risk plan (no AI call, no quota)."""
    from .copilot import tools as T
    syms = [_sym(x) for x in symbols.split(",") if x.strip()][:4]
    return _clean(T.daily_plan(hub, _ws(request), syms))


class FirmIn(BaseModel):
    firm: str
    plan: str


@app.post("/api/copilot/firm")
async def copilot_firm(body: FirmIn, request: Request):
    from .copilot import tools as T
    try:
        rules = T.rules_for(body.firm, body.plan)
    except KeyError as exc:
        raise HTTPException(404, f"unknown firm/plan: {exc}") from exc
    w = _ws(request)
    w.prop.reset(rules)
    w.flags.clear()
    w.save()
    return _clean({"ok": True, "rules": rules.to_dict(), "verified": T.firms()[body.firm]["plans"][body.plan].get("verified", False)})


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/copilot/chat")
async def copilot_chat(body: AskIn, request: Request):
    import anthropic
    u, w = _user(request), _ws(request)
    q = _copilot_quota(u)
    if q["remaining"] is not None and q["remaining"] <= 0:
        raise HTTPException(402, {"message": f"You've used this month's {q['limit']} Copilot questions.",
                                  "upgrade": "ultimate" if u.tier != "ultimate" else None})
    try:
        res = await asyncio.to_thread(_copilot().ask, hub, w, u.id, body.message.strip())
    except anthropic.AuthenticationError as exc:
        raise HTTPException(503, "The Copilot isn't set up on this server: add ANTHROPIC_API_KEY to .env and restart.") from exc
    except TypeError as exc:                     # the SDK found no credentials at all
        if "authentication" not in str(exc).lower():
            raise
        raise HTTPException(503, "The Copilot isn't set up on this server: add ANTHROPIC_API_KEY to .env and restart.") from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(429, "The AI service is busy. Try again in a minute.") from exc
    except (anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
        raise HTTPException(503, "Couldn't reach the AI service. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        log.warning("copilot API error %s: %s", exc.status_code, exc.message)
        raise HTTPException(502, f"AI service error ({exc.status_code}).") from exc
    if acct_auth.auth_on():
        acct_store.add_usage(u.id, "copilot", q["period"])
    res["quota"] = _copilot_quota(u)
    return _clean(res)


@app.post("/api/copilot/reset")
async def copilot_reset(request: Request):
    _copilot().reset(_user(request).id)
    return {"ok": True}


@app.get("/api/datastatus/{symbol}")
def data_status(symbol: str = "NG"):
    """Where every number on the NG screens comes from right now, and how fresh it is."""
    import time as _t
    from .data import intraday as idm
    from .natgas import curve, weather as wx
    sym = universe.get(_sym(symbol)).data_symbol
    md = hub.md
    src = md.sources.get(sym, {})
    rows = []
    d = md.daily[sym]
    feed = md.feed_status.get(sym, {})
    rows.append({"what": "Daily prices", "source": "Yahoo NG=F (delayed)" if src.get("prices") == "LIVE" else "Simulator",
                 "status": src.get("prices", "SIM"), "detail": f"{len(d)} sessions {d['date'].iloc[0].date()} → {d['date'].iloc[-1].date()}"
                 + (f" · {src['adjusted']}" if src.get("adjusted") else "")})
    rows.append({"what": "Live price & session bars", "source": "Yahoo 1-minute (≈10-min delay), polled every 30 s" if md.settings.live else "Tick simulator",
                 "status": ("LIVE" if feed.get("ok") else "ERROR") if md.settings.live else "SIM",
                 "detail": (f"last bar {feed.get('bar_time')} ET · polled {int(_t.time() - feed['at'])} s ago" if feed.get("ok") else feed.get("error", "waiting for first poll"))
                 if md.settings.live else "synthetic ticks"})
    try:
        bars, bsrc = idm.bars(md, sym)
        res = bars["res"].value_counts().to_dict() if "res" in bars else {}
        rows.append({"what": "Intraday history", "source": bsrc, "status": "SIM" if bsrc == "SIM" else "LIVE",
                     "detail": f"{bars['session'].nunique()} sessions · {res.get(1, 0):,} one-minute + {res.get(5, 0):,} five-minute bars · {bars.index[0].date()} → {bars.index[-1].date()}"})
    except Exception as exc:  # noqa: BLE001
        rows.append({"what": "Intraday history", "source": "—", "status": "ERROR", "detail": str(exc)[:120]})
    if sym == "NG":
        cv = curve.strip(md, 18)
        rows.append({"what": "Futures curve", "source": cv["source"], "status": "LIVE" if cv["source"].startswith("LIVE") else "SIM",
                     "detail": f"{sum(1 for r in cv['contracts'] if r['last'])} contracts · front {cv['summary']['front']}"})
        inv = md.inventory.get("NG")
        rows.append({"what": "Storage (EIA weekly)", "source": "EIA ir.eia.gov (no key)" if src.get("inventory") == "LIVE" else "Simulator",
                     "status": src.get("inventory", "SIM"), "detail": f"week ending {inv['date'].iloc[-1].date()} · {inv['stocks'].iloc[-1]:,.0f} Bcf" if inv is not None else ""})
        w = wx.weather(md, md.settings)
        rows.append({"what": "Weather (HDD)", "source": w["source"], "status": "LIVE" if w["source"].startswith("LIVE") else "SIM",
                     "detail": f"{len(w['cities'])} cities, 10-yr normals, 16-day forecast"})
    cot = md.cot.get(sym)
    rows.append({"what": "COT positioning", "source": "CFTC disaggregated (weekly)" if src.get("cot") == "LIVE" else "Simulator",
                 "status": src.get("cot", "SIM"), "detail": f"as of {cot['date'].iloc[-1].date()}" if cot is not None else ""})
    ns = hub.news.status() if hasattr(hub.news, "status") else {}
    rows.append({"what": "News", "source": "Google News, Yahoo Finance, NGI, EIA, Rigzone, OilPrice RSS" if md.settings.live else "Simulated headlines",
                 "status": "LIVE" if md.settings.live else "SIM", "detail": f"{ns.get('items', '?')} items" + (f" · {len(ns.get('errors', []))} feed errors" if ns.get("errors") else "")})
    if sym in ("NG", "ES", "NQ", "CL", "GC"):
        from .options import chains
        has = (chains.DATA_DIR / f"{sym}_chain.csv").exists()
        rows.append({"what": "Options chain", "source": "Your CSV" if has else "Simulated (no free NG options feed)",
                     "status": "LIVE" if has else "SIM",
                     "detail": "used everywhere" if has else "shown for illustration; excluded from live signals and the level ladder in live mode"})
    if sym == "NG":
        from .data import databento
        from .natgas import eia_api
        has_eia = bool(md.settings.eia_api_key)
        rows.append({"what": "Daily power burn + Henry Hub spot", "source": "EIA API v2 (EIA-930 grid data)",
                     "status": "LIVE" if has_eia and md.settings.live else "KEY",
                     "detail": "configured" if has_eia else f"free key needed: {eia_api.REGISTER} → EIA_API_KEY in .env"})
        db = databento.status()
        rows.append({"what": "Years of 1-minute history", "source": "Databento (CME Globex)",
                     "status": "LIVE" if db["configured"] else "KEY",
                     "detail": f"configured · spend cap ${db['max_usd']:.0f}/request" if db["configured"]
                     else f"optional: {db['signup']} → DATABENTO_API_KEY in .env (free credit on signup)"})
    from .config import ENV_INFO
    errs = src.get("errors") or {}
    for r in rows:
        key = {"Daily prices": "prices", "COT positioning": "cot", "Storage (EIA weekly)": "inventory"}.get(r["what"])
        if key in errs and r["status"] != "LIVE":
            r["status"], r["detail"] = "ERROR", f"fell back to simulated: {errs[key]}"
    hint = None
    if md.settings.data_mode != "live":
        hint = (f"DATA_MODE is '{md.settings.data_mode}'. Put DATA_MODE=live in the .env file next to README.md and restart. "
                f"({ENV_INFO.get('note') or 'loaded ' + str(ENV_INFO.get('path'))})")
    return {"symbol": sym, "mode": md.settings.data_mode, "rows": rows, "env": ENV_INFO, "hint": hint}


@app.get("/api/natgas/storage")
def natgas_storage():
    """EIA weekly storage: the latest report (regions, vs year ago and 5-year) plus the full history."""
    from .data import live
    from .signals import fundamental
    inv = hub.md.inventory.get("NG")
    out = {"source": hub.md.sources.get("NG", {}).get("inventory", "SIM"), "report": None}
    if inv is not None and len(inv):
        out["sd"] = _clean({k: v for k, v in fundamental.supply_demand_signals(inv).items()})
        regions = [r for r in ("east", "midwest", "mountain", "pacific", "south_central") if r in inv]
        out["regions_hist"] = [{"date": str(r.date.date()), **{k: float(getattr(r, k)) for k in regions}} for r in inv.tail(260).itertuples()] if regions else []
    if hub.md.settings.live:
        try:
            rep = live.fetch_ng_storage_report(hub.md.settings.cache_dir)
            out["report"] = {"release_date": rep["release_date"][:11], "week": rep["current_week"],
                             "series": [{"name": x["name"], "now": x["data"][0][1], "week_ago": x["data"][1][1], "year_ago": x["data"][2][1],
                                         **{k: x["calculated"].get(k) for k in ("net_change", "implied_flow", "5yr-avg", "pct-change_yrago", "pct-chg_5yr-avg")}}
                                        for x in rep["series"]]}
        except Exception as exc:  # noqa: BLE001
            out["report_error"] = str(exc)[:160]
    return out


# ---------------------------------------------------------------------- trade signals
@app.get("/api/tsignals")
async def tsignals(status: str | None = None, symbol: str | None = None, limit: int = 200):
    if symbol:
        _sym(symbol)
    return _clean(hub.tsig.list(status, symbol, min(limit, 500)))


@app.get("/api/tsignals/stats")
async def tsignal_stats():
    return _clean(hub.tsig.stats())


@app.post("/api/tsignals/{sid}/dismiss")
async def tsignal_dismiss(sid: str):
    ev = hub.tsig.dismiss(sid)
    if not ev:
        raise HTTPException(404, "No open signal with that id")
    await hub.signal_event(ev)
    return _clean(ev["signal"])


@app.post("/api/tsignals/{sid}/paper")
async def tsignal_paper(sid: str, use_micro: bool = False):
    """Execute a signal on the paper account as a market bracket order (stop + TP2 as OCO)."""
    s = hub.tsig.signals.get(sid)
    if not s or s["status"] not in ("PENDING", "ACTIVE"):
        raise HTTPException(404, "No open signal with that id")
    sym = s["size"]["micro"]["symbol"] if use_micro and s["size"].get("micro") else s["symbol"]
    qty = max(1, (s["size"]["micro"]["contracts"] if use_micro and s["size"].get("micro") else s["size"]["contracts"]))
    c = universe.get(sym)
    last = hub.md.last(sym)
    stop_ticks = max(1, int(round(abs(last - s["stop"]) / c.tick_size)))
    target_ticks = max(1, int(round(abs(s["tp2"] - last) / c.tick_size)))
    body = OrderIn(symbol=sym, side="BUY" if s["side"] > 0 else "SELL", qty=qty, stop_ticks=stop_ticks,
                   target_ticks=target_ticks)
    return await place_order(body)


@app.post("/api/tsignals/test-notify")
async def tsignal_test_notify():
    from .tradesignals import notify
    sent = notify.push("test", "Test notification from the Commodities Desk Terminal", {"event": "test"}, force=True)
    return {"sent": sent, "channels": notify.channels()}


# ---------------------------------------------------------------------- news
@app.get("/api/news")
async def news(symbol: str | None = None, min_stars: int = 1, direction: str | None = None, limit: int = 100):
    if symbol:
        _sym(symbol)
    return _clean(hub.news.query(symbol, min_stars, direction.upper() if direction else None, min(limit, 500)))


@app.get("/api/news/scores")
async def news_scores():
    return _clean(hub.news.scores())


@app.get("/api/news/status")
async def news_status():
    return hub.news.status()


# ---------------------------------------------------------------------- alerts
class RuleIn(BaseModel):
    symbol: str
    field: str
    op: str
    value: float
    note: str = ""
    once: bool = True


@app.get("/api/alerts")
async def alerts(request: Request, limit: int = 100):
    u = request.state.user
    uid = u.id if u else -1
    return [a.to_dict() for a in list(hub.alerts.history) if a.owner in (None, uid)][:limit]


@app.get("/api/alerts/rules")
async def rules(request: Request):
    uid = _user(request).id
    return [r.__dict__ for r in hub.alerts.rules.values() if r.owner == uid]


@app.post("/api/alerts/rules")
async def add_rule(r: RuleIn, request: Request):
    u = _user(request)
    _sym(r.symbol)
    mine = [x for x in hub.alerts.rules.values() if x.owner == u.id and x.active]
    if len(mine) >= acct_tiers.LIMITS[u.tier]["alerts"]:
        raise HTTPException(402, {"message": f"Your plan allows {acct_tiers.LIMITS[u.tier]['alerts']} active alert rules.",
                                  "upgrade": "premium" if u.tier == "pro" else "ultimate"})
    try:
        rule = hub.alerts.add_rule(UserRule(r.symbol.upper(), r.field, r.op, r.value, r.note, once=r.once, owner=u.id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return rule.__dict__


@app.delete("/api/alerts/rules/{rule_id}")
async def del_rule(rule_id: str, request: Request):
    r = hub.alerts.rules.get(rule_id)
    if r is not None and r.owner == _user(request).id:
        hub.alerts.remove_rule(rule_id)
    return {"ok": True}


# ---------------------------------------------------------------------- prop account
class PropRulesIn(BaseModel):
    preset: str | None = None
    rules: dict | None = None


class SizeIn(BaseModel):
    symbol: str
    stop_ticks: int = Field(gt=0)
    risk_pct: float = Field(default=20, gt=0, le=100)


class OrderIn(BaseModel):
    symbol: str
    side: str                       # BUY / SELL
    qty: int = Field(gt=0)
    type: str = "MARKET"
    price: float | None = None
    stop_ticks: int | None = None
    target_ticks: int | None = None
    route: str = "paper"            # paper | live
    override: bool = False          # allow sending despite a failed pre-trade check (paper only)


@app.get("/api/prop")
async def prop_status(request: Request):
    w = _ws(request)
    return _clean(w.prop.status(hub.prices()) | {"orders": w.paper.account_snapshot()})


@app.get("/api/prop/presets")
async def prop_presets():
    return {k: v.to_dict() for k, v in PRESETS.items()}


@app.post("/api/prop/rules")
async def set_prop_rules(body: PropRulesIn, request: Request):
    if body.preset:
        if body.preset not in PRESETS:
            raise HTTPException(404, "unknown preset")
        rules = PropRules(**PRESETS[body.preset].to_dict())
    elif body.rules:
        try:
            rules = PropRules(**body.rules)
        except TypeError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, "preset or rules required")
    w = _ws(request)
    w.prop.reset(rules)
    w.flags.clear()
    for oid in list(w.paper.working):
        w.paper.cancel(oid)
    w.save()
    return await prop_status(request)


@app.post("/api/prop/reset")
async def prop_reset(request: Request):
    w = _ws(request)
    w.prop.reset()
    w.flags.clear()
    w.save()
    return await prop_status(request)


@app.post("/api/prop/size")
async def prop_size(body: SizeIn, request: Request):
    return _ws(request).prop.risk_size(_sym(body.symbol), body.stop_ticks, body.risk_pct, hub.prices())


@app.post("/api/orders")
async def place_order(body: OrderIn, request: Request):
    w = _ws(request)
    sym = _sym(body.symbol)
    qty = body.qty if body.side.upper() == "BUY" else -body.qty
    check = w.prop.pre_trade_check(sym, qty, body.stop_ticks, hub.prices())
    if body.route == "live":
        if acct_auth.auth_on():                    # hosted: live routing needs per-user broker links (Ultimate)
            raise HTTPException(403, "Live routing in the hosted app is coming with broker linking (Ultimate).")
        if not hub.live_broker:
            raise HTTPException(403, "Live routing disabled. Set LIVE_TRADING_ENABLED=true and BROKER.")
        if not check["ok"]:
            raise HTTPException(409, {"message": "Pre-trade risk check failed", **check})
        o = hub.live_broker.place(Order(sym, qty, body.type.upper(), body.price, body.stop_ticks, body.target_ticks))
        return {"order": o.to_dict(), "check": check}
    if not check["ok"] and not body.override:
        raise HTTPException(409, {"message": "Pre-trade risk check failed", **check})
    o = w.paper.place(Order(sym, qty, body.type.upper(), body.price, body.stop_ticks, body.target_ticks))
    w.save()
    return _clean({"order": o.to_dict(), "check": check, "prop": w.light(hub.prices())})


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str, request: Request):
    _ws(request).paper.cancel(order_id)
    return {"ok": True}


@app.post("/api/prop/flatten")
async def flatten(request: Request):
    w = _ws(request)
    for sym, p in list(w.prop.positions.items()):
        w.paper.flatten_brackets(sym)
        if p.qty:
            w.paper.place(Order(sym, -p.qty))
    return await prop_status(request)


# ---------------------------------------------------------------------- backtest lab
# Plain `def` endpoints: FastAPI runs them in a worker thread so heavy optimisations
# never stall the live tick/alert loops. They only read the loaded history.
class BacktestIn(BaseModel):
    symbol: str = "CL"
    strategy: str = "sma_cross"
    params: dict = Field(default_factory=dict)
    sizing: str = "fixed"
    contracts: int = Field(default=1, ge=1, le=100)
    risk_pct: float = Field(default=0.5, gt=0, le=10)
    max_contracts: int = Field(default=10, ge=1, le=500)
    account_size: float = Field(default=50_000, gt=0)
    commission: float = Field(default=2.5, ge=0)
    slippage_ticks: float = Field(default=1.0, ge=0)
    stop_atr: float = Field(default=0.0, ge=0)
    target_atr: float = Field(default=0.0, ge=0)
    start: str | None = None
    end: str | None = None
    prop_preset: str | None = "eval_50k_eod"
    # optimisation / walk-forward / monte carlo
    ranges: dict = Field(default_factory=dict)
    objective: str = "sharpe"
    oos_pct: float = Field(default=30, ge=0, lt=90)
    train_days: int = Field(default=504, ge=60)
    test_days: int = Field(default=126, ge=20)
    anchored: bool = False
    n_sims: int = Field(default=1000, ge=100, le=10_000)

    def cfg(self) -> bt.RunConfig:
        _sym(self.symbol)
        return bt.RunConfig(**{k: getattr(self, k) for k in bt.RunConfig.__dataclass_fields__})

    def rules(self) -> PropRules | None:
        return PropRules(**PRESETS[self.prop_preset].to_dict()) if self.prop_preset in PRESETS else None


def _bt(fn):
    try:
        return _clean(fn())
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc).strip("'\"")) from exc


@app.get("/api/backtest/strategies")
def bt_strategies():
    return [s.spec() for s in STRATEGIES.values()]


@app.post("/api/backtest/run")
def bt_run(body: BacktestIn):
    return _bt(lambda: bt.run(hub.md, body.cfg(), body.rules()))


@app.post("/api/backtest/optimize")
def bt_optimize(body: BacktestIn):
    if not body.ranges:
        raise HTTPException(400, "Tick at least one parameter to optimise.")
    return _bt(lambda: bt.optimize(hub.md, body.cfg(), body.ranges, body.objective, body.oos_pct))


@app.post("/api/backtest/walkforward")
def bt_walkforward(body: BacktestIn):
    if not body.ranges:
        raise HTTPException(400, "Tick at least one parameter to optimise.")
    return _bt(lambda: bt.walk_forward(hub.md, body.cfg(), body.ranges, body.objective,
                                       body.train_days, body.test_days, body.anchored))


@app.post("/api/backtest/montecarlo")
def bt_montecarlo(body: BacktestIn):
    def go():
        res = bt.run(hub.md, body.cfg(), None)
        return bt.monte_carlo(res["trades"], body.account_size, body.n_sims, body.rules())
    return _bt(go)


# ---------------------------------------------------------------------- websocket & static
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    user = acct_auth.user_from(websocket.headers, websocket.cookies)
    uid = user.id if user else -1                      # -1 = anonymous free-tier viewer
    await websocket.accept()
    hub.clients[websocket] = uid
    try:
        await websocket.send_text(json.dumps(_clean({
            "type": "hello", "rows": list(hub.rows().values()),
            "alerts": [a.to_dict() for a in list(hub.alerts.history) if a.owner in (None, uid)][:100],
            "prop": hub.prop_status_light(uid) if uid >= 0 else None, "data_mode": settings.data_mode,
        }), default=str))
        while True:
            await websocket.receive_text()  # keepalive / ignore client messages
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.pop(websocket, None)


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND / "index.html")
