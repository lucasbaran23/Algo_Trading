"""Option chains: CSV import, CBOE delayed (index options), or a labelled simulator.

Every chain is a DataFrame with one row per option:
  expiry (date), strike, type ('C' / 'P'), oi, volume, iv (decimal)
plus attrs: underlying price, source, multiplier, symbol.

Sources
  CSV   backend/data/options/{SYMBOL}_chain.csv with columns
        expiry,strike,type,open_interest,volume,iv[,underlying]
        Export from CME QuikStrike, Databento (CME options + statistics), your broker, or a
        licensed Barchart OnDemand feed (scraping Barchart's site breaks its terms).
  CBOE  ES/NQ in live mode: CBOE's free delayed SPX / NDX chains, strikes scaled to the future
        with the futures/index basis. Delayed and subject to CBOE's terms of use.
  SIM   Synthetic chain: open interest clustered at round strikes, a volatility smile with the
        right sign of skew per market and season (natural gas: winter call skew; equity
        indices: put skew). Labelled SIM; for demonstrating the analytics only.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .. import universe

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "options"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{root}.json"
CBOE_ROOT = {"ES": "_SPX", "NQ": "_NDX"}
SUPPORTED = ["NG", "ES", "NQ", "CL", "GC"]


def _expiries(symbol: str, today: date) -> list[date]:
    """Main listed expiries (simplified): monthly for commodities, plus weeklies for index options."""
    from ..natgas import contracts as ngc
    out = []
    if symbol == "NG":                                   # LNE: business day before the futures' last trade
        y, m = ngc.front_contract(today)
        for _ in range(4):
            out.append(ngc.add_business_days(ngc.last_trade_date(y, m), -1))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif symbol in ("ES", "NQ"):
        d = today
        while len(out) < 4:                              # next Fridays (weeklies)
            d += timedelta(days=1)
            if d.weekday() == 4:
                out.append(d)
        q = date(today.year, ((today.month - 1) // 3 + 1) * 3, 15)
        q = q + timedelta(days=(4 - q.weekday()) % 7)    # 3rd Friday of the quarter month
        if q <= today:
            q = date(q.year + (q.month == 12), 3 if q.month == 12 else q.month + 3, 15)
            q = q + timedelta(days=(4 - q.weekday()) % 7)
        out.append(q)
    else:                                                # CL/GC monthlies (approximate: ~25th / ~4 days before month end)
        for k in range(1, 4):
            mth = (today.month + k - 1) % 12 + 1
            yr = today.year + (today.month + k - 1) // 12
            d = date(yr, mth, 1) - timedelta(days=5 if symbol == "CL" else 4)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            if d > today:
                out.append(d)
    return sorted(set(d for d in out if d > today))[:5]


def _strike_step(symbol: str, price: float) -> float:
    return {"NG": 0.05, "ES": 25.0, "NQ": 100.0, "CL": 0.5, "GC": 25.0}.get(symbol, round(price * 0.01, 2))


def simulate(symbol: str, price: float, ann_vol: float, today: date, seed: int = 42) -> pd.DataFrame:
    h = int.from_bytes(hashlib.sha256(f"{symbol}{today}{seed}".encode()).digest()[:8], "little")
    rng = np.random.default_rng(h)
    step = _strike_step(symbol, price)
    winter = today.month in (11, 12, 1, 2, 3)
    rows = []
    for k, ex in enumerate(_expiries(symbol, today)):
        T = max((ex - today).days, 1) / 365
        width = price * ann_vol * np.sqrt(T) * 3
        strikes = np.arange(np.floor((price - width) / step) * step, price + width + step, step)
        size = 1.0 / (1 + 0.6 * k) * (3 if (symbol in ("ES", "NQ") and k == len(_expiries(symbol, today)) - 1) else 1)
        for K in strikes:
            m = np.log(K / price) / (ann_vol * np.sqrt(T))          # standardised moneyness
            rnd = 3.0 if abs(K / (step * 10) - round(K / (step * 10))) < 1e-9 else 1.6 if abs(K / (step * 2) - round(K / (step * 2))) < 1e-9 else 1.0
            base = np.exp(-0.5 * (m / 1.6) ** 2) * rnd * size
            if symbol == "NG":
                skew = 0.05 * m if winter else -0.03 * m             # winter: calls richer (upside tail), summer: mild put skew
                call_tilt, put_tilt = (1.4, 0.8) if winter else (0.9, 1.1)
            elif symbol in ("ES", "NQ"):
                skew, call_tilt, put_tilt = -0.06 * m, 1.0, 1.25      # equity put skew, more put open interest
            else:
                skew, call_tilt, put_tilt = 0.01 * m, 1.0, 1.0
            iv = max(0.05, ann_vol * (1 + skew + 0.04 * m * m))
            otm_c = 1.0 if K >= price else 0.45
            otm_p = 1.0 if K <= price else 0.45
            for typ, tilt, otm in (("C", call_tilt, otm_c), ("P", put_tilt, otm_p)):
                scale = {"NG": 9000, "ES": 2500, "NQ": 700, "CL": 5000, "GC": 1800}.get(symbol, 3000)   # typical OI per strike
                oi = int(max(0, base * tilt * otm * scale * rng.lognormal(0, 0.35)))
                vol = int(oi * rng.uniform(0.03, 0.25) * (2.5 if k == 0 else 1))
                rows.append({"expiry": ex, "strike": round(float(K), 6), "type": typ, "oi": oi, "volume": vol, "iv": round(float(iv), 4)})
    return pd.DataFrame(rows)


def _cboe(symbol: str, fut_price: float) -> tuple[pd.DataFrame, float] | None:
    import re

    import httpx
    r = httpx.get(CBOE_URL.format(root=CBOE_ROOT[symbol]), timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    js = r.json()["data"]
    spot = float(js.get("current_price") or js.get("close"))
    basis = fut_price / spot if spot else 1.0
    rows = []
    for o in js["options"]:
        mm = re.match(r"^[A-Z]+W?(\d{6})([CP])(\d{8})$", o["option"])
        if not mm:
            continue
        ex = date(2000 + int(mm.group(1)[:2]), int(mm.group(1)[2:4]), int(mm.group(1)[4:6]))
        rows.append({"expiry": ex, "strike": int(mm.group(3)) / 1000 * basis, "type": mm.group(2),
                     "oi": int(o.get("open_interest") or 0), "volume": int(o.get("volume") or 0), "iv": float(o.get("iv") or 0)})
    df = pd.DataFrame(rows)
    df = df[(df["oi"] > 0) | (df["volume"] > 0)]
    keep = sorted(df["expiry"].unique())[:6]
    return df[df["expiry"].isin(keep)], spot


def load(md, symbol: str) -> tuple[pd.DataFrame, dict]:
    c = universe.get(symbol)
    sym = c.data_symbol
    price = float(md.sessions[sym].last)
    closes = md.daily[sym]["close"].tail(60)
    ann_vol = float(np.log(closes).diff().std() * np.sqrt(252)) or 0.3
    meta = {"symbol": sym, "price": price, "multiplier": universe.get(sym).point_value, "source": "SIM"}
    path = DATA_DIR / f"{sym}_chain.csv"
    if path.exists():
        df = pd.read_csv(path)
        cols = {k.lower(): k for k in df.columns}
        out = pd.DataFrame({
            "expiry": pd.to_datetime(df[cols["expiry"]]).dt.date, "strike": df[cols["strike"]].astype(float).to_numpy(),
            "type": df[cols["type"]].astype(str).str.upper().str[0].to_numpy(),
            "oi": df[cols.get("open_interest", cols.get("oi"))].fillna(0).astype(int).to_numpy(),
            "volume": df[cols["volume"]].fillna(0).astype(int).to_numpy() if "volume" in cols else 0,
            "iv": df[cols["iv"]].astype(float).to_numpy() if "iv" in cols else ann_vol})
        if out["iv"].median() > 3:                       # given in percent
            out["iv"] = out["iv"] / 100
        if "underlying" in cols:
            meta["price"] = float(df[cols["underlying"]].iloc[0])
        meta["source"] = "CSV"
        return out[out["expiry"] > date.today()], meta
    if md.settings.live and sym in CBOE_ROOT:
        try:
            df, spot = _cboe(sym, price)
            meta.update(source="CBOE delayed (index options, scaled to futures)", index_spot=spot, multiplier=100)
            return df, meta
        except Exception as exc:  # noqa: BLE001
            log.warning("CBOE chain failed for %s: %s", sym, exc)
    return simulate(sym, price, ann_vol, date.today(), md.settings.sim_seed), meta
