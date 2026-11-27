"""EIA Open Data API v2 (free key: https://www.eia.gov/opendata/register.php), set EIA_API_KEY.

Daily power burn
  EIA-930 Hourly Electric Grid Monitor, daily totals by fuel for the Lower 48
  (electricity/rto/daily-fuel-type-data, respondent US48, fuel NG): gas-fired generation in MWh,
  published with about a 1-day lag. Converted to Bcf/d with a factor calibrated against the EIA
  STEO monthly power-burn figures for the same months (gas fleet heat rate ~7-7.5 MMBtu/MWh), or
  a default of 7.4 MMBtu/MWh / 1.037 MMBtu per Mcf if calibration is not possible.
  This is the closest free thing to the daily power-burn number desks get from pipeline data.

Henry Hub spot
  natural-gas/pri/fut, series RNGWHHD (daily, $/MMBtu): the physical cash price at the delivery point.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = "https://api.eia.gov/v2/"
REGISTER = "https://www.eia.gov/opendata/register.php"
DEFAULT_BCF_PER_MWH = 7.4 / 1.037 / 1e6          # MMBtu/MWh / (MMBtu/Mcf) -> Mcf/MWh -> Bcf (1e6 Mcf)


def _get(route: str, params: list[tuple[str, str]], key: str, cache: Path, ttl: int) -> list[dict]:
    import httpx
    import hashlib
    tag = route.strip("/").replace("/", "_") + "_" + hashlib.sha1(repr(params).encode()).hexdigest()[:10]
    f = cache / f"eia_api_{tag}.json"
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        return json.loads(f.read_text())
    r = httpx.get(BASE + route.strip("/") + "/data/", params=[("api_key", key)] + params, timeout=40)
    r.raise_for_status()
    rows = r.json()["response"]["data"]
    cache.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows))
    return rows


def power_burn(settings, steo_df: pd.DataFrame | None = None, days: int = 400) -> dict:
    key = settings.eia_api_key
    if not key:
        return {"available": False, "reason": "EIA_API_KEY not set", "register": REGISTER}
    start = (date.today() - timedelta(days=days)).isoformat()
    rows = _get("electricity/rto/daily-fuel-type-data",
                [("frequency", "daily"), ("data[0]", "value"), ("facets[respondent][]", "US48"), ("facets[fueltype][]", "NG"),
                 ("facets[timezone][]", "Eastern"), ("start", start), ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
                 ("length", "5000")], key, Path(settings.cache_dir), 3 * 3600)
    if not rows:
        return {"available": False, "reason": "EIA returned no rows"}
    s = pd.Series({pd.Timestamp(r["period"]): float(r["value"]) for r in rows if r.get("value") is not None}).sort_index()
    factor, how = DEFAULT_BCF_PER_MWH, "default heat rate 7.4 MMBtu/MWh"
    if steo_df is not None and "NGEPCON" in steo_df:
        m = s.resample("MS").mean()
        both = pd.concat([m.rename("mwh"), steo_df["NGEPCON"].rename("bcfd")], axis=1, join="inner").dropna()
        both = both[both.index < pd.Timestamp(date.today().replace(day=1))].tail(12)
        if len(both) >= 3:
            factor = float((both["bcfd"] / both["mwh"]).median())
            how = f"calibrated to EIA STEO monthly power burn ({len(both)} months)"
    bcfd = s * factor
    last = bcfd.index[-1]
    yago = bcfd.reindex([last - pd.DateOffset(years=1)], method="nearest", tolerance=pd.Timedelta(days=3))
    d7 = bcfd.tail(7).mean()
    d7_ya = bcfd[(bcfd.index > last - pd.DateOffset(years=1) - pd.Timedelta(days=7)) & (bcfd.index <= last - pd.DateOffset(years=1))].mean()
    return {"available": True, "source": "EIA-930 (Hourly Electric Grid Monitor), daily, Lower 48", "conversion": how,
            "factor_bcf_per_mwh": factor, "last_date": str(last.date()), "last_bcfd": round(float(bcfd.iloc[-1]), 2),
            "avg7_bcfd": round(float(d7), 2), "avg7_yago_bcfd": round(float(d7_ya), 2) if np.isfinite(d7_ya) else None,
            "yoy_7d": round(float(d7 - d7_ya), 2) if np.isfinite(d7_ya) else None,
            "yago_same_day": round(float(yago.iloc[0]), 2) if len(yago) else None,
            "daily": [{"date": str(d.date()), "bcfd": round(float(v), 2), "mwh": round(float(s[d]), 0)} for d, v in bcfd.tail(120).items()]}


def henry_hub(settings, days: int = 400) -> dict:
    key = settings.eia_api_key
    if not key:
        return {"available": False, "reason": "EIA_API_KEY not set", "register": REGISTER}
    rows = _get("natural-gas/pri/fut", [("frequency", "daily"), ("data[0]", "value"), ("facets[series][]", "RNGWHHD"),
                                         ("start", (date.today() - timedelta(days=days)).isoformat()),
                                         ("sort[0][column]", "period"), ("sort[0][direction]", "asc"), ("length", "5000")],
                key, Path(settings.cache_dir), 3 * 3600)
    s = pd.Series({pd.Timestamp(r["period"]): float(r["value"]) for r in rows if r.get("value") is not None}).sort_index()
    if s.empty:
        return {"available": False, "reason": "EIA returned no rows"}
    return {"available": True, "source": "EIA, Henry Hub natural gas spot (RNGWHHD)", "last_date": str(s.index[-1].date()),
            "last": round(float(s.iloc[-1]), 3), "chg_1w": round(float(s.iloc[-1] - s.iloc[-6]), 3) if len(s) > 6 else None,
            "daily": [{"date": str(d.date()), "price": round(float(v), 3)} for d, v in s.tail(120).items()]}
