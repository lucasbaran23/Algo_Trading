"""Gas-weighted heating degree days (HDD): history, 1-16 day forecast, and normals.

Natural gas demand in winter is driven by heating: HDD = max(0, 65°F - daily mean temperature).
A basket of cities in the big gas-heating regions (Midwest, Northeast, South Central), weighted
by approximate residential/commercial gas demand, stands in for the population-weighted HDD
series that desks buy from weather vendors.

Live (DATA_MODE=live): Open-Meteo, no API key.
  history : https://archive-api.open-meteo.com/v1/archive   (daily temperature_2m_mean, °F)
  forecast: https://api.open-meteo.com/v1/forecast          (16 days)
  Check Open-Meteo's current terms before commercial use; a paid plan may be required.
  Past years use OBSERVED weather (what actually happened). Archived *forecasts* (what traders
  saw at the time) need Open-Meteo's historical-forecast / previous-runs APIs or a vendor feed.

Sim: a seasonal temperature curve plus persistent anomalies. In sim mode the anomaly is partly
tied to the simulated gas price, so the price/weather panels have something to show. That link
is ILLUSTRATIVE ONLY and is labelled SIM in the UI.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# (name, lat, lon, weight)
CITIES = [
    ("Chicago", 41.88, -87.63, 0.16), ("New York", 40.71, -74.01, 0.16), ("Philadelphia", 39.95, -75.17, 0.07),
    ("Boston", 42.36, -71.06, 0.07), ("Detroit", 42.33, -83.05, 0.08), ("Minneapolis", 44.98, -93.27, 0.06),
    ("Columbus", 39.96, -83.00, 0.06), ("Pittsburgh", 40.44, -79.99, 0.05), ("Atlanta", 33.75, -84.39, 0.08),
    ("Dallas", 32.78, -96.80, 0.10), ("Houston", 29.76, -95.37, 0.07), ("Denver", 39.74, -104.99, 0.04),
]
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
_CACHE: dict[str, tuple[float, dict]] = {}


def _weighted_hdd(daily_by_city: list[pd.Series]) -> pd.Series:
    w = np.array([c[3] for c in CITIES])
    df = pd.concat(daily_by_city, axis=1)
    hdd = (65.0 - df).clip(lower=0)
    return (hdd * w).sum(axis=1) / w.sum()


def _fetch(url: str, params: dict) -> list[pd.Series]:
    import httpx
    p = {"latitude": ",".join(str(c[1]) for c in CITIES), "longitude": ",".join(str(c[2]) for c in CITIES),
         "daily": "temperature_2m_mean", "temperature_unit": "fahrenheit", "timezone": "America/New_York", **params}
    for attempt in range(3):                                    # Open-Meteo occasionally resets connections
        try:
            r = httpx.get(url, params=p, timeout=40)
            r.raise_for_status()
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    data = r.json()
    data = data if isinstance(data, list) else [data]
    return [pd.Series(d["daily"]["temperature_2m_mean"], index=pd.to_datetime(d["daily"]["time"]), dtype=float) for d in data]


def _live(cache_dir: Path, years: int) -> tuple[pd.Series, pd.Series]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / "ng_weather.json"
    if f.exists() and time.time() - f.stat().st_mtime < 6 * 3600:
        raw = json.loads(f.read_text())
        return pd.Series(raw["obs"]["v"], index=pd.to_datetime(raw["obs"]["i"])), pd.Series(raw["fc"]["v"], index=pd.to_datetime(raw["fc"]["i"]))
    end = date.today() - timedelta(days=6)                      # the archive lags by a few days
    obs = _weighted_hdd(_fetch(ARCHIVE, {"start_date": (end - timedelta(days=365 * years)).isoformat(), "end_date": end.isoformat()}))
    fc = _weighted_hdd(_fetch(FORECAST, {"forecast_days": 16, "past_days": 7}))
    f.write_text(json.dumps({"obs": {"i": [str(x.date()) for x in obs.index], "v": [float(v) for v in obs.fillna(0)]},
                             "fc": {"i": [str(x.date()) for x in fc.index], "v": [float(v) for v in fc.fillna(0)]}}))
    return obs.dropna(), fc.dropna()


def _sim(ng_daily: pd.DataFrame, seed: int) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed + 7)
    end = pd.Timestamp(date.today())
    idx = pd.date_range(end - pd.Timedelta(days=365 * 10), end + pd.Timedelta(days=16), freq="D")
    doy = idx.dayofyear.to_numpy()
    normal_temp = 55 - 22 * np.cos(2 * np.pi * (doy - 20) / 365.25)          # coldest ~Jan 20
    px = ng_daily.set_index(pd.to_datetime(ng_daily["date"]))["close"].reindex(idx).ffill().bfill()
    ret5 = np.log(px).diff(5).fillna(0).to_numpy()
    anom = np.zeros(len(idx))
    for i in range(1, len(idx)):
        anom[i] = 0.8 * anom[i - 1] + rng.normal(0, 3.2)
    # ILLUSTRATIVE link for the demo: colder-than-normal spells line up with rising simulated gas prices
    temp = normal_temp + anom - 12 * ret5 * (np.cos(2 * np.pi * (doy - 20) / 365.25) > 0)
    hdd = pd.Series(np.clip(65 - temp, 0, None), index=idx)
    today = pd.Timestamp(date.today())
    return hdd[hdd.index < today], hdd[hdd.index >= today - pd.Timedelta(days=7)]


def normals(obs: pd.Series, years: int = 10) -> pd.Series:
    """Normal HDD for each day of year: mean of the prior `years` years, ±3-day window."""
    s = obs[obs.index >= obs.index.max() - pd.DateOffset(years=years)]
    by_doy = s.groupby(s.index.dayofyear).mean()
    by_doy = by_doy.reindex(range(1, 367)).interpolate().bfill().ffill()
    sm = pd.concat([by_doy.iloc[-3:], by_doy, by_doy.iloc[:3]]).rolling(7, center=True).mean().iloc[3:-3]
    sm.index = range(1, 367)
    return sm


def weather(md, settings) -> dict:
    key = f"{settings.data_mode}:{date.today()}"
    hit = _CACHE.get("w")
    # a live result is reused for 3 h; a simulated fallback in live mode only for 5 min, then retried
    ttl = 300 if (settings.live and hit and hit[1].get("source") == "SIM") else 3 * 3600
    if hit and hit[1].get("_key") == key and time.time() - hit[0] < ttl:
        return hit[1]
    src = "SIM"
    obs = fc = None
    if settings.live:
        try:
            obs, fc = _live(settings.cache_dir, 10)
            src = "LIVE (Open-Meteo)"
        except Exception as exc:  # noqa: BLE001 - fall back to the simulator
            log.warning("Open-Meteo weather failed: %s", exc)
    if obs is None:
        obs, fc = _sim(md.daily["NG"], settings.sim_seed)
    norm = normals(obs)
    nrm = lambda idx: np.array([norm.loc[min(d.dayofyear, 366)] for d in idx])  # noqa: E731
    today = pd.Timestamp(date.today())
    f14 = fc[(fc.index >= today) & (fc.index < today + pd.Timedelta(days=14))]
    f_norm = nrm(f14.index)
    d7, d14 = f14.iloc[:7], f14
    n7, n14 = f_norm[:7].sum(), f_norm.sum()
    anom7 = float(d7.sum() - n7) if len(d7) else 0.0
    anom14 = float(d14.sum() - n14) if len(d14) else 0.0
    heating = today.month in (10, 11, 12, 1, 2, 3, 4)
    pct14 = 100 * anom14 / n14 if n14 else 0.0
    if not heating:
        bias, why = "NEUTRAL", "Outside the heating season, HDDs matter little: watch cooling demand (CDD) and power burn instead."
    elif pct14 >= 8:
        bias, why = "BULLISH", f"Next 14 days {pct14:+.0f}% colder than normal: more heating demand and bigger storage draws."
    elif pct14 <= -8:
        bias, why = "BEARISH", f"Next 14 days {abs(pct14):.0f}% warmer than normal: weaker heating demand and smaller draws."
    else:
        bias, why = "NEUTRAL", f"Next 14 days near normal ({pct14:+.0f}%): weather is not the driver; storage and supply lead."
    recent = obs[obs.index >= today - pd.Timedelta(days=21)]
    out = {
        "_key": key, "source": src, "heating_season": heating, "bias": bias, "bias_why": why,
        "next7": {"hdd": round(float(d7.sum()), 1), "normal": round(float(n7), 1), "anomaly": round(anom7, 1)},
        "next14": {"hdd": round(float(d14.sum()), 1), "normal": round(float(n14), 1), "anomaly": round(anom14, 1), "pct": round(pct14, 1)},
        "daily": [{"date": str(d.date()), "hdd": round(float(v), 1), "normal": round(float(n), 1), "kind": "obs"}
                  for d, v, n in zip(recent.index, recent.values, nrm(recent.index))] +
                 [{"date": str(d.date()), "hdd": round(float(v), 1), "normal": round(float(n), 1), "kind": "fc"}
                  for d, v, n in zip(f14.index, f14.values, f_norm)],
        "cities": [c[0] for c in CITIES],
        "_obs": obs, "_norm": norm,
    }
    _CACHE["w"] = (time.time(), out)
    return out


def month_anomalies(w: dict, month: int) -> dict[int, float]:
    """Total HDD anomaly in `month` for each past year (observed)."""
    obs, norm = w["_obs"], w["_norm"]
    s = obs[obs.index.month == month]
    out = {}
    for y, g in s.groupby(s.index.year):
        if len(g) < 20:
            continue
        out[int(y)] = round(float(g.sum() - sum(norm.loc[d.dayofyear] for d in g.index)), 1)
    return out
