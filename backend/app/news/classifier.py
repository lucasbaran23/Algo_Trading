"""Rule-based news classifier: headline -> markets, direction (LONG/SHORT) and importance (1-3 stars).

Free, instant and explainable: every decision lists the phrases that triggered it.
An optional Claude-powered classifier (llm.py) can replace it for better accuracy.

Importance
  ★★★  Scheduled tier-1 releases and shocks that reprice a market on their own
       (Fed decisions, CPI/payrolls, OPEC+ decisions, EIA/USDA reports, wars,
       sanctions, supply outages, export bans, hurricanes in production areas).
  ★★   Fundamental news for that market (inventories, export sales, crop
       conditions, central-bank buying, earnings of index heavyweights...).
  ★    Commentary, previews, analyst opinions and plain price-action stories.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------- market mapping
# symbol -> phrases that tie a headline to that market
MARKET_TERMS: dict[str, list[str]] = {
    "ES": [r"s&p ?500", r"\bspx\b", r"\bspy\b", r"e-?mini s&p", r"wall street", r"u\.?s\.? stocks", r"stock market",
           r"equities", r"\bfed\b", r"federal reserve", r"\bfomc\b", r"\bcpi\b", r"inflation", r"payrolls", r"jobs report",
           r"treasury yields", r"\bvix\b"],
    "NQ": [r"nasdaq", r"\bndx\b", r"\bqqq\b", r"tech stocks", r"chipmakers?", r"semiconductor", r"\bnvidia\b", r"\bapple\b",
           r"\bmicrosoft\b", r"\balphabet\b", r"\bamazon\b", r"\bmeta\b", r"\btesla\b", r"\bai\b stocks", r"artificial intelligence",
           r"\bfed\b", r"federal reserve", r"\bfomc\b", r"\bcpi\b", r"treasury yields"],
    "YM": [r"\bdow\b", r"dow jones", r"\bdjia\b", r"blue[- ]chips?", r"industrials", r"\bboeing\b", r"\bcaterpillar\b",
           r"unitedhealth", r"goldman sachs", r"\bfed\b", r"federal reserve", r"\bfomc\b", r"\bcpi\b", r"payrolls"],
    "CL": [r"crude", r"\boil\b", r"\bwti\b", r"\bbrent\b", r"\bopec", r"cushing", r"refiner(y|ies)", r"oil rigs?", r"rig count",
           r"saudi", r"strait of hormuz", r"oil tanker", r"\bspr\b", r"strategic petroleum"],
    "NG": [r"natural gas", r"nat ?gas", r"\blng\b", r"henry hub", r"gas storage", r"storage (build|draw|injection)",
           r"heating demand", r"cold snap", r"polar vortex", r"heat ?wave", r"freeport lng"],
    "RB": [r"gasoline", r"\brbob\b", r"driving season", r"refiner(y|ies)", r"refinery outage"],
    "HO": [r"diesel", r"heating oil", r"distillates?", r"\bulsd\b", r"jet fuel"],
    "GC": [r"\bgold\b", r"bullion", r"precious metals?", r"safe[- ]haven", r"central bank (gold )?(buying|purchases)",
           r"real yields", r"\bfed\b", r"federal reserve", r"dollar index", r"\bdxy\b"],
    "SI": [r"\bsilver\b", r"precious metals?", r"solar (panel )?demand"],
    "HG": [r"copper", r"\blme\b", r"china (factory|manufacturing|pmi|stimulus)", r"chile", r"peru", r"smelter", r"mine (strike|closure)"],
    "PL": [r"platinum", r"palladium", r"autocatalyst", r"south africa(n)? (mines?|power)", r"\bpgms?\b"],
    "ZC": [r"\bcorn\b", r"maize", r"ethanol", r"\bwasde\b", r"corn belt", r"\busda\b"],
    "ZS": [r"soybeans?", r"\bsoy\b", r"soymeal", r"soyoil", r"crush", r"\bwasde\b", r"brazil (crop|harvest|soy)", r"\busda\b",
           r"china (purchases|buying) (of )?u\.?s\.? (soy|farm)"],
    "ZW": [r"wheat", r"black sea", r"ukraine (grain|export|port)", r"russia(n)? (wheat|grain)", r"\bwasde\b", r"\busda\b"],
    "KC": [r"coffee", r"arabica", r"robusta", r"minas gerais", r"vietnam (coffee|crop)"],
    "SB": [r"sugar", r"\bunica\b", r"cane", r"india (sugar|monsoon)"],
    "CC": [r"cocoa", r"ivory coast", r"c[oô]te d.ivoire", r"ghana", r"grindings?", r"chocolate"],
    "CT": [r"cotton", r"\bginning\b", r"textile demand"],
    "LE": [r"cattle", r"\bbeef\b", r"feedlots?", r"screwworm", r"cattle on feed"],
    "HE": [r"\bhogs?\b", r"\bpork\b", r"swine", r"african swine fever", r"\basf\b"],
}

# ---------------------------------------------------------------------- direction lexicon
# (pattern, direction, weight). +1 = bullish for the market, -1 = bearish.
_PRICE_UP = [r"\b(surges?|soars?|rall(y|ies)|jumps?|spikes?|climbs?|gains?|rises?|rebounds?|hits? (a )?record high|"
             r"advances?|higher|up \d)"]
_PRICE_DOWN = [r"\b(plunges?|tumbles?|slumps?|sinks?|drops?|falls?|slides?|declines?|lower|sell-?off|"
               r"down \d|hits? (a )?(\d+-\w+ )?low)"]

_MACRO_RISK_ON = [(r"rate cuts?|cuts? (interest )?rates|(more|further|deeper) (rate )?cuts|dovish|easing", +1, 1.5),
                  (r"(cpi|inflation) (cools?|eases?|slows?|softer|below)", +1, 1.5),
                  (r"soft landing|stimulus|trade deal|tariff (relief|pause|cut)", +1, 1.2),
                  (r"earnings beat|beats? (estimates|expectations)|record (profit|revenue)|strong guidance", +1, 1.2),
                  (r"rate hikes?|hikes? (interest )?rates|hawkish|tightening", -1, 1.5),
                  (r"(cpi|inflation) (hotter|accelerates?|jumps?|rises?|above|sticky)", -1, 1.5),
                  (r"recession|layoffs|job cuts|unemployment (rises|jumps)|weak (jobs|payrolls)", -1, 1.2),
                  (r"tariffs?|trade war|sanctions on china|export controls?", -1, 1.0),
                  (r"misses? (estimates|expectations)|profit warning|cuts? (guidance|outlook)|downgrade", -1, 1.2),
                  (r"shutdown|default|downgrades? u\.?s\.? credit|bank (failure|collapse)", -1, 1.5),
                  (r"\bwar\b|invasion|missile|geopolitical (tension|risk)", -1, 1.2)]

_ENERGY = [(r"(output|production|supply) cuts?|cuts? (output|production|supply)|extends? cuts", +1, 2.0),
           (r"(supply )?disruptions?|outages?|shut[- ]?ins?|pipeline (attack|explosion|leak|shutdown)|force majeure", +1, 2.0),
           (r"(attack|strike|drone|missile).{0,40}(refinery|tanker|oil|terminal|facility)", +1, 2.0),
           (r"sanctions? (on|against) (iran|russia|venezuela)|tightens? sanctions|embargo", +1, 1.5),
           (r"hurricane|tropical storm", +1, 1.2),
           (r"(inventor(y|ies)|stocks|stockpiles?) (draw|fall|drop|decline)|(unexpected|larger[- ]than[- ]expected) draw", +1, 1.5),
           (r"cold (snap|weather|blast)|polar vortex|heat ?wave|freeze", +1, 1.3),
           (r"(output|production|supply) (increase|hike|boost|rises?)|raises? (output|production)|unwind(s|ing)? cuts|"
            r"pumps? more", -1, 2.0),
           (r"(inventor(y|ies)|stocks|stockpiles?) (build|rise|jump|climb)|(unexpected|larger[- ]than[- ]expected) build", -1, 1.5),
           (r"glut|oversupply|surplus|demand (slump|slowdown|worries|concerns)|weak demand", -1, 1.5),
           (r"ceasefire|peace (deal|talks)|eases? sanctions|sanctions? (relief|lifted|waiver)", -1, 1.3),
           (r"mild (weather|winter)|warm(er)? (weather|winter)", -1, 1.2),
           # An LNG *export* outage strands gas at home: bearish U.S. gas. Weighted to override the generic
           # "outage = bullish" rule above, which also matches these headlines.
           (r"lng (export )?(outage|shutdown)|freeport (lng )?(outage|shut)", -1, 3.0)]

_METALS = [(r"safe[- ]haven|geopolitical (tension|risk)|war|conflict|flight to safety", +1, 1.3),
           (r"central banks? (keep |continue |are )?(buy|buying|purchases|add)", +1, 1.5),
           (r"rate cuts?|cuts? (interest )?rates|(more|further|deeper) (rate )?cuts|dovish|weak(er)? dollar|dollar (falls|slides|weakens)|real yields (fall|drop)", +1, 1.5),
           (r"rate hikes?|hikes? (interest )?rates|hawkish|strong(er)? dollar|dollar (rises|jumps|strengthens)|yields (rise|jump|climb)", -1, 1.5),
           (r"etf (outflows?|selling)|profit[- ]taking", -1, 1.0),
           (r"etf (inflows?|buying)", +1, 1.0),
           (r"mine (strike|closure|shutdown|flood)|smelter (cut|closure)|supply (deficit|shortage)|export ban", +1, 1.8),
           (r"china (stimulus|demand (rebounds|recovers))", +1, 1.3),
           (r"china (slowdown|demand (slumps|weakens))|weak (factory|pmi)", -1, 1.3),
           (r"surplus|oversupply|inventor(y|ies) (rise|build)", -1, 1.2)]

_AGS = [(r"drought|dry (weather|conditions|spell)|heat ?wave|frost|freeze|flooding|el ni[nñ]o|la ni[nñ]a|"
         r"poor (crop|harvest)|crop (damage|losses|downgrade)", +1, 1.8),
        (r"(cuts?|lowers?|slashes?) (yield|production|crop|output) (estimate|forecast)|"
         r"(yield|production|crop) (estimate|forecast) (cut|lowered)", +1, 2.0),
        (r"(strong|robust|record|surging) (export|exports|demand|buying)|flash sale|china (buys|purchases)", +1, 1.5),
        (r"export (ban|restrictions?|halt)|port (closure|attack|blockade)", +1, 2.0),
        (r"disease|outbreak|screwworm|bird flu|african swine fever", +1, 1.2),
        (r"(halts?|bans?|suspends?|blocks?) .{0,30}imports|import (ban|halt|suspension)", +1, 1.8),
        (r"(record|bumper|large|larger|bigger) (crop|harvest)|(crop|harvest) (seen |forecast |estimated )?(larger|bigger|at record)|"
         r"favou?rable weather|beneficial rains?|good rains", -1, 1.8),
        (r"(raises?|increases?|lifts?) (yield|production|crop|output) (estimate|forecast)", -1, 2.0),
        (r"(weak|slow|poor) (export|exports|demand)|cancell?ations?|harvest pressure", -1, 1.5),
        (r"(exports?|ports?) resume|corridor (reopens|extended)", -1, 1.5),
        (r"(grindings?|demand) (fall|drop|decline|weak)", -1, 1.5),
        (r"(grindings?|demand) (rise|jump|strong)", +1, 1.5)]

GROUPS = {"Indices": _MACRO_RISK_ON, "Energy": _ENERGY, "Metals": _METALS,
          "Grains": _AGS, "Softs": _AGS, "Livestock": _AGS}

# Gold/silver are havens: risk-off news is bullish for them, so they use the metals lexicon, not the equity one.

# ---------------------------------------------------------------------- importance
STAR3 = [r"\bfomc\b|fed (decision|raises|cuts|holds)|rate decision|powell|central bank decision",
         r"\bcpi\b|consumer prices|\bpce\b|payrolls|jobs report|unemployment rate|\bgdp\b",
         r"\bopec\+?\b.{0,30}(agree|decid|meeting|cut|increase|output)",
         r"\beia\b.{0,30}(report|inventor|storage|stocks)|weekly (crude|storage) report",
         r"\bwasde\b|usda (cuts|raises|report|acreage|stocks)|crop production report|prospective plantings",
         r"\bwar\b|invasion|missile|drone strike|attack|explosion|sanctions|embargo|export ban|force majeure",
         r"hurricane|cyclone|strike at|mine (closure|shutdown)|default|bank (failure|collapse)|shutdown",
         r"ceasefire|peace (deal|agreement)",
         r"(halts?|bans?|suspends?) .{0,30}(imports|exports)|import ban|export quota"]
STAR2 = [r"fed (signals|minutes|officials?|speakers?|chair)|central bank (signals|guidance)",
         r"inventor(y|ies)|stockpiles?|storage|export (sales|inspections)|crop (progress|condition)|rig count",
         r"central banks?|etf (flows?|inflows?|outflows?)|earnings|guidance|pmi|retail sales|jobless claims",
         r"tariffs?|trade (deal|war|talks)|stimulus|demand|supply|production|output|harvest|planting|weather|drought",
         r"yields|dollar"]
DAMPENERS = r"\b(could|may|might|analysts? (say|see|expect)|preview|outlook|what to watch|explainer|opinion|column|poll shows|technical(s| analysis)?|week ahead)\b"
BOOSTERS = r"\b(unexpected(ly)?|surprise|shock|record|biggest|largest|first time (in|since)|emergency|historic)\b"


@dataclass
class MarketSignal:
    symbol: str
    direction: str          # LONG / SHORT / NEUTRAL
    stars: int              # 1-3
    score: float            # -1..+1 directional conviction
    reasons: list[str] = field(default_factory=list)


def _find(patterns, text) -> list[str]:
    out = []
    for p in patterns:
        m = re.search(p, text)
        if m:
            out.append(m.group(0))
    return out


def importance(text: str) -> tuple[int, list[str]]:
    t = text.lower()
    hits3, hits2 = _find(STAR3, t), _find(STAR2, t)
    stars = 3 if hits3 else 2 if hits2 else 1
    why = hits3 or hits2
    if re.search(BOOSTERS, t) and stars < 3:
        stars += 1
        why = why + [re.search(BOOSTERS, t).group(0)]
    if re.search(DAMPENERS, t) and stars > 1:
        stars -= 1
        why = why + [f"-{re.search(DAMPENERS, t).group(0)}"]
    return stars, why


def classify(title: str, summary: str = "", sectors: dict[str, str] | None = None) -> list[MarketSignal]:
    """Return one MarketSignal per market the headline is about."""
    from .. import universe
    text = f"{title}. {summary}".lower()
    stars, star_why = importance(text)
    out = []
    for sym, terms in MARKET_TERMS.items():
        hits = _find(terms, text)
        if not hits:
            continue
        sector = (sectors or {}).get(sym) or universe.get(sym).sector
        if sym in ("GC", "SI"):
            lex = _METALS
        elif sym in ("HG", "PL"):
            lex = _METALS[2:]      # industrial metals: no safe-haven / central-bank-buying bid
        else:
            lex = GROUPS.get(sector, [])
        raw, why = 0.0, []
        for pat, sign, w in lex:
            m = re.search(pat, text)
            if m:
                raw += sign * w
                why.append(("+" if sign > 0 else "−") + m.group(0))
        # Generic price-action words ("jumps", "slides") only count in a clause that names this market,
        # so "S&P 500 jumps" in a Fed headline isn't read as gold rallying.
        clauses = [c for c in re.split(r"[;,.:]| as | while | after ", text) if _find(terms, c)]
        own = " | ".join(clauses)
        up, dn = _find(_PRICE_UP, own), _find(_PRICE_DOWN, own)
        if up and not dn:
            raw += 0.8
            why.append("+" + up[0])
        elif dn and not up:
            raw -= 0.8
            why.append("−" + dn[0])
        score = math.tanh(raw / 2.0)
        direction = "LONG" if score > 0.2 else "SHORT" if score < -0.2 else "NEUTRAL"
        # Being "about" a market only through macro words is weaker evidence than naming it
        direct = any(not re.search(r"fed|federal reserve|fomc|cpi|inflation|payrolls|treasury|usda|wasde", h) for h in hits)
        s = stars if direct else max(1, stars - 1)
        out.append(MarketSignal(sym, direction, s, round(score, 3), hits[:2] + why[:4] + star_why[:2]))
    return out
