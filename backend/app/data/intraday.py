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
    out = out[out["session"].dt.dayofweek < 5]          # Sunday-evening bars belong to Monday's session
    out.index.name = "ts"
    return out


# ---------------------------------------------------------------------- live intraday (Yahoo, delayed)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
LIVE_SRC = "YAHOO (delayed)"


def yahoo_chart(ticker: str, interval: str, p1: int | None = None, p2: int | None = None, rng: str | None = None) -> pd.DataFrame:
    """Yahoo chart bars as a DataFrame indexed by bar-CLOSE time in naive America/New_York."""
    import httpx
    params = {"interval": interval, "includePrePost": "true"}
    if rng:
        params["range"] = rng
    else:
        params["period1"], params["period2"] = str(p1), str(p2)
    r = httpx.get(YAHOO_CHART.format(symbol=ticker), params=params, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}, timeout=25)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    if not ts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    q = res["indicators"]["quote"][0]
    mins = int(interval.rstrip("m")) if interval.endswith("m") else 60
    idx = (pd.to_datetime(ts, unit="s", utc=True).tz_convert("America/New_York").tz_localize(None)
           + pd.Timedelta(minutes=mins))                                     # Yahoo stamps the bar open
    df = pd.DataFrame({k: pd.to_numeric(pd.Series(q.get(k)), errors="coerce").to_numpy() for k in ("open", "high", "low", "close", "volume")},
                      index=pd.DatetimeIndex(idx, name="ts"))
    df = df.dropna(subset=["close"])
    df["volume"] = df["volume"].fillna(0.0)
    df["res"] = mins
    return df[~df.index.duplicated(keep="last")]


def _store_path(md, sym: str) -> Path:
    return Path(md.settings.cache_dir) / "intraday" / f"{sym}_bars.csv.gz"


def live_history(md, sym: str, max_age_s: int = 600) -> pd.DataFrame:
    """Real intraday history that grows over time.

    Each refresh pulls 1-minute bars for the last 30 days (Yahoo's limit) and 5-minute bars for the
    last 60 days, and merges them into a store on disk. 1-minute bars win wherever both exist, and
    days already stored are kept, so the history keeps lengthening the longer the terminal runs.
    """
    import time as _t
    c = universe.get(sym)
    path = _store_path(md, sym)
    old = None
    if path.exists():
        old = pd.read_csv(path, index_col="ts", parse_dates=["ts"])
        if _t.time() - path.stat().st_mtime < max_age_s:
            return old
    now = int(_t.time())
    parts = []
    for k in range(4, 0, -1):                                   # 4 x 7-day chunks of 1-minute bars
        p1 = now - min(k * 7, 29) * 86400 + 60
        p2 = now - (k - 1) * 7 * 86400
        try:
            parts.append(yahoo_chart(c.yahoo, "1m", p1, p2))
        except Exception:  # noqa: BLE001
            pass
    one = pd.concat(parts) if parts else pd.DataFrame()
    five = pd.DataFrame()
    try:
        five = yahoo_chart(c.yahoo, "5m", now - 59 * 86400, now)
    except Exception:  # noqa: BLE001
        pass
    frames = [f for f in (old, one) if f is not None and len(f)]
    fine = pd.concat(frames) if frames else pd.DataFrame(columns=["open", "high", "low", "close", "volume", "res"])
    fine = fine[~fine.index.duplicated(keep="last")]
    if len(five):
        fine_days = set((fine.index + pd.Timedelta(hours=6)).normalize()) if len(fine) else set()
        five_days = (five.index + pd.Timedelta(hours=6)).normalize()
        five = five[~five_days.isin(list(fine_days))]
    out = pd.concat([fine, five]).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    if out.empty:
        raise ValueError(f"no Yahoo intraday bars for {sym}")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, compression="gzip")
    return out


def bars(md, symbol: str, sessions: int = 250) -> tuple[pd.DataFrame, str]:
    """Intraday bars for a market and their source: 'CSV' (your file), 'YAHOO (delayed)' (live mode) or 'SIM'.

    Bars are 1-minute where available; older live days may be 5-minute (column `res`)."""
    import time as _t
    c = universe.get(symbol)
    sym = c.data_symbol
    path = DATA_DIR / f"{sym}_1m.csv"
    live = bool(getattr(md.settings, "live", False)) and bool(c.yahoo) and not path.exists()
    key = f"{sym}:{sessions}:{len(md.daily[sym])}:{path.exists()}:{live}"
    hit = _CACHE.get(sym)
    if hit and hit[0] == key and (not live or _t.time() - hit[3] < 600):
        return hit[1], hit[2]
    src = "SIM"
    df = None
    if path.exists():
        df = _load_csv(path)
        src = "CSV"
    elif live:
        try:
            df = live_history(md, sym)
            src = LIVE_SRC
        except Exception:  # noqa: BLE001
            df = None
    if df is not None:
        df = df.copy()
        df["session"] = (df.index + pd.Timedelta(hours=6)).normalize()
        df = df[df["session"].dt.dayofweek < 5]           # keep Sunday-evening bars (Monday session)
        df = df[df["session"].isin(sorted(df["session"].unique())[-sessions:])]
        if "res" not in df:
            df["res"] = 1
    else:
        df = _simulate(sym, md.daily[sym], md.settings.sim_seed, sessions)
        df["res"] = 1
    _CACHE[sym] = (key, df, src, _t.time())
    return df, src


def is_real(src: str) -> bool:
    return src != "SIM"


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 1-minute bars (bar-close timestamps) to a coarser timeframe."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df[["open", "high", "low", "close", "volume"]].resample(rule, label="right", closed="right").agg(agg)
    return out.dropna(subset=["close"])
