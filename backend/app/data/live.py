"""Adapters for free, public data sources.

* CFTC Public Reporting (Socrata) - Disaggregated Futures-Only COT, no key needed.
* EIA Open Data v2 - weekly petroleum & natural gas storage (free API key).
* Yahoo Finance chart API - delayed continuous futures prices (unofficial; fine for
  prototyping, NOT licensed for redistribution - swap for a licensed feed such as
  CME via Databento/dxFeed/Rithmic before selling the product).

Every adapter returns a DataFrame in the same shape as the simulator, or raises.
Responses are cached on disk so restarts don't hammer the sources.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pandas as pd

from ..universe import Contract

CFTC_DISAGG_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"   # commodities
CFTC_TFF_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"      # financial futures
EIA_SERIES_URL = "https://api.eia.gov/v2/seriesid/{series}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

_HEADERS = {"User-Agent": "commodities-terminal/0.1"}


def _cached_json(cache_dir: Path, key: str, ttl_s: int, fetch) -> object:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        return json.loads(path.read_text())
    data = fetch()
    path.write_text(json.dumps(data))
    return data


def fetch_cot(c: Contract, cache_dir: Path, weeks: int = 210) -> pd.DataFrame:
    def _fetch():
        params = {
            "cftc_contract_market_code": c.cftc_code,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(weeks),
        }
        url = CFTC_TFF_URL if c.financial else CFTC_DISAGG_URL
        r = httpx.get(url, params=params, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()

    rows = _cached_json(cache_dir, f"cot_{c.cftc_code}", 6 * 3600, _fetch)
    if not rows:
        raise ValueError(f"No COT rows for {c.symbol}")

    def num(row, *keys):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return float(row[k])
        return 0.0

    recs = []
    for row in rows:
        if c.financial:
            # TFF report: leveraged funds = speculators ("managed money"), asset managers = real money
            recs.append({
                "date": pd.Timestamp(row["report_date_as_yyyy_mm_dd"][:10]),
                "open_interest": num(row, "open_interest_all"),
                "mm_long": num(row, "lev_money_positions_long"),
                "mm_short": num(row, "lev_money_positions_short"),
                "pm_long": num(row, "asset_mgr_positions_long"),
                "pm_short": num(row, "asset_mgr_positions_short"),
                "swap_net": num(row, "dealer_positions_long_all") - num(row, "dealer_positions_short_all"),
            })
            continue
        recs.append(
            {
                "date": pd.Timestamp(row["report_date_as_yyyy_mm_dd"][:10]),
                "open_interest": num(row, "open_interest_all"),
                "mm_long": num(row, "m_money_positions_long_all"),
                "mm_short": num(row, "m_money_positions_short_all"),
                "pm_long": num(row, "prod_merc_positions_long", "prod_merc_positions_long_all"),
                "pm_short": num(row, "prod_merc_positions_short", "prod_merc_positions_short_all"),
                "swap_net": num(row, "swap_positions_long_all")
                - num(row, "swap__positions_short_all", "swap_positions_short_all"),
            }
        )
    df = pd.DataFrame(recs).sort_values("date").reset_index(drop=True)
    df["mm_net"] = df["mm_long"] - df["mm_short"]
    df["pm_net"] = df["pm_long"] - df["pm_short"]
    return df


def fetch_eia_series(c: Contract, api_key: str, cache_dir: Path) -> pd.DataFrame:
    if not c.eia_series:
        raise ValueError(f"{c.symbol} has no EIA series")
    if not api_key:
        raise ValueError("EIA_API_KEY not set")

    def _fetch():
        r = httpx.get(
            EIA_SERIES_URL.format(series=c.eia_series),
            params={"api_key": api_key},
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["response"]["data"]

    rows = _cached_json(cache_dir, f"eia_{c.eia_series}", 6 * 3600, _fetch)
    df = pd.DataFrame(
        {"date": pd.to_datetime([r["period"] for r in rows]), "stocks": [float(r["value"]) for r in rows]}
    )
    df = df.sort_values("date").reset_index(drop=True)
    df["week"] = df["date"].dt.isocalendar().week.astype(int)
    return df


def fetch_yahoo_daily(c: Contract, cache_dir: Path, rng: str = "10y") -> pd.DataFrame:
    def _fetch():
        r = httpx.get(
            YAHOO_CHART_URL.format(symbol=c.yahoo),
            params={"range": rng, "interval": "1d"},
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    payload = _cached_json(cache_dir, f"yahoo_{c.yahoo}_{rng}", 1800, _fetch)
    res = payload["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(res["timestamp"], unit="s").normalize(),
            "open": q["open"],
            "high": q["high"],
            "low": q["low"],
            "close": q["close"],
            "volume": q["volume"],
        }
    ).dropna()
    df["second"] = float("nan")  # curve data needs a real futures feed
    return df.reset_index(drop=True)


def fetch_yahoo_last(c: Contract) -> float:
    r = httpx.get(
        YAHOO_CHART_URL.format(symbol=c.yahoo),
        params={"range": "1d", "interval": "1m"},
        headers=_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
