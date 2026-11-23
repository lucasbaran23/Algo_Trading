"""Research-backed intraday strategies.

Each strategy records the published evidence behind it (with links), the replication
caveats, an evidence grade, the timeframes worth testing, and an implementation that
runs on one regular-trading-hours (RTH) session of 1-minute bars.

Evidence grades
  A  Peer-reviewed, replicated across many markets / long samples.
  B  Peer-reviewed or strong working paper, but narrower or contested out of sample.
  C  Practitioner rule with weak or no peer-reviewed support (kept as a control).

A session `d` provides numpy arrays for the RTH session: m (minutes since RTH open, bar-close),
o, h, l, c, v; plus d["prev_close"] (prior RTH close), d["vwap"], d["weekday"], d["date"],
d["fomc"], d["sigma"] (noise-area profile from the prior 14 sessions, or None).
Each strategy returns a list of trades: (side, entry_price, exit_price).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class IntradayStrategy:
    key: str
    name: str
    grade: str
    family: str
    tf_label: str                 # what the "timeframe" dimension means for this strategy
    tf_values: list[int]          # minutes
    params: dict                  # other params -> list of values to test
    finding: str
    caveats: str
    how_to: str
    citations: list[dict]
    fn: Callable
    markets: tuple = ()           # empty = all markets
    extra: dict = field(default_factory=dict)

    def applies(self, symbol: str, sector: str) -> bool:
        return not self.markets or symbol in self.markets or sector in self.markets


def _idx_at(m: np.ndarray, minute: int) -> int:
    """Index of the last bar closing at or before `minute` after the open (-1 if none)."""
    return int(np.searchsorted(m, minute, side="right")) - 1


# ---------------------------------------------------------------------- 1. market intraday momentum
def intraday_momentum(d, tf, signal="first30"):
    m, c = d["m"], d["c"]
    n = m[-1]
    if n < 2 * tf + 30:
        return []
    k_sig = _idx_at(m, 30) if signal == "first30" else _idx_at(m, n - tf)
    k_in = _idx_at(m, n - tf)
    if k_sig < 0 or k_in < 0 or d["prev_close"] is None:
        return []
    r = c[k_sig] / d["prev_close"] - 1
    if r == 0:
        return []
    side = 1 if r > 0 else -1
    return [(side, c[k_in], c[-1])]


# ---------------------------------------------------------------------- 2. opening range breakout
def opening_range_breakout(d, tf, variant="breakout"):
    m, o, h, l, c = d["m"], d["o"], d["h"], d["l"], d["c"]
    k = _idx_at(m, tf)
    if k < 0 or k >= len(m) - 2:
        return []
    orh, orl = h[: k + 1].max(), l[: k + 1].min()
    if orh <= orl:
        return []
    if variant == "first_candle":
        # Zarattini-Barbon-Aziz: trade in the direction of the opening-range candle at the next bar's open,
        # stop at the opposite end of the range, target 10R, otherwise exit at the close.
        dirn = np.sign(c[k] - o[0])
        if dirn == 0:
            return []
        side = int(dirn)
        entry = o[k + 1]
        stop = orl if side > 0 else orh
        risk = abs(entry - stop)
        if risk <= 0:
            return []
        target = entry + side * 10 * risk
        for i in range(k + 1, len(c)):
            if (side > 0 and l[i] <= stop) or (side < 0 and h[i] >= stop):
                return [(side, entry, stop)]
            if (side > 0 and h[i] >= target) or (side < 0 and l[i] <= target):
                return [(side, entry, target)]
        return [(side, entry, c[-1])]
    # Classic (Crabel / Holmberg et al.): stop orders at the range extremes, first trigger wins,
    # protective stop at the other side of the range, exit at the close.
    for i in range(k + 1, len(c)):
        up, dn = h[i] > orh, l[i] < orl
        if not (up or dn):
            continue
        if up and dn:                                   # both in one minute: assume the nearer one first
            up = abs(o[i] - orh) <= abs(o[i] - orl)
        side = 1 if up else -1
        entry = max(o[i], orh) if side > 0 else min(o[i], orl)
        stop = orl if side > 0 else orh
        for j in range(i, len(c)):
            if j > i and ((side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop)):
                return [(side, entry, stop)]
        return [(side, entry, c[-1])]
    return []


# ---------------------------------------------------------------------- 3. noise-area momentum
def noise_area(d, tf, band=1.0):
    sig = d.get("sigma")
    if sig is None:
        return []
    m, c, vw = d["m"], d["c"], d["vwap"]
    o0, pc = d["o"][0], d["prev_close"] or d["o"][0]
    up_ref, dn_ref = max(o0, pc), min(o0, pc)
    trades, pos, entry = [], 0, 0.0
    checkpoints = list(range(tf, int(m[-1]) + 1, tf))
    for cp in checkpoints:
        k = _idx_at(m, cp)
        if k < 0:
            continue
        s = sig[min(k, len(sig) - 1)] * band
        upper, lower = up_ref * (1 + s), dn_ref * (1 - s)
        px = c[k]
        if pos > 0 and px < max(upper, vw[k]):
            trades.append((1, entry, px)); pos = 0
        elif pos < 0 and px > min(lower, vw[k]):
            trades.append((-1, entry, px)); pos = 0
        if pos == 0:
            if px > upper:
                pos, entry = 1, px
            elif px < lower:
                pos, entry = -1, px
    if pos:
        trades.append((pos, entry, c[-1]))
    return trades


# ---------------------------------------------------------------------- 4. VWAP trend
def vwap_trend(d, tf):
    m, c, vw = d["m"], d["c"], d["vwap"]
    trades, pos, entry = [], 0, 0.0
    marks = list(range(tf, int(m[-1]), tf))
    for cp in marks:
        k = _idx_at(m, cp)
        if k < 0:
            continue
        want = 1 if c[k] > vw[k] else -1 if c[k] < vw[k] else pos
        if want != pos:
            if pos:
                trades.append((pos, entry, c[k]))
            pos, entry = want, c[k]
    if pos:
        trades.append((pos, entry, c[-1]))
    return trades


# ---------------------------------------------------------------------- 5. pre-FOMC drift
def pre_fomc(d, tf):
    if not d.get("fomc"):
        return []
    m, c = d["m"], d["c"]
    k_exit = _idx_at(m, tf)          # tf = minutes after the open to exit (before the 14:00 ET statement)
    if k_exit <= 0:
        return []
    return [(1, d["o"][0], c[k_exit])]


# ---------------------------------------------------------------------- 6. EIA-day half-hour momentum
def eia_halfhour(d, tf):
    if d["weekday"] != 2:            # Wednesday = EIA Weekly Petroleum Status Report (10:30 ET)
        return []
    m, c = d["m"], d["c"]
    o_min = d["open_min"]            # RTH open in ET minutes
    k0, k1 = _idx_at(m, 10 * 60 + 30 - o_min), _idx_at(m, 11 * 60 - o_min)
    n = int(m[-1])
    k_in = _idx_at(m, n - tf)
    if min(k0, k1, k_in) < 0 or k_in <= k1:
        return []
    r = c[k1] / c[k0] - 1
    if r == 0:
        return []
    return [(1 if r > 0 else -1, c[k_in], c[-1])]


# ---------------------------------------------------------------------- 7. control: RSI(2) reversion
def rsi2_reversion(d, tf, lo=10):
    m, c = d["m"], d["c"]
    marks = [k for k in (_idx_at(m, cp) for cp in range(tf, int(m[-1]), tf)) if k >= 0]
    px = c[marks]
    if len(px) < 5:
        return []
    ch = np.diff(px)
    trades, pos, entry = [], 0, 0.0
    up = dn = 0.0
    for i in range(1, len(px)):
        g, ls = max(ch[i - 1], 0), max(-ch[i - 1], 0)
        up, dn = 0.5 * up + 0.5 * g, 0.5 * dn + 0.5 * ls
        if i < 3:
            continue
        rsi = 100 if dn == 0 else 100 - 100 / (1 + up / dn)
        if pos > 0 and rsi > 50 or pos < 0 and rsi < 50:
            trades.append((pos, entry, px[i])); pos = 0
        if pos == 0:
            if rsi < lo:
                pos, entry = 1, px[i]
            elif rsi > 100 - lo:
                pos, entry = -1, px[i]
    if pos:
        trades.append((pos, entry, c[-1]))
    return trades


CITE = {
    "gao": {"ref": "Gao, Han, Li & Zhou (2018), 'Market intraday momentum', Journal of Financial Economics 129(2), 394-414",
            "url": "https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414"},
    "baltussen": {"ref": "Baltussen, Da, Lammers & Martens (2021), 'Hedging demand and market intraday momentum', Journal of Financial Economics 142, 377-403",
                  "url": "https://ideas.repec.org/a/eee/jfinec/v142y2021i1p377-403.html"},
    "wen": {"ref": "Wen, Indriawan, Lien & Xu (2023), 'Intraday return predictability in the crude oil market: the role of EIA inventory announcements', The Energy Journal 44(5)",
            "url": "https://econpapers.repec.org/RePEc:aen:journl:ej44-5-xu"},
    "uso": {"ref": "Intraday momentum in crude oil (USO, 2006-2018), Economic Modelling 95 (2021), 374-384",
            "url": "https://econpapers.repec.org/RePEc:eee:ecmode:v:95:y:2021:i:c:p:374-384"},
    "holmberg": {"ref": "Holmberg, Lönnbark & Lundström (2013), 'Assessing the profitability of intraday opening range breakout strategies', Finance Research Letters 10(1), 27-33",
                 "url": "https://ideas.repec.org/a/eee/finlet/v10y2013i1p27-33.html"},
    "zarattini_orb": {"ref": "Zarattini, Barbon & Aziz (2023/24), 'Can Day Trading Really Be Profitable? / A Profitable Day Trading Strategy for the U.S. Equity Market', SSRN 4416622",
                      "url": "https://papers.ssrn.com/abstract=4416622"},
    "orb_replication": {"ref": "Independent replication on index CFDs (NQ, SPX, Dow, DAX, FTSE): gross edge reproduces, net of costs ≈ 0",
                        "url": "https://www.mql5.com/en/blogs/post/776235"},
    "beat": {"ref": "Zarattini, Aziz & Barbon (2024), 'Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)', Swiss Finance Institute RP 24-97",
             "url": "https://ideas.repec.org/p/chf/rpseri/rp2497.html"},
    "vwap": {"ref": "Zarattini & Aziz (2023), 'Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems', SSRN 4631351",
             "url": "https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/"},
    "vwap_repl": {"ref": "QuantConnect community replications report much weaker results",
                  "url": "https://www.quantconnect.com/forum/discussion/16706/bear-bull-traders-039-paper-on-quot-the-holy-grail-quot-not-so-fast/"},
    "lucca": {"ref": "Lucca & Moench (2015), 'The Pre-FOMC Announcement Drift', Journal of Finance 70(1)",
              "url": "https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr512.html"},
    "kurov": {"ref": "Kurov, Wolfe & Gilbert (2021), 'The disappearing pre-FOMC announcement drift', Finance Research Letters 40",
              "url": "https://ideas.repec.org/a/eee/finlet/v40y2021ics1544612320315956.html"},
}

STRATEGIES: list[IntradayStrategy] = [
    IntradayStrategy(
        "intraday_momentum", "Market intraday momentum (last-half-hour)", "A", "Momentum",
        "trade window: last N minutes of the session", [15, 30, 60], {"signal": ["first30", "rest_of_day"]},
        "The return from the prior close through the first half-hour (or through the rest of the day) predicts the "
        "direction of the final half-hour. Documented in the S&P 500 ETF 1993-2013, and in 60+ futures across "
        "equities, bonds, commodities and currencies 1974-2020, linked to option and leveraged-ETF gamma hedging.",
        "Small per-trade edge (OOS R² ≈ 1.2% in the original study), so costs matter. Strongest in equity indices on "
        "high-volatility days; weaker in some markets (overnight return may matter more than the first half-hour). "
        "Futures settle at slightly different times than ETFs.",
        "At N minutes before the close, take the sign of the return since yesterday's close: long if up, short if "
        "down, and exit at the close. Best on ES/NQ/YM; test before using on commodities.",
        [CITE["gao"], CITE["baltussen"], CITE["uso"]], intraday_momentum),
    IntradayStrategy(
        "orb", "Opening range breakout", "B", "Breakout",
        "opening-range length (minutes)", [5, 15, 30, 60], {"variant": ["breakout", "first_candle"]},
        "Breakouts of the first minutes' range have been studied in crude oil futures (Holmberg et al. 2013, "
        "bootstrap tests) and, in the 5-minute 'first candle' form, across 7,000+ U.S. stocks 2016-2023, with the "
        "biggest profits from a 5-minute opening range on 'stocks in play'.",
        "Results are sensitive to costs and selection: an independent replication on index CFDs found the gross edge "
        "reproduces but is ≈0 after costs. The stock version relies on picking high-relative-volume names, a filter "
        "futures don't have.",
        "Mark the high and low of the first N minutes after the RTH open. Breakout variant: buy above the high or sell "
        "below the low, stop at the other side, flat at the close. First-candle variant: trade the direction of the "
        "N-minute candle, stop at the range extreme, 10R target.",
        [CITE["holmberg"], CITE["zarattini_orb"], CITE["orb_replication"]], opening_range_breakout),
    IntradayStrategy(
        "noise_area", "Noise-area intraday momentum", "B", "Momentum",
        "decision interval (minutes)", [15, 30, 60], {"band": [1.0, 1.5]},
        "Bands = today's open ± the average absolute move from the open at each time of day over the last 14 days. "
        "Moves outside the 'noise area' signal imbalance: go with them, trail with max(band, VWAP), flat by the close. "
        "Reported Sharpe 1.33 net of costs on SPY 2007-2024 (19.6% a year).",
        "Single-instrument working paper. Public replications report weaker results. Uses volatility-targeted sizing, "
        "which this test ignores (1 contract).",
        "Every N minutes: above the upper band, go long; below the lower band, go short. Exit when price falls back "
        "inside max(band, VWAP). Flat at the close.",
        [CITE["beat"]], noise_area),
    IntradayStrategy(
        "vwap_trend", "VWAP trend following", "C", "Trend",
        "bar size (minutes)", [1, 5, 15], {},
        "Long above VWAP, short below, re-evaluated every bar. The authors report strong QQQ results 2018-2023.",
        "Disputed: replications put the out-of-sample Sharpe far below the paper's. High turnover makes it very "
        "cost-sensitive on futures.",
        "At each N-minute bar close: hold long if price is above session VWAP, short if below. Flat at the close.",
        [CITE["vwap"], CITE["vwap_repl"]], vwap_trend),
    IntradayStrategy(
        "pre_fomc", "Pre-FOMC drift", "B", "Event",
        "exit, minutes after the open", [240, 265], {},
        "The S&P 500 historically rose in the 24 hours before scheduled FOMC announcements (about 49 bp per meeting, "
        "1994-2011).",
        "The drift largely disappeared after 2015 (Kurov, Wolfe & Gilbert 2021). Only 8 events a year: tiny samples.",
        "On FOMC announcement days, buy the RTH open and exit before the 14:00 ET statement.",
        [CITE["lucca"], CITE["kurov"]], pre_fomc, markets=("ES", "NQ", "YM")),
    IntradayStrategy(
        "eia_halfhour", "EIA-day half-hour momentum (energy)", "B", "Event",
        "trade window: last N minutes", [15, 30], {},
        "On EIA inventory days the half-hour right after the 10:30 release predicts the last half-hour of the "
        "session; on other days the first half-hour does.",
        "Studied on the USO ETF rather than futures; settlement-time differences apply. About 50 events a year.",
        "Wednesdays: take the sign of the 10:30-11:00 ET return. In the last N minutes before the energy settle, "
        "trade in that direction and exit at the close.",
        [CITE["wen"], CITE["uso"]], eia_halfhour, markets=("CL", "RB", "HO")),
    IntradayStrategy(
        "rsi2_reversion", "RSI(2) intraday reversion (control)", "C", "Reversion",
        "bar size (minutes)", [1, 5, 15], {"lo": [10]},
        "A popular retail mean-reversion rule. Included as a control: no peer-reviewed evidence of an intraday edge "
        "in futures after costs.",
        "Expect it to lose after costs. If it ranks high on your data, suspect overfitting or a data issue.",
        "Buy RSI(2) < 10 and sell RSI(2) > 90 on N-minute bars, exit when RSI crosses 50.",
        [], rsi2_reversion),
]
BY_KEY = {s.key: s for s in STRATEGIES}
