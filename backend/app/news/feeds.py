"""News sources.

Live (free, no key): RSS polling.
  * Google News RSS search, one query per market (targeted, ~minutes latency).
  * Yahoo Finance headline RSS per futures symbol.
  * Any extra feeds in NEWS_FEEDS (comma-separated RSS URLs), e.g. agency or exchange feeds.
  Headlines + links only. For a commercial product, license a real-time newswire
  (Benzinga, Dow Jones, Reuters, MT Newswires...) - RSS terms usually forbid redistribution.

Sim: a template generator so the whole pipeline (classification, signals, alerts, UI)
can be demonstrated offline. Every simulated item is flagged `sim=True` and shown as SIM.
"""
from __future__ import annotations

import hashlib
import html
import random
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

GOOGLE_QUERIES = {
    "ES": "S&P 500 futures OR Wall Street stocks OR Federal Reserve",
    "NQ": "Nasdaq futures OR tech stocks OR chipmakers",
    "YM": "Dow Jones futures OR blue chip stocks",
    "CL": "crude oil OR OPEC OR WTI OR Brent",
    "NG": "natural gas prices OR LNG OR gas storage",
    "RB": "gasoline prices OR refinery outage",
    "HO": "diesel prices OR heating oil OR distillates",
    "GC": "gold prices OR bullion",
    "SI": "silver prices",
    "HG": "copper prices OR LME copper",
    "PL": "platinum prices OR palladium",
    "ZC": "corn futures OR corn crop OR WASDE",
    "ZS": "soybean futures OR soybean exports",
    "ZW": "wheat futures OR Black Sea wheat",
    "KC": "coffee prices OR arabica",
    "SB": "sugar prices OR UNICA",
    "CC": "cocoa prices OR Ivory Coast cocoa",
    "CT": "cotton futures",
    "LE": "cattle futures OR beef prices",
    "HE": "hog futures OR pork prices",
}
GOOGLE_RSS = "https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
_HEADERS = {"User-Agent": "Mozilla/5.0 (commodities-terminal news reader)"}


def item_id(title: str, url: str = "") -> str:
    norm = re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def parse_rss(xml_text: str, source_hint: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or "")).strip()
        src_el = it.find("source")
        source = (src_el.text if src_el is not None and src_el.text else source_hint).strip()
        # Google News appends " - Publisher" to titles
        if source_hint == "Google News" and " - " in title:
            title, _, pub = title.rpartition(" - ")
            source = pub or source
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
        except Exception:  # noqa: BLE001
            ts = time.time()
        out.append({"id": item_id(title, link), "ts": ts, "title": title, "summary": desc[:400],
                    "url": link, "source": source, "sim": False})
    return out


def fetch_live(symbols: list[str], yahoo_map: dict[str, str], extra_feeds: list[str]) -> tuple[list[dict], list[str]]:
    """Poll all configured RSS feeds. Returns (items, errors)."""
    items, errors = [], []
    urls: list[tuple[str, str]] = []
    for sym in symbols:
        if sym in GOOGLE_QUERIES:
            urls.append((GOOGLE_RSS.format(q=quote_plus(GOOGLE_QUERIES[sym])), "Google News"))
        if sym in yahoo_map:
            urls.append((YAHOO_RSS.format(sym=quote_plus(yahoo_map[sym])), "Yahoo Finance"))
    urls += [(u, "RSS") for u in extra_feeds]
    with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as client:
        for url, hint in urls:
            try:
                for attempt in range(2):                 # Google throttles bursts: pace and retry once
                    if hint == "Google News":
                        time.sleep(0.5 + 2.0 * attempt)
                    r = client.get(url)
                    if r.status_code in (429, 500, 502, 503) and attempt == 0:
                        continue
                    break
                r.raise_for_status()
                items += parse_rss(r.text, hint)
            except httpx.HTTPStatusError as exc:
                errors.append(f"{hint}: HTTP {exc.response.status_code}")
            except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the rest
                errors.append(f"{hint}: {type(exc).__name__}")
    return items, errors


# ---------------------------------------------------------------------- simulator
_SIM = {
    "ES": ["Fed {fed} rates by 25 bps; S&P 500 futures {mv}", "CPI {cpi} than expected; stock futures {mv}",
           "Payrolls {jobs} forecasts; Wall Street {mv}", "Analysts say S&P 500 could {mv2} into year-end"],
    "NQ": ["Nvidia {earn} estimates; Nasdaq futures {mv}", "Chipmakers {mv} on new export controls",
           "Tech stocks {mv} as Treasury yields {yl}"],
    "YM": ["Dow futures {mv} as UnitedHealth {earn} estimates", "Blue chips {mv} after Boeing delivery update"],
    "CL": ["OPEC+ agrees to {opec} output from January", "EIA: U.S. crude inventories post unexpected {inv} of {n} million barrels",
           "Drone attack on Saudi oil facility disrupts supply; crude {mvs}", "Oil {mvs} as ceasefire talks progress",
           "Analysts see crude demand slowdown weighing on prices"],
    "NG": ["Natural gas {mvs} as forecasts turn {wx}", "EIA reports storage {inv} larger than expected",
           "Freeport LNG outage leaves more gas in U.S. storage"],
    "RB": ["Refinery outage in Gulf Coast tightens gasoline supply", "Gasoline stocks {inv} as driving season ends"],
    "HO": ["Diesel inventories {inv2} to lowest since 2022", "Heating oil rallies on cold snap in Northeast"],
    "GC": ["Gold {mvs} as central banks keep buying", "Gold {mvs} after Fed signals {fed2}", "Bullion slips as dollar strengthens"],
    "SI": ["Silver {mvs} on record solar panel demand", "Silver ETF outflows continue"],
    "HG": ["Copper {mvs} on China stimulus hopes", "Mine strike in Chile threatens copper supply", "LME copper inventories rise to 6-month high"],
    "PL": ["South African mines cut platinum output on power shortages", "Platinum market surplus forecast for 2027"],
    "ZC": ["USDA {usda} corn yield estimate in WASDE", "Drought in the Corn Belt deepens; corn futures {mv}",
           "Beneficial rains improve corn crop conditions"],
    "ZS": ["China buys U.S. soybeans in flash sale", "Brazil soy harvest seen at record; soybeans {mv}"],
    "ZW": ["Black Sea port attack halts wheat exports", "Wheat {mvs} as Russian export quota eases"],
    "KC": ["Frost in Minas Gerais damages coffee crop; arabica {mvs}", "Vietnam coffee harvest seen larger"],
    "SB": ["UNICA: Brazil sugar output tops estimates", "India monsoon deficit threatens sugar cane"],
    "CC": ["Ivory Coast cocoa arrivals fall; prices {mv}", "Europe cocoa grindings drop sharply in Q3"],
    "CT": ["Cotton {mvs} on weak export sales", "Drought cuts Texas cotton crop estimate"],
    "LE": ["Screwworm outbreak halts Mexican cattle imports", "Cattle futures {mv} as feedlot placements rise"],
    "HE": ["China probe into U.S. pork imports weighs on hog futures", "Hog futures {mv} on strong pork exports"],
}
_FILL = {
    "fed": ["cuts", "raises"], "fed2": ["more cuts", "a pause in easing"], "cpi": ["cooler", "hotter"],
    "jobs": ["beat", "miss"], "mv": ["jump", "slide", "rally", "fall", "climb", "drop"],
    "mvs": ["jumps", "slides", "rallies", "falls", "climbs", "drops"], "mv2": ["rally", "fall"],
    "earn": ["beats", "misses"], "yl": ["fall", "rise"], "opec": ["extend cuts in", "raise"],
    "inv": ["draw", "build"], "inv2": ["fall", "rise"], "n": ["3.1", "4.8", "6.2", "2.4"], "wx": ["colder", "milder"],
    "usda": ["cuts", "raises"],
}
_SOURCES = ["Newswire (SIM)", "Market Desk (SIM)", "Ags Wire (SIM)", "Energy Desk (SIM)"]


class SimNews:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def generate(self, n: int = 1) -> list[dict]:
        out = []
        for _ in range(n):
            sym = self.rng.choice(list(_SIM))
            tpl = self.rng.choice(_SIM[sym])
            title = re.sub(r"\{(\w+)\}", lambda m: self.rng.choice(_FILL[m.group(1)]), tpl)
            ts = time.time()
            out.append({"id": item_id(title + str(ts)), "ts": ts, "title": title, "summary": "",
                        "url": "", "source": self.rng.choice(_SOURCES), "sim": True})
        return out
