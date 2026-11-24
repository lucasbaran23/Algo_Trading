"""Roll handling for continuous futures from Yahoo (e.g. NG=F).

Yahoo's continuous series jumps from one contract to the next around expiry. That jump is the
calendar spread, not a market move, but it shows up as a huge one-day "return" (NG: +64% on
29 Jan 2026, +38% on 27 Jan 2022) and poisons seasonality, volatility, sigma bands and backtests.

Expired contracts are not available from Yahoo, so the roll is located and removed like this:
  1. Candidate window: 3 business days before to 2 after each contract's last trade date
     (natgas.contracts.last_trade_date).
  2. The roll day is the day in that window with the largest overnight gap (open vs prior close).
  3. That gap is treated as the spread and removed: every earlier price is multiplied by
     exp(gap) (ratio back-adjustment). The latest prices stay equal to the real contract.
The roll day keeps its intraday move (open to close), so only the overnight portion is lost.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def detect_rolls(df: pd.DataFrame, root: str = "NG") -> list[dict]:
    from ..natgas import contracts as ngc
    if root != "NG" or df.empty:
        return []
    d = df.reset_index(drop=True)
    dates = pd.to_datetime(d["date"]).dt.date
    gaps = np.log(d["open"] / d["close"].shift(1)).to_numpy()
    out = []
    first, last = dates.iloc[0], dates.iloc[-1]
    y, m = first.year, first.month
    while date(y, m, 1) <= date(last.year + (last.month == 12), (last.month % 12) + 1, 1):
        ltd = ngc.last_trade_date(y, m)
        lo, hi = ngc.add_business_days(ltd, -3), ngc.add_business_days(ltd, 2)
        idx = [i for i in range(1, len(d)) if lo <= dates.iloc[i] <= hi and np.isfinite(gaps[i])]
        if idx:
            i = max(idx, key=lambda k: abs(gaps[k]))
            out.append({"date": str(dates.iloc[i]), "i": int(i), "gap": float(gaps[i]), "expiring": ngc.code(y, m),
                        "last_trade": str(ltd)})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def back_adjust(df: pd.DataFrame, rolls: list[dict]) -> pd.DataFrame:
    if not rolls:
        return df
    out = df.reset_index(drop=True).copy()
    out["raw_close"] = out["close"]
    factor = np.ones(len(out))
    for r in rolls:
        factor[: r["i"]] *= np.exp(r["gap"])
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    out["roll"] = False
    out.loc[[r["i"] for r in rolls], "roll"] = True
    return out
