"""Intraday (1-minute) bars: real data from CSV when available, otherwise a simulator.

Real data
  Put one file per market in backend/data/intraday/{SYMBOL}_1m.csv with columns
  timestamp,open,high,low,close,volume (e.g. exported from Databento, FirstRate Data,
  Kibot, or your broker/platform). Naive timestamps are read as INTRADAY_TZ
  (default America/New_York); timestamps with an offset/Z are converted to ET.

Simulator
  For each daily bar, a 1-minute Globex-session path (18:00 ET -> 17:00 ET) is built as a
  random walk with a realistic time-of-day volatility profile (quiet Asia, London pick-up,
  08:30 data, the U-shaped regular session), bridged to the day's open and close and
  stretched to hit exactly the day's high and low. Steps are independent, so the simulator
  contains NO intraday edge: any strategy should earn ~0 before costs on it. That makes it
  a useful null benchmark, and the research screen labels it clearly. Real edges can only
  be measured on real data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .. import universe
from ..daytrade.calendar import session_window

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "intraday"
SESSION_MIN = 1380            # 18:00 -> 17:00 ET
_CACHE: dict[str, tuple[str, pd.DataFrame]] = {}


def _rng(seed: int, *keys: str) -> np.random.Generator:
    d = hashlib.sha256(("|".join(keys) + f"|{seed}").encode()).digest()
    return np.random.default_rng(int.from_bytes(d[:8], "little"))


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def vol_profile(sector: str, symbol: str) -> np.ndarray:
    """Relative volatility per minute of the Globex session (index 0 = 18:00 ET)."""
    t = (np.arange(SESSION_MIN) + 18 * 60) % 1440          # minute of the ET clock
    w = np.full(SESSION_MIN, 0.35)                          # Asia: quiet
    w[(t >= 3 * 60) & (t < 8 * 60 + 30)] = 0.6              # London
    w[(t >= 8 * 60 + 30) & (t < 8 * 60 + 35)] = 1.8         # 08:30 U.S. data releases
    ro, rc = (_hm(x) for x in session_window(sector, symbol))
    rth = (t >= ro) & (t < rc)
    w[rth] = 0.75
    k = np.clip((t - ro) / 30.0, 0, 1)                      # opening burst decaying over 30 min
    w[rth] += (1.3 * (1 - k))[rth]
    w[(t >= rc - 30) & (t < rc)] += 0.45                    # closing activity
    w[(t >= rc) & (t < rc + 15)] = 0.5
    return w


def _simulate(symbol: str, daily: pd.DataFrame, seed: int, sessions: int) -> pd.DataFrame:
    c = universe.get(symbol)
    tick = c.tick_size
    w = vol_profile(c.sector, symbol)
    vw = w / w.sum()
    d = daily.tail(sessions).reset_index(drop=True)
    rng = _rng(seed, "intraday", symbol)
    frames = []
    base = np.arange(SESSION_MIN)
    for r in d.itertuples():
        o, h, l, cl = float(r.open), float(r.high), float(r.low), float(r.close)
        eps = rng.standard_t(4, SESSION_MIN) / np.sqrt(2) * np.sqrt(w)
        walk = np.cumsum(eps)
        tt = (base + 1) / SESSION_MIN
        bridge = walk - tt * walk[-1]
        span = max(h - l, tick)
        path = o + (cl - o) * tt + bridge / (np.abs(bridge).max() + 1e-12) * span * 0.6
        hi_oc, lo_oc = max(o, cl), min(o, cl)
        # stretch the excursions so the session prints exactly the daily high and low
        pmax, pmin = path.max(), path.min()
        up = path > hi_oc
        if pmax > hi_oc + 1e-12:
            path[up] = hi_oc + (path[up] - hi_oc) * (h - hi_oc) / (pmax - hi_oc)
        else:
            path[int(np.argmax(path))] = h
        dn = path < lo_oc
        if pmin < lo_oc - 1e-12:
            path[dn] = lo_oc - (lo_oc - path[dn]) * (lo_oc - l) / (lo_oc - pmin)
        else:
            path[int(np.argmin(path))] = l
        path[-1] = cl
        close = np.round(np.round(path / tick) * tick, 6)
        opn = np.r_[o, close[:-1]]
        noise = np.abs(rng.normal(0, span * 0.004, SESSION_MIN)) * np.sqrt(w)
        snap = lambda a: np.round(np.round(a / tick) * tick, 6)  # noqa: E731
        high = np.clip(snap(np.maximum(opn, close) + noise), None, h)
        low = np.clip(snap(np.minimum(opn, close) - noise), l, None)
        high = np.maximum(high, np.maximum(opn, close))
        low = np.minimum(low, np.minimum(opn, close))
        # the session must print exactly the daily extremes
        high[int(np.argmax(high))] = h
        low[int(np.argmin(low))] = l
        vol = np.maximum(1, (float(r.volume) * vw * rng.lognormal(0, 0.3, SESSION_MIN))).astype(int)
        start = pd.Timestamp(r.date).normalize() - pd.Timedelta(hours=6)          # 18:00 ET the evening before
        idx = start + pd.to_timedelta(base + 1, unit="min")                          # bar close times
        frames.append(pd.DataFrame({"open": opn, "high": high, "low": low, "close": close,
                                    "volume": vol, "session": pd.Timestamp(r.date).normalize()}, index=idx))
    out = pd.concat(frames)
    out.index.name = "ts"
    return out


def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    ts = pd.to_datetime(df[cols.get("timestamp", cols.get("datetime", df.columns[0]))], utc=False)
    tz = os.getenv("INTRADAY_TZ", "America/New_York")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("America/New_York").dt.tz_localize(None)
    elif tz != "America/New_York":
        ts = ts.dt.tz_localize(tz).dt.tz_convert("America/New_York").dt.tz_localize(None)
    # .to_numpy(): the CSV's 0..n index must not be aligned against the new timestamp index
    out = pd.DataFrame({k: df[cols[k]].astype(float).to_numpy() for k in ("open", "high", "low", "close")},
                       index=pd.DatetimeIndex(ts))
    out["volume"] = df[cols["volume"]].astype(float).to_numpy() if "volume" in cols else 1.0
    out = out.sort_index()
    # Globex session date: bars after 18:00 ET belong to the next day's session
    sess = (out.index + pd.Timedelta(hours=6)).normalize()
    out["session"] = sess
    out = out[out.index.dayofweek < 6]
    out.index.name = "ts"
    return out


def bars(md, symbol: str, sessions: int = 250) -> tuple[pd.DataFrame, str]:
    """1-minute bars for a market and their source ('CSV' or 'SIM')."""
    c = universe.get(symbol)
    sym = c.data_symbol
    path = DATA_DIR / f"{sym}_1m.csv"
    key = f"{sym}:{sessions}:{len(md.daily[sym])}:{path.exists()}"
    hit = _CACHE.get(sym)
    if hit and hit[0] == key:
        return hit[1], ("CSV" if path.exists() else "SIM")
    if path.exists():
        df = _load_csv(path)
        df = df[df["session"].isin(sorted(df["session"].unique())[-sessions:])]
        src = "CSV"
    else:
        df = _simulate(sym, md.daily[sym], md.settings.sim_seed, sessions)
        src = "SIM"
    _CACHE[sym] = (key, df)
    return df, src


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 1-minute bars (bar-close timestamps) to a coarser timeframe."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df[["open", "high", "low", "close", "volume"]].resample(rule, label="right", closed="right").agg(agg)
    return out.dropna(subset=["close"])
