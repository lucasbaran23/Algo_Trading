"""Optional Claude-powered news classifier.

Enabled when NEWS_CLASSIFIER=claude and Anthropic credentials are available
(ANTHROPIC_API_KEY, or an `ant auth login` profile). Headlines are sent in batches,
and Claude returns schema-validated JSON: which markets each headline moves, the
direction, 1-3 stars of importance and a one-line reason. Any failure (no credentials,
network, rate limit, refusal) returns None and the caller falls back to the rule-based
classifier, so the feed never stops.

Cost guide (Claude Opus 5.5, $4 / $20 per MTok): ~20 headlines per call is roughly
2-3K input + ~1.5K output tokens, i.e. a few cents per batch. Set NEWS_LLM_MODEL to
change the model.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """You classify financial news headlines for futures traders.

For each headline, list ONLY the futures markets it is likely to move materially, from this set:
{markets}

For each market give:
- direction: LONG if the news is bullish for that market's price, SHORT if bearish, NEUTRAL if it matters but the sign is unclear.
- stars (importance for trading that market today):
  3 = scheduled tier-1 releases or shocks that can reprice the market on their own (central-bank decisions, CPI/payrolls, OPEC+ decisions, EIA/USDA reports, wars, sanctions, supply outages, export bans, major weather disasters in production areas).
  2 = genuine fundamental news for that market (inventories, export sales, crop conditions, earnings of index heavyweights, central-bank gold buying, trade policy).
  1 = commentary, previews, analyst opinions, or a story that only describes a price move.
- reason: one short sentence on the causal link (max 20 words).

Think about second-order effects the way a commodities desk would (e.g. a Fed cut is bullish for gold and equity indices; an LNG export outage is bearish for U.S. natural gas; a stronger dollar is bearish for gold).
Return an empty signals list for headlines that move none of the markets."""


def _schema(symbols: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "signals": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string", "enum": symbols},
                                    "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
                                    "stars": {"type": "integer", "enum": [1, 2, 3]},
                                    "reason": {"type": "string"},
                                },
                                "required": ["symbol", "direction", "stars", "reason"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["index", "signals"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


class ClaudeClassifier:
    def __init__(self, markets: dict[str, str]):
        self.markets = markets  # symbol -> name
        self.model = os.getenv("NEWS_LLM_MODEL", DEFAULT_MODEL)
        self.client = None
        self.disabled_reason = ""
        try:
            import anthropic
            self.anthropic = anthropic
            self.client = anthropic.Anthropic(max_retries=2, timeout=60)
        except Exception as exc:  # noqa: BLE001 - SDK missing or no credentials
            self.disabled_reason = f"Claude classifier unavailable: {exc}"
            log.warning(self.disabled_reason)

    @property
    def available(self) -> bool:
        return self.client is not None

    def classify_batch(self, headlines: list[dict]) -> dict[int, list[dict]] | None:
        """headlines: [{"title", "summary"}]. Returns {index: [signal, ...]} or None on any failure."""
        if not self.client or not headlines:
            return None
        a = self.anthropic
        markets = "\n".join(f"- {s}: {n}" for s, n in self.markets.items())
        lines = "\n".join(
            f"[{i}] {h['title']}" + (f" - {h['summary'][:200]}" if h.get("summary") else "")
            for i, h in enumerate(headlines)
        )
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM.format(markets=markets),
                messages=[{"role": "user", "content": f"Classify these headlines:\n{lines}"}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(list(self.markets))}},
                # Server-side fallback: if the model declines a batch, the API retries it on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError:
            log.warning("Claude classifier rate-limited; using rules for this batch")
            return None
        except a.APIStatusError as exc:
            log.warning("Claude classifier API error %s (request %s)", exc.status_code, exc.request_id)
            return None
        except a.APIConnectionError:
            log.warning("Claude classifier network error; using rules for this batch")
            return None
        if response.stop_reason == "refusal":
            log.warning("Claude declined this batch; using rules")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Claude classifier returned invalid JSON (stop_reason=%s)", response.stop_reason)
            return None
        out: dict[int, list[dict]] = {}
        for item in data.get("items", []):
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(headlines):
                out[idx] = [s for s in item.get("signals", []) if s.get("symbol") in self.markets]
        return out
