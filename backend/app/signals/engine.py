"""Combines quant + quantamental signals into swing and day-trade composites."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import universe
from ..data.store import MarketData
from . import forecast as fc
from . import fundamental, quant

SWING_WEIGHTS = {
    "trend": 0.25, "breakout": 0.10, "mean_reversion": 0.05, "carry": 0.10,
    "seasonality": 0.10, "cot": 0.20, "supply_demand": 0.20,
}
DAY_WEIGHTS = {"vwap": 0.25, "breakout": 0.20, "mean_reversion": 0.15, "trend": 0.25, "news": 0.15}
QUANT_KEYS = {"trend", "breakout", "mean_reversion", "carry", "seasonality"}


def bias_label(score: float) -> str:
    if score >= 50:
        return "STRONG BUY"
    if score >= 20:
        return "BUY"
    if score <= -50:
        return "STRONG SELL"
    if score <= -20:
        return "SELL"
    return "NEUTRAL"


def _weighted(scores: dict[str, float], weights: dict[str, float]) -> float:
    avail = {k: w for k, w in weights.items() if k in scores and scores[k] is not None}
    tot = sum(avail.values())
    return sum(scores[k] * w for k, w in avail.items()) / tot if tot else 0.0


class SignalEngine:
    def __init__(self, md: MarketData):
        self.md = md
        self.hit_rates: dict[str, float] = {}
        self.snapshots: dict[str, dict] = {}
        self.news_scores: dict[str, dict] = {}   # set by the news service before each compute

    def warmup(self) -> None:
        for sym in universe.DATA_SYMBOLS:
            closes = self.md.daily[sym].set_index("date")["close"]
            self.hit_rates[sym] = fc.walk_forward_hit_rate(closes)

    def compute(self, sym: str) -> dict:
        c = universe.get(sym)
        md = self.md
        df = md.daily[sym].copy()
        ses = md.sessions[sym]
        df.loc[df.index[-1], "close"] = ses.last
        df.loc[df.index[-1], "high"] = max(df["high"].iloc[-1], ses.last)
        df.loc[df.index[-1], "low"] = min(df["low"].iloc[-1], ses.last)
        closes = df.set_index("date")["close"]

        trend = quant.trend_score(closes)
        mr = quant.mean_reversion_score(closes)
        bo = quant.breakout_score(df)
        carry = quant.carry_score(df)
        seas = quant.seasonality_score(closes)
        vol = quant.vol_regime(closes)
        cot = fundamental.cot_signals(md.cot[sym])
        sd = fundamental.supply_demand_signals(md.inventory[sym]) if sym in md.inventory else None
        levels = quant.day_trade_levels(df, ses.last, ses.vwap, c.tick_size)
        f = fc.forecast(closes, seas.get("avg_fwd_ret", 0.0))
        f["hit_rate_5d"] = self.hit_rates.get(sym)

        scores = {
            "trend": trend["score"], "breakout": bo["score"], "mean_reversion": mr["score"],
            "carry": carry["score"] if carry.get("available") else None,
            "seasonality": seas["score"], "cot": cot["score"],
            "supply_demand": sd["score"] if sd else None,
        }
        swing = _weighted(scores, SWING_WEIGHTS)
        quant_only = _weighted({k: v for k, v in scores.items() if k in QUANT_KEYS}, SWING_WEIGHTS)
        fundamental_only = _weighted(
            {k: v for k, v in scores.items() if k not in QUANT_KEYS}, SWING_WEIGHTS
        )
        vwap_score = max(-100.0, min(100.0, 100 * math.tanh(levels["vwap_dev_atr"] / 0.5)))
        news = self.news_scores.get(sym)
        news_score = news["score"] if news and news["count"] else None
        day = _weighted(
            {"vwap": vwap_score, "breakout": bo["score"], "mean_reversion": mr["score"],
             "trend": trend["score"], "news": news_score},
            DAY_WEIGHTS,
        )
        live_scores = [v for v in scores.values() if v is not None]
        agree = sum(1 for v in live_scores if math.copysign(1, v) == math.copysign(1, swing) and abs(v) > 10)

        snap = {
            "symbol": sym,
            "name": c.name,
            "sector": c.sector,
            "ts": datetime.now(timezone.utc).isoformat(),
            "quote": md.quote(sym),
            "sources": md.sources.get(sym, {}),
            "scores": scores,
            "swing": {"score": swing, "bias": bias_label(swing), "agreement": f"{agree}/{len(live_scores)}"},
            "day": {"score": day, "bias": bias_label(day), "vwap_score": vwap_score, "news_score": news_score},
            "news": news,
            "quant_score": quant_only,
            "fundamental_score": fundamental_only,
            "detail": {
                "trend": trend, "mean_reversion": mr, "breakout": bo, "carry": carry,
                "seasonality": seas, "vol": vol,
            },
            "cot": cot,
            "supply_demand": sd,
            "levels": levels,
            "forecast": f,
        }
        self.snapshots[sym] = snap
        return snap

    def compute_all(self) -> dict[str, dict]:
        for sym in universe.DATA_SYMBOLS:
            self.compute(sym)
        return self.snapshots

    def summary_row(self, sym: str) -> dict:
        s = self.snapshots[sym]
        return {
            "symbol": sym, "name": s["name"], "sector": s["sector"], **s["quote"],
            "swing": round(s["swing"]["score"], 1), "swing_bias": s["swing"]["bias"],
            "day": round(s["day"]["score"], 1), "day_bias": s["day"]["bias"],
            "trend": round(s["scores"]["trend"], 1),
            "cot_index": round(s["cot"]["mm_index_3y"], 1), "cot_state": s["cot"]["state"],
            "sd_z": round(s["supply_demand"]["dev_z"], 2) if s["supply_demand"] else None,
            "p_up_5d": round(s["forecast"]["horizons"]["5"]["p_up"], 3),
            "vol_regime": s["detail"]["vol"]["regime"],
            "news": s["day"]["news_score"],
            "sources": s["sources"],
        }
