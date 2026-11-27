"""Databento: real CME Globex 1-minute bars for years of intraday history.

Sign up at https://databento.com/signup (new accounts get free credit; historical data is billed per
GB and one market's 1-minute bars cost cents to a few dollars). Put the key in .env as
DATABENTO_API_KEY. Bars are merged into the same on-disk intraday store the Yahoo feed fills, so
every intraday screen and backtest picks them up.

  python -m app.tools.backfill NG --years 3          # one-off, prints the cost first
  (also runs automatically in live mode when the store holds fewer than ~200 sessions)

Symbol: the continuous front month by volume (NG.v.0), so rolls follow where the liquidity is.
Spend guard: DATABENTO_MAX_USD (default 5) per request; the cost is checked with Databento's
metadata.get_cost before any download.
"""
from __future__ import annotations

import io
import logging
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)
BASE = "https://hist.databento.com/v0/"
SIGNUP = "https://databento.com/signup"
DATASET = "GLBX.MDP3"


def api_key() -> str:
    return os.getenv("DATABENTO_API_KEY", "").strip()


def _params(root: str, start: str, end: str, schema: str = "ohlcv-1m") -> dict:
    return {"dataset": DATASET, "symbols": f"{root}.v.0", "stype_in": "continuous", "schema": schema, "start": start, "end": end}


def cost(root: str, start: str, end: str) -> float:
    import httpx
    r = httpx.get(BASE + "metadata.get_cost", params=_params(root, start, end), auth=(api_key(), ""), timeout=40)
    r.raise_for_status()
    return float(r.json())


def fetch(root: str, start: str, end: str) -> pd.DataFrame:
    """1-minute OHLCV, indexed by bar-CLOSE time in naive America/New_York (the app's convention)."""
    import httpx
    p = {**_params(root, start, end), "encoding": "csv", "pretty_px": "true", "pretty_ts": "true", "map_symbols": "true"}
    with httpx.Client(timeout=300) as client:
        r = client.get(BASE + "timeseries.get_range", params=p, auth=(api_key(), ""))
        r.raise_for_status()
    return parse_csv(r.text)


def parse_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text))
    if raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "res"])
    ts = pd.to_datetime(raw["ts_event"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None) + pd.Timedelta(minutes=1)
    out = pd.DataFrame({k: pd.to_numeric(raw[k], errors="coerce").to_numpy() for k in ("open", "high", "low", "close", "volume")},
                       index=pd.DatetimeIndex(ts, name="ts"))
    out["res"] = 1
    return out.dropna(subset=["close"]).sort_index()


def backfill(md, symbol: str = "NG", years: float = 2.0, max_usd: float | None = None, dry_run: bool = False) -> dict:
    """Download `years` of 1-minute bars (in yearly chunks) and merge them into the intraday store."""
    from . import intraday
    if not api_key():
        return {"ok": False, "reason": "DATABENTO_API_KEY not set", "signup": SIGNUP}
    max_usd = float(os.getenv("DATABENTO_MAX_USD", "5")) if max_usd is None else max_usd
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)   # stay clear of the live edge
    start = end - timedelta(days=int(365 * years))
    chunks, t = [], start
    while t < end:
        t2 = min(end, t + timedelta(days=365))
        chunks.append((t.strftime("%Y-%m-%dT%H:%M"), t2.strftime("%Y-%m-%dT%H:%M")))
        t = t2
    total = sum(cost(symbol, a, b) for a, b in chunks)
    if dry_run or total > max_usd:
        return {"ok": dry_run and total <= max_usd, "cost_usd": round(total, 4), "max_usd": max_usd, "chunks": len(chunks),
                "reason": None if dry_run else f"cost ${total:.2f} is above DATABENTO_MAX_USD=${max_usd:.2f}"}
    frames = [fetch(symbol, a, b) for a, b in chunks]
    new = pd.concat(frames).sort_index()
    new = new[~new.index.duplicated(keep="last")]
    path = intraday._store_path(md, symbol)
    old = pd.read_csv(path, index_col="ts", parse_dates=["ts"]) if path.exists() else None
    merged = pd.concat([new, old]) if old is not None else new          # Databento wins where both exist
    merged = merged[~merged.index.duplicated(keep="first")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, compression="gzip")
    intraday._CACHE.pop(symbol, None)
    sess = (merged.index + pd.Timedelta(hours=6)).normalize().nunique()
    log.warning("Databento backfill %s: %d bars, %s -> %s, cost ~$%.2f; store now %d sessions",
                symbol, len(new), new.index[0], new.index[-1], total, sess)
    return {"ok": True, "bars": int(len(new)), "from": str(new.index[0]), "to": str(new.index[-1]),
            "cost_usd": round(total, 4), "sessions_in_store": int(sess)}


def auto_backfill(md, symbol: str = "NG") -> None:
    """Called in a background thread at startup: top up the store once if it is short of history."""
    from . import intraday
    if not api_key() or not md.settings.live:
        return
    try:
        path = intraday._store_path(md, symbol)
        if path.exists():
            df = pd.read_csv(path, index_col="ts", parse_dates=["ts"])
            if (df.index + pd.Timedelta(hours=6)).normalize().nunique() >= 200 and (df["res"] == 1).mean() > 0.9:
                return
        res = backfill(md, symbol, float(os.getenv("DATABENTO_YEARS", "2")))
        if not res.get("ok"):
            log.warning("Databento auto-backfill skipped: %s", res.get("reason"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Databento auto-backfill failed: %s", exc)


def status() -> dict:
    return {"configured": bool(api_key()), "signup": SIGNUP, "max_usd": float(os.getenv("DATABENTO_MAX_USD", "5")),
            "checked": str(date.today())}
