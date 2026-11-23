"""Natural gas winter dynamics: weather sensitivity, upside skew, tail risk and the winter long bias.

Weather sensitivity ("what does +10 HDD in the forecast do to price?")
  Preferred: regress daily returns on day-over-day REVISIONS of the 14-day HDD forecast. The desk
  stores a forecast snapshot every day (backend/.cache/ng_forecast_snapshots.json) and switches to
  this method automatically once ~60 snapshots exist.
  Until then (proxy): treat the realised weather of the next 14 days as a perfect forecast and
  regress returns on the daily change of that 14-day HDD anomaly. This overstates what traders
  could know, so it is labelled PROXY.
  Fitted separately for winter (Nov-Mar) and the rest of the year, and for colder vs warmer
  revisions (asymmetry).

Skew and tails: per calendar month, the 95th-percentile up day vs the 5th-percentile down day,
return skewness, the biggest up and down days, and the 90th-percentile adverse move against a
5-day long vs a 5-day short ("long and wrong vs short and dead").

Winter policy (Nov-Mar): prefer longs. Shorts only intraday, at half size, and only with a warm
forecast or a bearish storage surprise behind them; swing shorts are off. Configurable with
NG_WINTER_POLICY=off.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import seasonality as ngs
from . import weather as wx

WINTER = (11, 12, 1, 2, 3)


def policy_active(today: date | None = None) -> bool:
    today = today or date.today()
    return os.getenv("NG_WINTER_POLICY", "on").lower() != "off" and today.month in WINTER


# ---------------------------------------------------------------------- forecast snapshots / revisions
def _snap_path(settings) -> Path:
    return Path(settings.cache_dir) / "ng_forecast_snapshots.json"


def record_snapshot(settings, w: dict) -> dict:
    """Store today's forecast (date -> HDD by target date). Returns all snapshots."""
    p = _snap_path(settings)
    try:
        snaps = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        snaps = {}
    today = date.today().isoformat()
    snaps[today] = {x["date"]: x["hdd"] for x in w["daily"] if x["kind"] == "fc"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(sorted(snaps.items())[-400:])))
    except Exception:  # noqa: BLE001
        pass
    return snaps


def revision(snaps: dict, sim: bool, w: dict) -> dict:
    """Change in forecast HDD for the overlapping target dates vs the previous snapshot."""
    days = sorted(snaps)
    today = date.today().isoformat()
    cur = snaps.get(today, {})
    prev = snaps.get(days[-2]) if len(days) >= 2 else None
    source = "snapshots"
    if prev is None and sim:
        # SIM: synthesise yesterday's forecast so the panel has something to show (labelled)
        rng = np.random.default_rng(int(date.today().strftime("%Y%m%d")))
        prev = {k: max(0.0, v - rng.normal(0.6, 1.6)) for k, v in cur.items()}
        source = "SIM (synthetic previous run)"
    if not prev:
        return {"available": False, "note": "The first forecast snapshot was saved today. Revisions appear from tomorrow."}
    common = [k for k in cur if k in prev]
    d = float(sum(cur[k] - prev[k] for k in common))
    return {"available": True, "hdd_change": round(d, 1), "days_compared": len(common), "source": source,
            "vs": days[-2] if len(days) >= 2 else "synthetic"}


# ---------------------------------------------------------------------- sensitivity
def _beta(x: np.ndarray, y: np.ndarray) -> dict:
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 30 or x.std() == 0:
        return {"n": int(len(x))}
    b = float(np.cov(x, y, ddof=1)[0, 1] / x.var(ddof=1))
    resid = y - (y.mean() + b * (x - x.mean()))
    se = float(math.sqrt(resid.var(ddof=2) / ((len(x) - 1) * x.var(ddof=1))))
    r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
    return {"n": int(len(x)), "pct_per_10hdd": round(100 * b * 10, 3), "t": round(b / se, 2) if se else 0.0, "r2": round(r2, 3)}


def sensitivity(d: pd.DataFrame, w: dict, snaps: dict) -> dict:
    """Return per 10 HDD of forecast change, winter vs other months and colder vs warmer."""
    ret = d.set_index(d["date"].dt.normalize())["ret"]
    if len(snaps) >= 60:
        # real forecast revisions vs next-day... same-day returns (forecast runs land before/during the session)
        rows = []
        days = sorted(snaps)
        for a, b in zip(days[:-1], days[1:]):
            common = [k for k in snaps[b] if k in snaps[a]]
            rows.append((pd.Timestamp(b), sum(snaps[b][k] - snaps[a][k] for k in common)))
        x = pd.Series(dict(rows))
        method = f"Forecast revisions ({len(x)} daily snapshots)"
    elif w["source"] == "SIM":
        # SIM only: synthetic forecast revisions built from the simulated price moves (winter beta ~2% per 10 HDD,
        # near zero otherwise) so the panel demonstrates the method. Illustrative, not a finding.
        rng = np.random.default_rng(11)
        winter = ret.index.month.isin(WINTER)
        x = pd.Series(np.where(winter, ret.fillna(0) / 0.02 * 10 * 0.6, ret.fillna(0) / 0.02 * 10 * 0.08)
                      + rng.normal(0, 14, len(ret)), index=ret.index)
        method = "SIM: synthetic forecast revisions (illustrative only)"
    else:
        obs, norm = w["_obs"], w["_norm"]
        anom = obs - np.array([norm.loc[min(t.dayofyear, 366)] for t in obs.index])
        fwd = anom[::-1].rolling(14, min_periods=10).sum()[::-1].shift(-1)       # next-14-day anomaly seen "today"
        x = fwd.diff()
        method = "PROXY: realised next-14-day weather used as a perfect forecast"
    j = pd.concat([x.rename("x"), ret.rename("y")], axis=1, join="inner").dropna()
    win = j[j.index.month.isin(WINTER)]
    rest = j[~j.index.month.isin(WINTER)]
    out = {"method": method, "winter": _beta(win["x"].to_numpy(), win["y"].to_numpy()),
           "other": _beta(rest["x"].to_numpy(), rest["y"].to_numpy()),
           "winter_colder": _beta(win[win["x"] > 0]["x"].to_numpy(), win[win["x"] > 0]["y"].to_numpy()),
           "winter_warmer": _beta(win[win["x"] < 0]["x"].to_numpy(), win[win["x"] < 0]["y"].to_numpy())}
    return out


# ---------------------------------------------------------------------- tails & held-position risk
def tails(d: pd.DataFrame) -> list[dict]:
    out = []
    c = d.set_index("date")["close"]
    r5 = np.log(c.shift(-5) / c)                         # 5-session forward move (approx., spans rolls)
    for m in range(1, 13):
        g = d[d["month"] == m]
        r = g["ret"].dropna()
        f5 = r5[g["date"]].dropna()
        if len(r) < 30:
            continue
        up95, dn05 = float(r.quantile(0.95)), float(r.quantile(0.05))
        big_up = g.loc[g["ret"].idxmax()]
        big_dn = g.loc[g["ret"].idxmin()]
        out.append({"month": m, "skew": round(float(r.skew()), 2),
                    "up95_pct": round(100 * (math.exp(up95) - 1), 2), "down05_pct": round(100 * (math.exp(dn05) - 1), 2),
                    "tail_ratio": round(up95 / abs(dn05), 2) if dn05 else None,
                    "max_up": {"pct": round(100 * (math.exp(big_up["ret"]) - 1), 1), "date": str(big_up["date"].date())},
                    "max_down": {"pct": round(100 * (math.exp(big_dn["ret"]) - 1), 1), "date": str(big_dn["date"].date())},
                    "short_5d_p90_pct": round(100 * (math.exp(float(f5.quantile(0.90))) - 1), 1),
                    "long_5d_p90_pct": round(100 * (math.exp(-float(f5.quantile(0.10))) - 1), 1)})
    return out


# ---------------------------------------------------------------------- assemble
def dynamics(md, settings) -> dict:
    s = ngs.build(md, settings)
    d = s["_frame"]
    w = wx.weather(md, settings)
    snaps = record_snapshot(settings, w)
    rev = revision(snaps, w["source"] == "SIM", w)
    sens = sensitivity(d, w, snaps)
    t = tails(d)
    price = float(md.sessions["NG"].last)
    winter_now = date.today().month in WINTER
    beta = sens["winter" if winter_now else "other"].get("pct_per_10hdd")
    implied = None
    if rev.get("available") and beta is not None:
        pct = beta * rev["hdd_change"] / 10
        implied = {"pct": round(pct, 2), "usd_ng": round(price * pct / 100 * 10000, 0), "usd_mng": round(price * pct / 100 * 1000, 0)}
    win_t = [x for x in t if x["month"] in WINTER]
    oth_t = [x for x in t if x["month"] not in WINTER]
    avg = lambda rows, k: round(float(np.mean([r[k] for r in rows if r[k] is not None])), 2) if rows else None  # noqa: E731
    pol = policy_active()
    rules = ([
        "Prefer long positions: buy pullbacks to VWAP, value-area low and support. Let winners run on cold revisions.",
        "Shorts: intraday only, HALF size, and only with confirmation (a warmer forecast revision or a bearish storage surprise). No swing shorts.",
        "Never hold a short through Thursday's storage report or over a weekend when the 6-15 day forecast is turning colder.",
        "Stops on shorts go beyond the high of the day, not inside the noise. Upside gaps are larger than downside gaps.",
        f"Typical winter tail: the 95th-percentile up day is {avg(win_t, 'up95_pct')}% vs {avg(win_t, 'down05_pct')}% for the 5th-percentile down day; "
        f"a 5-day short faces a 90th-percentile adverse move of +{avg(win_t, 'short_5d_p90_pct')}% vs {avg(win_t, 'long_5d_p90_pct')}% against a long.",
    ] if pol else ["Winter policy is off: trade both directions on their merits."])
    return {
        "policy": {"active": pol, "label": "WINTER LONG BIAS ACTIVE" if pol else "BALANCED (outside winter)", "rules": rules},
        "revision": rev, "implied_move": implied, "sensitivity": sens,
        "tails": t, "winter_summary": {k: avg(win_t, k) for k in ("skew", "up95_pct", "down05_pct", "tail_ratio", "short_5d_p90_pct", "long_5d_p90_pct")},
        "other_summary": {k: avg(oth_t, k) for k in ("skew", "up95_pct", "down05_pct", "tail_ratio", "short_5d_p90_pct", "long_5d_p90_pct")},
        "weather_bias": w["bias"], "next14_pct": w["next14"]["pct"], "source": w["source"], "price_source": s["source"],
    }


_QS: dict = {}


def quick_state(md, settings) -> dict:
    """Cheap, cached (15 min) winter-policy / weather state for the signal engine."""
    import time
    hit = _QS.get("s")
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    w = wx.weather(md, settings)
    snaps = record_snapshot(settings, w)
    rev = revision(snaps, w["source"] == "SIM", w)
    st = {"policy": policy_active(), "bias": w["bias"], "rev_hdd": rev.get("hdd_change") if rev.get("available") else None,
          "next14_pct": w["next14"]["pct"]}
    _QS["s"] = (time.time(), st)
    return st
