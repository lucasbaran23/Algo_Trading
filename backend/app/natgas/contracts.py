"""NYMEX Henry Hub natural gas (NG) contract calendar.

* Contract months: every calendar month (F G H J K M N Q U V X Z).
* Last trading day: the 3rd business day before the first calendar day of the delivery month
  (CME rule; U.S. exchange holidays are skipped). Example: December 2026 (NGZ26) delivers from
  Tue Dec 1, so trading ends Wed Nov 25, 2026 (Thanksgiving, Thu Nov 26, is not a business day).
* Options on NG (LNE) expire the business day before the futures; Micro Henry Hub (MNG) also
  terminates one business day before the corresponding NG contract.
* Volume typically migrates to the next month in the last few sessions before expiry.
Verify dates against CME's product calendar before relying on them.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..daytrade.calendar import holiday

MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and not holiday(d)


def add_business_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    while n:
        d += timedelta(days=step)
        if is_business_day(d):
            n -= step
    return d


def last_trade_date(year: int, month: int) -> date:
    """Last trading day of the contract delivering in (year, month)."""
    return add_business_days(date(year, month, 1), -3)


def code(year: int, month: int, root: str = "NG") -> str:
    return f"{root}{MONTH_CODES[month - 1]}{year % 100:02d}"


def front_contract(d: date) -> tuple[int, int]:
    """(year, month) of the contract that is front month on trading date d."""
    y, m = d.year, d.month + 1
    if m > 12:
        y, m = y + 1, 1
    while last_trade_date(y, m) < d:
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return y, m


def business_days_between(a: date, b: date) -> int:
    """Business days after a up to and including b (0 if b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_business_day(d)
    return n


def contract_info(d: date) -> dict:
    y, m = front_contract(d)
    ltd = last_trade_date(y, m)
    ny, nm = (y + (m == 12), 1 if m == 12 else m + 1)
    tdte = business_days_between(d, ltd)      # 0 = expiry day itself
    return {
        "front": code(y, m), "front_month": MONTH_NAMES[m - 1], "front_year": y, "front_month_num": m,
        "last_trade": ltd.isoformat(), "last_trade_weekday": ltd.strftime("%A"),
        "options_expiry": add_business_days(ltd, -1).isoformat(),
        "micro_last_trade": add_business_days(ltd, -1).isoformat(),
        "trading_days_left": tdte,
        "next": code(ny, nm), "next_month": MONTH_NAMES[nm - 1],
        "next_last_trade": last_trade_date(ny, nm).isoformat(),
        "roll_window": tdte <= 4,
        "expiry_day": tdte == 0,
    }


def label_days(dates) -> list[dict]:
    """Front contract and trading days to expiry for each historical date (continuous series)."""
    out = []
    for ts in dates:
        d = ts.date() if hasattr(ts, "date") else ts
        y, m = front_contract(d)
        ltd = last_trade_date(y, m)
        out.append({"contract": code(y, m), "month": m, "tdte": business_days_between(d, ltd)})
    return out
