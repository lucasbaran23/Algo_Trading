"""U.S. natural gas supply & demand balance.

Monthly (EIA Short-Term Energy Outlook, Table 5a, keyless download of STEO_m.xlsx)
  Supply : dry production (+ marketed production by basin: Permian, Haynesville, Appalachia, ...),
           pipeline imports (Canada), LNG imports, supplemental fuels
  Demand : residential, commercial, industrial, electric power (power burn), lease & plant fuel,
           pipeline & distribution use, vehicle fuel, LNG exports (~ feedgas), pipeline exports (Mexico)
  Storage: net withdrawals and end-of-month working gas
  History and EIA's forecast in one consistent table, in Bcf/d. The forecast start is read from the
  workbook's shading (EIA shades estimates and forecasts).

Weekly (computed here from real data; no free weekly S&D feed exists)
  Weather-adjusted balance: fit the weekly EIA storage change on that week's gas-weighted HDD and CDD
  (Open-Meteo) over the trailing 3 years, plus a trend. The residual = how much looser (+) or tighter (-)
  the market was than weather alone explains, in Bcf/d. A run of negative residuals = demand (LNG,
  power) or weak production is tightening the balance: bullish.

Note: there is no free daily pipeline-flow data (LNG feedgas, production nominations); those come
from paid vendors (e.g. Wood Mackenzie, LSEG, Criterion). STEO's LNG exports are a monthly proxy for
feedgas (feedgas runs ~5-8% higher because liquefaction uses some gas).
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

STEO_URL = "https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx"
SUPPLY = [("NGPRPUS", "Dry production"), ("NGIMPUS_PIPE", "Pipeline imports (Canada)"), ("NGIMPUS_LNG", "LNG imports"),
          ("NGSFPUS", "Supplemental fuels")]
DEMAND = [("NGRCPUS", "Residential"), ("NGCCPUS", "Commercial"), ("NGINX", "Industrial"), ("NGEPCON", "Power burn"),
          ("NGLPPUS", "Lease & plant fuel"), ("NGACPUS", "Pipeline & distribution"), ("NGVHPUS", "Vehicle"),
          ("NGEXPUS_LNG", "LNG exports (≈ feedgas)"), ("NGEXPUS_PIPE", "Pipeline exports (Mexico)")]
BASINS = [("NGMPPM", "Permian"), ("NGMPHA", "Haynesville"), ("NGMPAP", "Appalachia"), ("NGMPEF", "Eagle Ford"),
          ("NGMPBK", "Bakken"), ("NGMPPGLF", "Gulf of America"), ("NGMPR48", "Rest of Lower 48")]
OTHER = [("NGTCPUS", "Total consumption"), ("NGNWPUS", "Net storage withdrawals"), ("NGWGPUS", "Working gas, end of month"),
         ("NGMPPUS", "Marketed production"), ("BALIT", "Balancing item")]
_CACHE: dict = {}


# ---------------------------------------------------------------------- STEO
def _download(cache_dir: Path) -> Path:
    import httpx
    p = Path(cache_dir) / "eia_steo_m.xlsx"
    if not p.exists() or time.time() - p.stat().st_mtime > 24 * 3600:
        r = httpx.get(STEO_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True)
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    return p


def parse_steo(path: Path) -> tuple[pd.DataFrame, pd.Timestamp | None, str]:
    """Table 5a as a monthly DataFrame (index month start, columns = series codes, Bcf/d; storage in Bcf)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["5atab"]
    years, months = [c.value for c in ws[3]], [c.value for c in ws[4]]
    forecast_date = str(ws.cell(4, 1).value or "")
    cols, y = [], None
    for j in range(2, len(months)):
        y = years[j] or y
        if months[j] and y:
            cols.append((j, pd.Timestamp(f"{int(y)}-{months[j]}-01")))
    data, fc_start = {}, None
    for row in ws.iter_rows(min_row=5, max_row=ws.max_row):
        code = row[0].value
        if not code or not isinstance(code, str) or code.startswith("Table"):
            continue
        vals = []
        for j, ts in cols:
            v = row[j].value
            vals.append(float(v) if isinstance(v, (int, float)) else np.nan)
            f = row[j].fill.fgColor
            if fc_start is None and code == "NGPRPUS" and f.type == "theme" and (f.tint or 0) < 0:
                fc_start = ts
        data[code.strip()] = vals
    df = pd.DataFrame(data, index=pd.DatetimeIndex([ts for _, ts in cols], name="month"))
    return df, fc_start, forecast_date


def _sim_steo(md) -> tuple[pd.DataFrame, pd.Timestamp, str]:
    idx = pd.date_range("2022-01-01", "2027-12-01", freq="MS")
    t = np.arange(len(idx))
    m = idx.month.to_numpy()
    heat = np.cos(2 * np.pi * (m - 1) / 12)                       # +1 in January
    cool = np.clip(np.cos(2 * np.pi * (m - 7.5) / 12), 0, None)  # summer peak
    df = pd.DataFrame(index=idx)
    df["NGPRPUS"] = 98 + 0.25 * t
    df["NGIMPUS_PIPE"] = 8 + 1.0 * heat
    df["NGIMPUS_LNG"] = 0.1
    df["NGSFPUS"] = 0.2
    df["NGRCPUS"] = 13 + 11 * heat
    df["NGCCPUS"] = 9 + 5.5 * heat
    df["NGINX"] = 23 + 1.5 * heat
    df["NGEPCON"] = 30 + 10 * cool + 1.5 * heat
    df["NGLPPUS"], df["NGACPUS"], df["NGVHPUS"] = 5.0 + 0.01 * t, 3.4 + 1.2 * heat, 0.2
    df["NGEXPUS_LNG"] = 11 + 0.17 * t
    df["NGEXPUS_PIPE"] = 6.5 + 0.06 * t + 0.6 * cool
    for code, _ in BASINS:
        df[code] = df["NGPRPUS"] / len(BASINS)
    df["NGTCPUS"] = df[["NGRCPUS", "NGCCPUS", "NGINX", "NGEPCON", "NGLPPUS", "NGACPUS", "NGVHPUS"]].sum(axis=1)
    sup = df["NGPRPUS"] + df["NGIMPUS_PIPE"] + df["NGIMPUS_LNG"] + df["NGSFPUS"]
    dem = df["NGTCPUS"] + df["NGEXPUS_LNG"] + df["NGEXPUS_PIPE"]
    df["NGNWPUS"] = dem - sup
    df["NGWGPUS"] = (2500 - (df["NGNWPUS"] * idx.days_in_month).cumsum()).clip(1500, 4200)
    df["NGMPPUS"] = df["NGPRPUS"] * 1.08
    df["BALIT"] = 0.0
    today = pd.Timestamp.today().normalize()
    return df, pd.Timestamp(today.year, today.month, 1), "SIM"


def steo(md) -> tuple[pd.DataFrame, pd.Timestamp | None, str, str]:
    key = (md.settings.data_mode, str(pd.Timestamp.today().date()))
    hit = _CACHE.get("steo")
    if hit and hit[0] == key:
        return hit[1]
    out = None
    if md.settings.live:
        try:
            df, fc, fdate = parse_steo(_download(md.settings.cache_dir))
            out = (df, fc, fdate, "LIVE (EIA STEO)")
        except Exception as exc:  # noqa: BLE001
            log.warning("EIA STEO failed: %s", exc)
    if out is None:
        df, fc, fdate = _sim_steo(md)
        out = (df, fc, fdate, "SIM")
    _CACHE["steo"] = (key, out)
    return out


# ---------------------------------------------------------------------- weekly weather-adjusted balance
def weekly_adjusted(md, settings) -> dict:
    from . import weather as wx
    inv = md.inventory.get("NG")
    w = wx.weather(md, settings)
    if inv is None or len(inv) < 60:
        return {"available": False}
    obs_hdd, obs_cdd = w.get("_obs"), w.get("_obs_cdd")
    if obs_hdd is None:
        return {"available": False}
    if obs_cdd is None:
        obs_cdd = pd.Series(0.0, index=obs_hdd.index)
    s = inv[["date", "stocks"]].copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.set_index("date")["stocks"]
    chg = s.diff()                                                    # week ending Friday
    rows = []
    for d, v in chg.dropna().items():
        win = (obs_hdd.index > d - pd.Timedelta(days=7)) & (obs_hdd.index <= d)
        if win.sum() < 6:
            continue
        rows.append({"date": d, "chg": float(v), "hdd": float(obs_hdd[win].sum()), "cdd": float(obs_cdd.reindex(obs_hdd.index[win]).fillna(0).sum())})
    df = pd.DataFrame(rows)
    if len(df) < 80:
        return {"available": False, "note": "not enough overlapping weather history"}
    df["t"] = (df["date"] - df["date"].iloc[0]).dt.days / 365.25
    out, resid = [], []
    for i in range(len(df)):
        tr = df[(df["date"] < df["date"].iloc[i]) & (df["date"] >= df["date"].iloc[i] - pd.Timedelta(days=3 * 365))]
        if len(tr) < 100:
            continue
        X = np.column_stack([np.ones(len(tr)), tr["hdd"], tr["cdd"], tr["t"]])
        beta, *_ = np.linalg.lstsq(X, tr["chg"].to_numpy(), rcond=None)
        r = df.iloc[i]
        pred = float(beta @ np.array([1, r["hdd"], r["cdd"], r["t"]]))
        resid.append(r["chg"] - pred)
        out.append({"date": str(r["date"].date()), "actual": round(float(r["chg"]), 0), "weather_model": round(pred, 0),
                    "residual_bcf": round(float(r["chg"] - pred), 0), "residual_bcfd": round(float(r["chg"] - pred) / 7, 2),
                    "hdd": round(float(r["hdd"]), 0), "cdd": round(float(r["cdd"]), 0)})
    if not out:
        return {"available": False}
    last4 = np.mean([x["residual_bcfd"] for x in out[-4:]])
    sd = float(np.std([x["residual_bcfd"] for x in out[-156:]]) or 1)
    return {"available": True, "weeks": out[-52:], "last4_bcfd": round(float(last4), 2), "z": round(float(last4 / (sd / 2)), 2),
            "verdict": "TIGHTER than weather explains" if last4 < -0.5 else "LOOSER than weather explains" if last4 > 0.5 else "IN LINE with weather",
            "source": w["source"], "model": "weekly storage change ~ HDD + CDD + trend, trailing 3 years"}


# ---------------------------------------------------------------------- assemble
def balance(md) -> dict:
    df, fc_start, fdate, source = steo(md)
    df = df.copy()
    have = lambda codes: [c for c, _ in codes if c in df]  # noqa: E731
    df["SUPPLY"] = df[have(SUPPLY)].sum(axis=1)
    df["DEMAND"] = df[have(DEMAND)].sum(axis=1)
    # implied storage change (+ injection, - withdrawal); EIA's balancing item closes the identity
    df["BAL"] = df["SUPPLY"] - df["DEMAND"] + (df["BALIT"].fillna(0) if "BALIT" in df else 0)
    last_hist = df.index[df.index < fc_start][-1] if fc_start is not None and (df.index < fc_start).any() else df.index[-1]
    cur = fc_start if fc_start is not None else last_hist
    yoy = lambda col, ts: (df.at[ts, col] - df.at[ts - pd.DateOffset(years=1), col]) if ts - pd.DateOffset(years=1) in df.index else None  # noqa: E731
    rnd = lambda v, d=2: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), d)  # noqa: E731

    def comp(codes, ts):
        return [{"code": c, "name": n, "value": rnd(df.at[ts, c]), "yoy": rnd(yoy(c, ts))} for c, n in codes if c in df]

    months = [ts for ts in df.index if last_hist - pd.DateOffset(months=11) <= ts <= last_hist + pd.DateOffset(months=12)]
    table = []
    for ts in months:
        row = {"month": ts.strftime("%Y-%m"), "forecast": bool(fc_start is not None and ts >= fc_start)}
        for c, _ in SUPPLY + DEMAND + [("SUPPLY", ""), ("DEMAND", ""), ("BAL", ""), ("NGWGPUS", ""), ("NGTCPUS", "")]:
            if c in df:
                row[c] = rnd(df.at[ts, c])
                row[c + "_yoy"] = rnd(yoy(c, ts))
        table.append(row)
    series = [{"month": ts.strftime("%Y-%m"), "forecast": bool(fc_start is not None and ts >= fc_start),
               **{c: rnd(df.at[ts, c]) for c in [x for x, _ in SUPPLY + DEMAND] + ["SUPPLY", "DEMAND", "BAL", "NGWGPUS"] if c in df}}
              for ts in df.index]
    # end-of-March storage forecast vs the 5-year average for end of March (EIA weekly history)
    eom = None
    mar = [ts for ts in df.index if ts.month == 3 and ts >= last_hist]
    inv = md.inventory.get("NG")
    if mar and "NGWGPUS" in df and inv is not None:
        ts = mar[0]
        h = inv.copy()
        h["date"] = pd.to_datetime(h["date"])
        prev = [float(h[(h["date"].dt.year == y) & (h["date"].dt.month.isin([3, 4]))].iloc[(h[(h["date"].dt.year == y) & (h["date"].dt.month.isin([3, 4]))]["date"] - pd.Timestamp(f"{y}-03-31")).abs().argsort()[:1]]["stocks"].iloc[0])
                for y in range(ts.year - 5, ts.year) if ((h["date"].dt.year == y) & (h["date"].dt.month.isin([3, 4]))).any()]
        if prev:
            eom = {"month": ts.strftime("%Y-%m"), "forecast_bcf": rnd(df.at[ts, "NGWGPUS"], 0), "avg5_bcf": round(float(np.mean(prev)), 0),
                   "vs_avg_pct": rnd(100 * (df.at[ts, "NGWGPUS"] / np.mean(prev) - 1), 1)}
    # drivers: next 6 months vs the same months a year earlier
    nxt = [ts for ts in df.index if cur <= ts < cur + pd.DateOffset(months=6)]
    drivers = []
    for c, n in SUPPLY + DEMAND:
        if c in df and nxt:
            ch = np.nanmean([yoy(c, ts) for ts in nxt if yoy(c, ts) is not None]) if any(yoy(c, ts) is not None for ts in nxt) else None
            if ch is not None and math.isfinite(ch):
                drivers.append({"code": c, "name": n, "side": "supply" if c in dict(SUPPLY) else "demand", "yoy_bcfd": round(float(ch), 2)})
    drivers.sort(key=lambda x: -abs(x["yoy_bcfd"]))
    sup6 = sum(x["yoy_bcfd"] for x in drivers if x["side"] == "supply")
    dem6 = sum(x["yoy_bcfd"] for x in drivers if x["side"] == "demand")
    net = sup6 - dem6
    bullets = [f"Next 6 months vs a year earlier: supply {sup6:+.1f} Bcf/d, demand {dem6:+.1f} Bcf/d → balance {net:+.1f} Bcf/d "
               f"({'looser: bearish' if net > 0.5 else 'tighter: bullish' if net < -0.5 else 'little change'})."]
    top = [x for x in drivers if abs(x["yoy_bcfd"]) >= 0.3][:4]
    if top:
        bullets.append("Biggest changes: " + ", ".join(f"{x['name']} {x['yoy_bcfd']:+.1f}" for x in top) + " Bcf/d.")
    if eom:
        bullets.append(f"EIA sees end-of-March storage at {eom['forecast_bcf']:,.0f} Bcf, {eom['vs_avg_pct']:+.1f}% vs the 5-year average "
                       f"({'a comfortable cushion' if eom['vs_avg_pct'] > 3 else 'a thin cushion: upside risk on cold' if eom['vs_avg_pct'] < -3 else 'close to normal'}).")
    lng = next((x for x in drivers if x["code"] == "NGEXPUS_LNG"), None)
    if lng and lng["yoy_bcfd"] > 1:
        bullets.append(f"LNG is the swing: exports up {lng['yoy_bcfd']:+.1f} Bcf/d y/y. Terminal outages or new trains moving feedgas by 1-2 Bcf/d can swing storage by 7-14 Bcf a week.")
    wk = weekly_adjusted(md, md.settings)
    if wk.get("available"):
        bullets.append(f"Last 4 EIA reports: the market was {wk['last4_bcfd']:+.1f} Bcf/d vs what weather explains: {wk['verdict'].lower()}.")
    return {"source": source, "forecast_date": fdate, "forecast_start": fc_start.strftime("%Y-%m") if fc_start is not None else None,
            "last_actual": last_hist.strftime("%Y-%m"), "current_month": cur.strftime("%Y-%m"),
            "supply": comp(SUPPLY, cur), "demand": comp(DEMAND, cur), "basins": comp(BASINS, last_hist),
            "totals": {"supply": rnd(df.at[cur, "SUPPLY"]), "demand": rnd(df.at[cur, "DEMAND"]), "balance": rnd(df.at[cur, "BAL"]),
                       "supply_yoy": rnd(yoy("SUPPLY", cur)), "demand_yoy": rnd(yoy("DEMAND", cur)), "balance_yoy": rnd(yoy("BAL", cur))},
            "table": table, "series": series, "drivers": drivers, "end_of_march": eom, "weekly": wk, "bullets": bullets,
            "labels": dict(SUPPLY + DEMAND + BASINS + OTHER)}
