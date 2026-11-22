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

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        s = self.settings
        cache = s.cache_dir
        for sym in universe.DATA_SYMBOLS:
            c = universe.get(sym)
            src = {"prices": "SIM", "cot": "SIM", "inventory": "NA"}
            prices = None
            if s.live:
                try:
                    prices = live.fetch_yahoo_daily(c, cache)
                    src["prices"] = "LIVE"
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

    # ------------------------------------------------------------------ live ticks
    def tick(self, dt_s: float) -> dict[str, float]:
        """Advance prices one step. Live-priced symbols poll Yahoo at most every 30s."""
        now = time.time()
        prices = self.ticker.step(dt_s) if self.ticker else {}
        if self.settings.live and now - self._last_live_poll > 30:
            self._last_live_poll = now
            for sym, src in self.sources.items():
                if src["prices"] == "LIVE":
                    try:
                        prices[sym] = live.fetch_yahoo_last(universe.get(sym))
                        self.ticker.prices[sym] = prices[sym]
                    except Exception:  # noqa: BLE001
                        pass
        elif self.settings.live:
            for sym, src in self.sources.items():
                if src["prices"] == "LIVE":
                    prices[sym] = self.sessions[sym].last  # hold between polls
        for sym, p in prices.items():
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
