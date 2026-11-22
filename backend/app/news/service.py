"""News pipeline: fetch -> de-duplicate -> classify -> store -> per-market news score."""
from __future__ import annotations

import logging
import math
import os
import time
from collections import deque

from .. import universe
from ..config import Settings
from . import feeds
from .classifier import classify

log = logging.getLogger(__name__)

HALF_LIFE_H = 12.0              # news impact decays by half every 12 hours
STAR_WEIGHT = {1: 0.25, 2: 0.6, 3: 1.0}


class NewsService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.items: deque[dict] = deque(maxlen=600)
        self.seen: set[str] = set()
        self.sim = feeds.SimNews(settings.sim_seed)
        self.mode = "live" if settings.live else "sim"
        self.errors: list[str] = []
        self.last_poll = 0.0
        self.poll_s = float(os.getenv("NEWS_POLL_S", "120" if settings.live else "20"))
        self.extra_feeds = [u.strip() for u in os.getenv("NEWS_FEEDS", "").split(",") if u.strip()]
        self.llm = None
        if os.getenv("NEWS_CLASSIFIER", "rules").lower() == "claude":
            from .llm import ClaudeClassifier
            self.llm = ClaudeClassifier({s: universe.get(s).name for s in universe.DATA_SYMBOLS})
            if not self.llm.available:
                self.llm = None

    @property
    def classifier_name(self) -> str:
        return f"claude ({self.llm.model})" if self.llm else "rules"

    # ------------------------------------------------------------------ ingest
    def seed(self, n: int = 14) -> None:
        """Back-fill a few simulated items so the screen isn't empty on a cold start."""
        if self.mode == "sim":
            now = time.time()
            batch = self.sim.generate(n)
            for i, it in enumerate(batch):
                it["ts"] = now - (n - i) * 1500
            self.ingest(batch)

    def poll(self) -> list[dict]:
        """Fetch new items (if due) and return the newly classified ones."""
        now = time.time()
        if now - self.last_poll < self.poll_s:
            return []
        self.last_poll = now
        if self.mode == "live":
            raw, self.errors = feeds.fetch_live(universe.DATA_SYMBOLS,
                                                {s: universe.get(s).yahoo for s in universe.DATA_SYMBOLS},
                                                self.extra_feeds)
            if not raw and self.errors:
                log.warning("All news feeds failed: %s", self.errors[:3])
        else:
            raw = self.sim.generate(1 if self.sim.rng.random() < 0.7 else 2)
        return self.ingest(raw)

    def ingest(self, raw: list[dict]) -> list[dict]:
        fresh = []
        for it in sorted(raw, key=lambda x: x["ts"]):
            if it["id"] in self.seen or time.time() - it["ts"] > 3 * 86400:
                continue
            self.seen.add(it["id"])
            fresh.append(it)
        if not fresh:
            return []
        llm_out = None
        if self.llm:
            llm_out = {}
            for start in range(0, len(fresh), 20):
                chunk = fresh[start:start + 20]
                res = self.llm.classify_batch(chunk)
                if res is None:
                    llm_out = None
                    break
                llm_out.update({start + k: v for k, v in res.items()})
        out = []
        for i, it in enumerate(fresh):
            if llm_out is not None and i in llm_out:
                signals = [{"symbol": s["symbol"], "direction": s["direction"], "stars": int(s["stars"]),
                            "score": {"LONG": 1.0, "SHORT": -1.0}.get(s["direction"], 0.0),
                            "reasons": [s["reason"]]} for s in llm_out[i]]
                by = "claude"
            else:
                signals = [m.__dict__ for m in classify(it["title"], it.get("summary", ""))]
                by = "rules"
            if not signals:
                continue  # not about any market we cover
            item = {**it, "signals": signals, "classifier": by,
                    "stars": max(s["stars"] for s in signals),
                    "symbols": [s["symbol"] for s in signals]}
            self.items.appendleft(item)
            out.append(item)
        self.seen = set(list(self.seen)[-5000:]) if len(self.seen) > 6000 else self.seen
        return out

    # ------------------------------------------------------------------ queries
    def query(self, symbol: str | None = None, min_stars: int = 1, direction: str | None = None,
              limit: int = 100) -> list[dict]:
        sym = universe.get(symbol).data_symbol if symbol else None
        out = []
        for it in self.items:
            sigs = it["signals"]
            if sym:
                sigs = [s for s in sigs if s["symbol"] == sym]
            sigs = [s for s in sigs if s["stars"] >= min_stars and (not direction or s["direction"] == direction)]
            if sigs:
                out.append({**it, "signals": sigs})
            if len(out) >= limit:
                break
        return out

    def scores(self) -> dict[str, dict]:
        """Time-decayed, star-weighted news sentiment per market in [-100, +100]."""
        now = time.time()
        acc = {s: {"raw": 0.0, "n": 0, "long": 0, "short": 0, "top": None} for s in universe.DATA_SYMBOLS}
        for it in self.items:
            age_h = max(0.0, (now - it["ts"]) / 3600)
            if age_h > 72:
                continue
            decay = 0.5 ** (age_h / HALF_LIFE_H)
            for s in it["signals"]:
                a = acc.get(s["symbol"])
                if a is None:
                    continue
                d = {"LONG": 1, "SHORT": -1}.get(s["direction"], 0)
                a["raw"] += d * STAR_WEIGHT[s["stars"]] * decay
                a["n"] += 1
                a["long"] += d > 0
                a["short"] += d < 0
                if a["top"] is None or s["stars"] > a["top"]["stars"]:
                    a["top"] = {"title": it["title"], "stars": s["stars"], "direction": s["direction"], "ts": it["ts"]}
        return {s: {"score": round(100 * math.tanh(a["raw"] / 1.5), 1), "count": a["n"], "long": a["long"],
                    "short": a["short"], "top": a["top"]} for s, a in acc.items()}

    def status(self) -> dict:
        return {"mode": self.mode, "classifier": self.classifier_name, "poll_s": self.poll_s,
                "items": len(self.items), "errors": self.errors[:5]}
