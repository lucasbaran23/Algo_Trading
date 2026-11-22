import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.news import feeds
from app.news.classifier import classify, importance
from app.news.service import NewsService


def sig(title, sym):
    return next(m for m in classify(title) if m.symbol == sym)


@pytest.mark.parametrize("title,sym,direction,stars", [
    ("OPEC+ agrees to extend output cuts through June", "CL", "LONG", 3),
    ("EIA: U.S. crude inventories post unexpected build of 6 million barrels", "CL", "SHORT", 3),
    ("Fed cuts rates by 25 bps; S&P 500 jumps", "ES", "LONG", 3),
    ("CPI hotter than expected; Nasdaq futures slide", "NQ", "SHORT", 3),
    ("Drought in the Corn Belt deepens; USDA cuts corn yield estimate", "ZC", "LONG", 3),
    ("Gold hits record high as central banks keep buying", "GC", "LONG", 3),
    ("Beneficial rains improve corn crop conditions", "ZC", "SHORT", 2),
    ("Freeport LNG outage leaves more gas in U.S. storage", "NG", "SHORT", 2),
    ("Screwworm outbreak halts Mexican cattle imports", "LE", "LONG", 3),
])
def test_classifier_cases(title, sym, direction, stars):
    m = sig(title, sym)
    assert m.direction == direction and m.stars == stars and m.reasons


def test_hedged_commentary_is_low_importance():
    assert importance("Analysts say copper could rally on China stimulus hopes")[0] == 1


def test_price_words_do_not_leak_across_markets():
    gold = sig("Fed cuts rates; S&P 500 jumps", "GC")
    assert "+jumps" not in gold.reasons


def test_irrelevant_headline_maps_to_nothing():
    assert classify("Local bakery wins award for best croissant") == []


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Oil jumps after drone attack on Saudi facility - Example Wire</title><link>https://x/1</link>
<pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;Crude&lt;/b&gt; up 4%</description></item>
<item><title>Oil jumps after drone attack on Saudi facility - Other Wire</title><link>https://x/2</link></item>
</channel></rss>"""


def test_rss_parse_and_dedup():
    items = feeds.parse_rss(RSS, "Google News")
    assert items[0]["title"] == "Oil jumps after drone attack on Saudi facility"
    assert items[0]["source"] == "Example Wire" and items[0]["summary"] == "Crude  up 4%"
    for it in items:
        it["ts"] = time.time()
    svc = NewsService(Settings())
    fresh = svc.ingest(items)
    assert len(fresh) == 1                                  # same headline from two publishers -> one item
    assert fresh[0]["signals"][0]["symbol"] == "CL" and fresh[0]["stars"] == 3


def test_scores_decay_and_sign():
    svc = NewsService(Settings())
    now = time.time()
    svc.ingest([{"id": "a", "ts": now, "title": "OPEC+ agrees to extend output cuts", "summary": "", "url": "",
                 "source": "t", "sim": False},
                {"id": "b", "ts": now - 48 * 3600, "title": "Oil prices plunge on demand slowdown worries", "summary": "",
                 "url": "", "source": "t", "sim": False}])
    sc = svc.scores()["CL"]
    assert sc["score"] > 0 and sc["count"] == 2           # fresh bullish 3* outweighs 2-day-old bearish


def test_llm_failure_falls_back_to_rules():
    svc = NewsService(Settings())

    class Broken:
        model = "x"
        def classify_batch(self, h):
            return None
    svc.llm = Broken()
    out = svc.ingest([{"id": "z", "ts": time.time(), "title": "Gold hits record high as central banks keep buying",
                       "summary": "", "url": "", "source": "t", "sim": False}])
    assert out[0]["classifier"] == "rules" and out[0]["signals"][0]["symbol"] == "GC"


def test_llm_results_are_used():
    svc = NewsService(Settings())

    class Fake:
        model = "x"
        def classify_batch(self, h):
            return {0: [{"symbol": "NG", "direction": "LONG", "stars": 3, "reason": "cold snap"}]}
    svc.llm = Fake()
    out = svc.ingest([{"id": "q", "ts": time.time(), "title": "Polar vortex to grip U.S. Midwest", "summary": "",
                       "url": "", "source": "t", "sim": False}])
    assert out[0]["classifier"] == "claude" and out[0]["signals"][0]["reasons"] == ["cold snap"]


def test_news_api():
    with TestClient(app) as c:
        items = c.get("/api/news?limit=5").json()
        assert items and all(it["signals"] for it in items)
        assert c.get("/api/news?symbol=MCL").status_code == 200
        assert c.get("/api/news?symbol=XXX").status_code == 404
        sc = c.get("/api/news/scores").json()
        assert set(sc) >= {"ES", "NQ", "YM", "CL"}
        assert c.get("/api/news/status").json()["classifier"] == "rules"
