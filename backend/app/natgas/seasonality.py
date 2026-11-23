"""Natural gas seasonality, macro to micro, and the next-session volatility outlook.

Input: the continuous front-month daily series (simulator, Yahoo NG=F in live mode, or your
own data). Every day is labelled with its front contract and trading days to expiry; the first
day of each new contract is excluded from return/volatility stats, so roll gaps between
contracts don't pollute the numbers.

Layers
  year x month   monthly return / volatility / range for every year
  month profile  average behaviour of each calendar month (and which contract is front)
  analogs        this month's path in each past year, the average path, and this year so far
  weekday x month  average daily range by weekday for every month
  expiry cycle   range relative to the month's normal, by trading days to expiry
  holidays       ranges the day before / after U.S. exchange holidays
  weather link   each year's HDD anomaly in this month vs the month's return
  outlook        expected range for the next session: recent volatility x weekday x expiry x holiday
Ranges are reported in % of price, in $ per NG contract (10,000 MMBtu), and in ticks.
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..daytrade import calendar as cal
from . import contracts as ngc
from . import weather as wx

WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
_CACHE: dict[str, tuple[float, dict]] = {}


def frame(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily[["date", "open", "high", "low", "close"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    lab = ngc.label_days(d["date"])
    d["contract"] = [x["contract"] for x in lab]
    d["cmonth"] = [x["month"] for x in lab]
    d["tdte"] = [x["tdte"] for x in lab]
    d["roll"] = d["contract"] != d["contract"].shift()
    d.loc[d.index[0], "roll"] = True
    prev = d["close"].shift()
    d["ret"] = np.log(d["close"] / prev)
    d.loc[d["roll"], "ret"] = np.nan                       # contract switch: not a real return
    d["rng"] = (d["high"] - d["low"]) / prev
    d["year"], d["month"], d["wd"] = d["date"].dt.year, d["date"].dt.month, d["date"].dt.weekday
    d["abs"] = d["ret"].abs()
    return d.iloc[1:].reset_index(drop=True)


def _month_table(d: pd.DataFrame) -> list[dict]:
    rows = []
    for (y, m), g in d.groupby(["year", "month"]):
        r = g["ret"].dropna()
        if len(g) < 10:
            continue
        rows.append({"year": int(y), "month": int(m), "ret_pct": round(100 * (math.exp(r.sum()) - 1), 2),
                     "vol_pct": round(100 * r.std() * math.sqrt(252), 1), "range_pct": round(100 * g["rng"].mean(), 2),
                     "contract": g["contract"].mode().iat[0], "days": int(len(g))})
    return rows


def _profile(d: pd.DataFrame, price: float, pv: float, tick: float) -> list[dict]:
    out = []
    yearly = d.groupby(["year", "month"])["ret"].sum()
    for m in range(1, 13):
        g = d[d["month"] == m]
        if g.empty:
            continue
        ym = yearly.xs(m, level=1) if m in yearly.index.get_level_values(1) else pd.Series(dtype=float)
        rng = g["rng"].mean()
        out.append({"month": m, "name": ngc.MONTH_NAMES[m - 1][:3], "front": ngc.MONTH_NAMES[g["cmonth"].mode().iat[0] - 1][:3],
                    "years": int(len(ym)), "avg_ret_pct": round(100 * float(np.expm1(ym).mean()), 2) if len(ym) else None,
                    "up_pct": round(100 * float((ym > 0).mean()), 0) if len(ym) else None,
                    "vol_pct": round(100 * g["ret"].std() * math.sqrt(252), 1),
                    "range_pct": round(100 * rng, 2), "range_usd": round(rng * price * pv, 0),
                    "range_ticks": int(round(rng * price / tick))})
    return out


def _weekday_month(d: pd.DataFrame) -> dict:
    allm = d["rng"].mean()
    table = []
    for m in range(1, 13):
        g = d[d["month"] == m]
        mm = g["rng"].mean()
        row = {"month": m, "name": ngc.MONTH_NAMES[m - 1][:3], "cells": []}
        for w in range(5):
            gw = g[g["wd"] == w]
            row["cells"].append({"wd": w, "n": int(len(gw)), "range_pct": round(100 * gw["rng"].mean(), 2) if len(gw) else None,
                                 "factor": round(float(gw["rng"].mean() / mm), 2) if len(gw) and mm else None,
                                 "up_pct": round(100 * float((gw["ret"] > 0).mean()), 0) if len(gw) else None})
        table.append(row)
    overall = [{"wd": w, "factor": round(float(d[d["wd"] == w]["rng"].mean() / allm), 2)} for w in range(5)]
    return {"table": table, "overall": overall}


def _expiry_curve(d: pd.DataFrame, max_t: int = 20) -> list[dict]:
    mm = d.groupby(["year", "month"])["rng"].transform("mean")
    rel = d["rng"] / mm
    out = []
    for t in range(0, max_t + 1):
        s = rel[d["tdte"] == t]
        if len(s) >= 5:
            out.append({"tdte": t, "factor": round(float(s.mean()), 3), "n": int(len(s)),
                        "abs_ret_pct": round(100 * float(d.loc[d["tdte"] == t, "abs"].mean()), 2)})
    return out


def _hol_between(a: date, b: date) -> bool:
    """Is there an exchange holiday strictly between dates a and b?"""
    return any(cal.holiday(a + timedelta(days=k)) for k in range(1, (b - a).days))


def holiday_adjacent(x: date) -> tuple[bool, bool]:
    """(day before a holiday, day after a holiday) for a trading date."""
    return (_hol_between(x, ngc.add_business_days(x, 1)), _hol_between(ngc.add_business_days(x, -1), x))


def _holidays(d: pd.DataFrame) -> dict:
    mm = d.groupby(["year", "month"])["rng"].transform("mean")
    rel = d["rng"] / mm
    flags = [holiday_adjacent(x) for x in d["date"].dt.date]
    before = pd.Series([f[0] for f in flags], index=d.index)
    after = pd.Series([f[1] for f in flags], index=d.index)
    return {"before": {"factor": round(float(rel[before].mean()), 2) if before.any() else None, "n": int(before.sum())},
            "after": {"factor": round(float(rel[after].mean()), 2) if after.any() else None, "n": int(after.sum())}}


def _analogs(d: pd.DataFrame, month: int, today: date) -> dict:
    lines = []
    for y, g in d[d["month"] == month].groupby("year"):
        r = g["ret"].fillna(0).to_numpy()
        path = (np.exp(np.cumsum(r)) - 1) * 100
        lines.append({"year": int(y), "path": [0.0] + [round(float(v), 2) for v in path], "current": int(y) == today.year})
    past = [ln["path"] for ln in lines if not ln["current"]]
    n = max((len(p) for p in past), default=0)
    avg = [round(float(np.mean([p[i] for p in past if len(p) > i])), 2) for i in range(n)]
    return {"month": month, "name": ngc.MONTH_NAMES[month - 1], "lines": lines, "average": avg}


def build(md, settings, symbol_price: float | None = None) -> dict:
    key = f"{len(md.daily['NG'])}:{md.daily['NG']['date'].iloc[-1]}:{date.today()}"
    hit = _CACHE.get("s")
    if hit and hit[1]["_key"] == key and time.time() - hit[0] < 3600:
        return hit[1]
    from .. import universe
    c = universe.get("NG")
    d = frame(md.daily["NG"])
    price = float(md.daily["NG"]["close"].iloc[-1])
    today = date.today()
    w = wx.weather(md, settings)
    month = today.month
    anomalies = wx.month_anomalies(w, month)
    mt = _month_table(d)
    link = [{"year": r["year"], "ret_pct": r["ret_pct"], "vol_pct": r["vol_pct"], "hdd_anom": anomalies.get(r["year"])}
            for r in mt if r["month"] == month and r["year"] != today.year]
    pairs = [(x["hdd_anom"], x["ret_pct"]) for x in link if x["hdd_anom"] is not None]
    corr = float(np.corrcoef(*zip(*pairs))[0, 1]) if len(pairs) >= 4 else None
    out = {
        "_key": key, "source": md.sources.get("NG", {}).get("prices", "SIM"), "price": price,
        "years": f"{d['year'].min()}–{d['year'].max()}", "sessions": int(len(d)),
        "year_month": mt, "profile": _profile(d, price, c.point_value, c.tick_size),
        "weekday_month": _weekday_month(d), "expiry_curve": _expiry_curve(d), "holidays": _holidays(d),
        "analogs": _analogs(d, month, today),
        "weather_link": {"month": ngc.MONTH_NAMES[month - 1], "rows": link,
                         "corr": round(corr, 2) if corr is not None and math.isfinite(corr) else None,
                         "source": w["source"]},
    }
    _CACHE["s"] = (time.time(), out)
    out["_frame"] = d
    return out


def outlook(md, settings, now_et) -> dict:
    """Expected volatility and context for the next NG session."""
    from .. import universe
    c = universe.get("NG")
    s = build(md, settings)
    d = s["_frame"]
    day = cal.next_session_date(now_et)
    info = ngc.contract_info(day)
    price = float(md.sessions["NG"].last)
    m, wdn = day.month, day.weekday()
    base = float(d["rng"].tail(20).mean())                      # recent realised daily range
    month_norm = float(d[d["month"] == m]["rng"].mean())
    year_norm = float(d["rng"].mean())
    wm = next(r for r in s["weekday_month"]["table"] if r["month"] == m)["cells"][wdn]
    w_all = s["weekday_month"]["overall"][wdn]["factor"]
    n = wm["n"]
    wd_f = (n * (wm["factor"] or w_all) + 20 * w_all) / (n + 20)      # shrink thin samples toward all-month average
    curve = {x["tdte"]: x["factor"] for x in s["expiry_curve"]}
    exp_f = curve.get(info["trading_days_left"], 1.0)
    hol = s["holidays"]
    before_hol, after_hol = holiday_adjacent(day)
    hol_f = (hol["before"]["factor"] or 1.0) if before_hol else (hol["after"]["factor"] or 1.0) if after_hol else 1.0
    exp_rng = base * wd_f * exp_f * hol_f
    sub = d[(d["month"] == m) & (d["wd"] == wdn)]["rng"]
    scale = base / month_norm if month_norm else 1.0
    p50 = float(sub.quantile(0.5) * scale * exp_f * hol_f) if len(sub) > 5 else exp_rng
    p80 = float(sub.quantile(0.8) * scale * exp_f * hol_f) if len(sub) > 5 else exp_rng * 1.4
    usd = lambda r, pv=c.point_value: round(r * price * pv, 0)  # noqa: E731
    ticks = lambda r: int(round(r * price / c.tick_size))  # noqa: E731
    evs = cal.for_market(day, "NG")
    thanksgiving_week = any(cal.holiday(day + timedelta(days=k)) == "Thanksgiving" for k in range(-3, 4))
    up = d[(d["month"] == m) & (d["wd"] == wdn)]["ret"].dropna()
    dayname = day.strftime("%A %b %d").replace(" 0", " ")
    regime_vs_season = base / month_norm if month_norm else 1.0
    tleft = info["trading_days_left"]
    if info["expiry_day"]:
        expiry_txt = "Today is its LAST trading day. "
    elif tleft == 1:
        expiry_txt = f"Its last trading day is {info['last_trade_weekday']} {info['last_trade'][5:]}, the session right after this one. "
    else:
        expiry_txt = f"Its last trading day is {info['last_trade_weekday']} {info['last_trade'][5:]} ({tleft} sessions after this one). "
    roll_txt = (expiry_txt + f"Volume has mostly rolled to {info['next']}: day-trade {info['next']}, not the expiring contract."
                if info["roll_window"] else f"{tleft} trading days to expiry ({info['last_trade']}).")
    bullets = [
        f"{dayname}: the front month is {info['front']} ({info['front_month']}). {roll_txt}",
        f"Expected range: ~{100 * exp_rng:.1f}% ≈ ${usd(exp_rng):,.0f} per NG contract ({ticks(exp_rng)} ticks; "
        f"${usd(exp_rng, 1000):,.0f} per MNG). A big day (80th percentile) is ~{100 * p80:.1f}% ≈ ${usd(p80):,.0f}.",
        f"Why: recent volatility {100 * base:.1f}%/day × {WD[wdn]} factor {wd_f:.2f} × expiry factor {exp_f:.2f}"
        + (f" × holiday factor {hol_f:.2f}" if hol_f != 1.0 else "") + ".",
        f"Seasonal context: {ngc.MONTH_NAMES[m - 1]} days average {100 * month_norm:.1f}% ranges vs {100 * year_norm:.1f}% across the year "
        f"({month_norm / year_norm:.2f}×). Current volatility is {regime_vs_season:.2f}× this month's norm: "
        + ("running hot, so size down." if regime_vs_season > 1.2 else "running quiet, so expect smaller follow-through." if regime_vs_season < 0.8 else "normal for the season."),
    ]
    if info["options_expiry"] == day.isoformat():
        bullets.append(f"NG options on {info['front']} expire today: pinning near big strikes, then a release of hedging flows late in the session.")
    if info["expiry_day"]:
        bullets.append("Futures expiry day: thin, erratic trade in the expiring contract into the close. Avoid it.")
    if wdn == 3 and not before_hol:
        bullets.append("Thursday: the EIA storage report at 10:30 ET is the event of the week. Be flat before it; ranges are widest on Thursdays.")
    if wdn == 0:
        bullets.append("Monday: the weekend weather-model changes get priced at the open, so gaps are common.")
    if thanksgiving_week:
        bullets.append("Thanksgiving week: the EIA storage report usually moves earlier (often Wednesday). Confirm the time on eia.gov. "
                       "Liquidity thins into the holiday.")
    if before_hol:
        bullets.append("Day before a market holiday: positions get squared and liquidity thins in the afternoon.")
    if after_hol:
        bullets.append("Day after a holiday: the session prices several days of weather-forecast changes at once.")
    bullets.append(f"History, {ngc.MONTH_NAMES[m - 1]} {WD[wdn]}s: {len(up)} sessions, up {100 * float((up > 0).mean()):.0f}% of the time "
                   f"(direction is close to a coin flip, so trade the range, not a seasonal bias).")
    return {"date": day.isoformat(), "weekday": WD[wdn] if wdn < 5 else day.strftime("%A"), "contract": info,
            "price": price, "expected": {"pct": round(100 * exp_rng, 2), "usd": usd(exp_rng), "ticks": ticks(exp_rng),
                                         "usd_mng": usd(exp_rng, 1000), "usd_qg": usd(exp_rng, 2500),
                                         "p50_pct": round(100 * p50, 2), "p80_pct": round(100 * p80, 2), "p80_usd": usd(p80),
                                         "high": round(price * (1 + exp_rng / 2), 3), "low": round(price * (1 - exp_rng / 2), 3)},
            "factors": {"base_pct": round(100 * base, 2), "weekday": round(wd_f, 2), "expiry": round(exp_f, 2),
                        "holiday": round(hol_f, 2), "month_norm_pct": round(100 * month_norm, 2),
                        "year_norm_pct": round(100 * year_norm, 2), "vs_season": round(regime_vs_season, 2)},
            "events": evs, "bullets": bullets}
