"""In-memory market data hub: history, positioning, inventories, live ticks, intraday session."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .. import universe
from ..config import Settings
from . import live, sim

log = logging.getLogger(__name__)


@dataclass
class Session:
    """Intraday session statistics used by the day-trading panel."""

    open: float
    high: float
    low: float
    last: float
    prev_close: float
    vwap_num: float = 0.0
    vwap_den: float = 0.0
    bars: list = field(default_factory=list)  # 1-minute OHLC bars: [t, o, h, l, c]

    def update(self, price: float, ts: float, vol: float = 1.0) -> None:
        self.last = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.vwap_num += price * vol
        self.vwap_den += vol
        minute = int(ts // 60 * 60)
        if self.bars and self.bars[-1][0] == minute:
            b = self.bars[-1]
            b[2], b[3], b[4] = max(b[2], price), min(b[3], price), price
        else:
            self.bars.append([minute, price, price, price, price])
            del self.bars[:-600]

    def set_bars(self, rows: list, globex: dict) -> None:
        """Replace the session with real bars: rows = [[epoch_minute, o, h, l, c], ...] since the RTH open
        (or the Globex open before it); globex = {open, high, low, vwap_num, vwap_den} for the whole session."""
        if not rows:
            return
        self.bars = [list(r) for r in rows][-600:]
        self.open, self.high, self.low = globex["open"], globex["high"], globex["low"]
        self.vwap_num, self.vwap_den = globex["vwap_num"], globex["vwap_den"]
        self.last = float(rows[-1][4])

    @property
    def vwap(self) -> float:
        return self.vwap_num / self.vwap_den if self.vwap_den else self.last


class MarketData:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.daily: dict[str, pd.DataFrame] = {}
        self.cot: dict[str, pd.DataFrame] = {}
        self.inventory: dict[str, pd.DataFrame] = {}
        self.sessions: dict[str, Session] = {}
        self.sources: dict[str, dict[str, str]] = {}  # symbol -> {prices|cot|inventory: LIVE/SIM/NA}
        self.ticker: sim.TickSimulator | None = None
        self.loaded_at: datetime | None = None
        self._last_live_poll = 0.0
        self._feed: dict[str, dict] = {}          # symbol -> latest from the poller thread
        self._feed_lock = None
        self.feed_status: dict[str, dict] = {}    # symbol -> {ok, at, error}
        self.rolls: dict[str, list[dict]] = {}      # symbol -> detected contract rolls (live continuous series)

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        s = self.settings
        cache = s.cache_dir
        if s.live:                                     # warm the on-disk caches in parallel (cold start ~40 s -> ~8 s)
            from concurrent.futures import ThreadPoolExecutor

            def _warm(sym):
                c_ = universe.get(sym)
                for fn in (lambda: live.fetch_yahoo_daily(c_, cache), lambda: live.fetch_cot(c_, cache)):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001 - the main loop below logs and falls back
                        pass
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(_warm, universe.DATA_SYMBOLS))
        for sym in universe.DATA_SYMBOLS:
            c = universe.get(sym)
            src = {"prices": "SIM", "cot": "SIM", "inventory": "NA"}
            prices = None
            if s.live:
                try:
                    prices = live.fetch_yahoo_daily(c, cache)
                    src["prices"] = "LIVE"
                    if sym == "NG":                       # remove contract-roll gaps from the continuous series
                        from . import rolls
                        self.rolls[sym] = rolls.detect_rolls(prices, "NG")
                        prices = rolls.back_adjust(prices, self.rolls[sym])
                        src["adjusted"] = f"back-adjusted for {len(self.rolls[sym])} rolls"
                except Exception as exc:  # noqa: BLE001 - any source failure falls back to sim
                    log.warning("Yahoo prices failed for %s: %s", sym, exc)
            if prices is None:
                prices = sim.daily_history(c, s.sim_seed)
            self.daily[sym] = prices

            cot = None
            if s.live:
                try:
                    cot = live.fetch_cot(c, cache)
                    src["cot"] = "LIVE"
                except Exception as exc:  # noqa: BLE001
                    log.warning("CFTC COT failed for %s: %s", sym, exc)
            self.cot[sym] = cot if cot is not None else sim.cot_history(c, prices, s.sim_seed)

            inv = None
            if c.eia_series and s.live:
                try:
                    if sym == "NG":                       # keyless EIA storage history (ir.eia.gov)
                        inv = live.fetch_ng_storage(cache)
                    else:
                        inv = live.fetch_eia_series(c, s.eia_api_key, cache)
                    src["inventory"] = "LIVE"
                except Exception as exc:  # noqa: BLE001
                    log.warning("EIA failed for %s: %s", sym, exc)
            if inv is None and c.eia_series:
                inv = sim.inventory_history(c, prices, s.sim_seed)
                src["inventory"] = "SIM"
            if inv is not None:
                self.inventory[sym] = inv
            self.sources[sym] = src

            last, prev = self.daily[sym].iloc[-1], self.daily[sym].iloc[-2]
            self.sessions[sym] = Session(
                open=float(last.close), high=float(last.close), low=float(last.close),
                last=float(last.close), prev_close=float(prev.close),
            )
        self.ticker = sim.TickSimulator(
            [universe.get(s_) for s_ in universe.DATA_SYMBOLS],
            {k: v.last for k, v in self.sessions.items()},
            s.sim_seed,
        )
        self.loaded_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ live feed (background thread)
    def _session_from_bars(self, sym: str, df: pd.DataFrame) -> tuple[list, dict] | None:
        """Today's Globex session from 1-minute bars (bar-close ET): stats for the whole session,
        bars from the regular-session open once it has started."""
        from ..daytrade import calendar as cal
        if df is None or df.empty:
            return None
        sess = (df.index + pd.Timedelta(hours=6)).normalize()
        g = df[sess == sess.max()]
        if g.empty:
            return None
        c = universe.get(sym)
        o_s = int(cal.session_window(c.sector, sym)[0].split(":")[0]) * 60 + int(cal.session_window(c.sector, sym)[0].split(":")[1])
        mins = g.index.hour * 60 + g.index.minute
        rth = g[(mins > o_s) & (g.index.normalize() == g.index[-1].normalize())]
        use = rth if len(rth) >= 5 else g
        et = use.index.tz_localize("America/New_York")
        rows = [[int(t.timestamp()) - 60, float(o), float(h), float(lo), float(cl)]
                for t, o, h, lo, cl in zip(et, use["open"], use["high"], use["low"], use["close"])]
        vol = g["volume"].clip(lower=1.0)
        tp = (g["high"] + g["low"] + g["close"]) / 3
        globex = {"open": float(g["open"].iloc[0]), "high": float(g["high"].max()), "low": float(g["low"].min()),
                  "vwap_num": float((tp * vol).sum()), "vwap_den": float(vol.sum())}
        return rows, globex

    def start_feed(self) -> None:
        """Poll Yahoo off the event loop: focus markets (LIVE_FOCUS, default NG) every 30 s with full
        1-minute bars, the rest every 120 s (last price only) to stay inside Yahoo's rate limits."""
        import os
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from . import intraday
        if not self.settings.live or self._feed_lock is not None:
            return
        self._feed_lock = threading.Lock()
        focus = {x.strip().upper() for x in os.getenv("LIVE_FOCUS", "NG").split(",") if x.strip()}
        live_syms = [k for k, v in self.sources.items() if v["prices"] == "LIVE"]

        def one(sym: str, with_bars: bool) -> None:
            c = universe.get(sym)
            try:
                df = intraday.yahoo_chart(c.yahoo, "1m", rng="1d" if with_bars else "1d")
                item = {"last": float(df["close"].iloc[-1]), "at": time.time(), "bar_time": str(df.index[-1])}
                if with_bars:
                    item["session"] = self._session_from_bars(sym, df)
                with self._feed_lock:
                    self._feed[sym] = item
                self.feed_status[sym] = {"ok": True, "at": time.time(), "bar_time": item["bar_time"]}
            except Exception as exc:  # noqa: BLE001
                self.feed_status[sym] = {"ok": False, "at": time.time(), "error": str(exc)[:160]}

        def loop() -> None:
            pool = ThreadPoolExecutor(max_workers=6)
            last_all = 0.0
            while True:
                now = time.time()
                todo = [(s_, True) for s_ in live_syms if s_ in focus]
                if now - last_all > 120:
                    todo += [(s_, False) for s_ in live_syms if s_ not in focus]
                    last_all = now
                list(pool.map(lambda a: one(*a), todo))
                time.sleep(30)

        threading.Thread(target=loop, name="live-feed", daemon=True).start()

    # ------------------------------------------------------------------ live ticks
    def tick(self, dt_s: float) -> dict[str, float]:
        """Advance prices one step. In live mode, real prices come from the feed thread."""
        now = time.time()
        prices = self.ticker.step(dt_s) if self.ticker else {}
        if self.settings.live:
            if self._feed_lock is None:
                self.start_feed()
            with self._feed_lock:
                feed = dict(self._feed)
            for sym, src in self.sources.items():
                if src["prices"] != "LIVE":
                    continue
                item = feed.get(sym)
                if item and item.get("session"):
                    rows, globex = item["session"]
                    if item.get("_applied") != item["at"]:
                        self.sessions[sym].set_bars(rows, globex)
                        item["_applied"] = item["at"]
                    prices[sym] = self.sessions[sym].last
                    continue
                prices[sym] = item["last"] if item else self.sessions[sym].last
        for sym, p in prices.items():
            if self.settings.live and self.sources.get(sym, {}).get("prices") == "LIVE" and (self._feed.get(sym) or {}).get("session"):
                continue                                   # real bars already set; don't add synthetic ticks
            self.sessions[sym].update(p, now)
        return prices

    def last(self, symbol: str) -> float:
        c = universe.get(symbol)
        return self.sessions[c.data_symbol].last

    def closes_with_live(self, symbol: str) -> pd.Series:
        """Daily closes with today's live price appended as the latest observation."""
        df = self.daily[symbol]
        s = df.set_index("date")["close"].copy()
        s.iloc[-1] = self.sessions[symbol].last
        return s

    def quote(self, symbol: str) -> dict:
        c = universe.get(symbol)
        ses = self.sessions[c.data_symbol]
        prev = float(self.daily[c.data_symbol].iloc[-2].close)
        chg = ses.last - prev
        return {
            "symbol": symbol,
            "last": ses.last,
            "chg": round(chg, 6),
            "chg_pct": round(100 * chg / prev, 3) if prev else 0.0,
            "high": ses.high,
            "low": ses.low,
            "vwap": round(ses.vwap, 6),
            "prev_close": round(prev, 6),
        }
