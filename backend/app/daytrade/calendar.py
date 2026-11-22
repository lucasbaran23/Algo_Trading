"""Scheduled market-moving releases, per market, for a given trading day.

Most recurring reports follow fixed weekly rules and are generated exactly (EIA petroleum
on Wednesday, EIA gas storage and USDA export sales on Thursday, Baker Hughes and COT on
Friday...). Monthly releases whose exact day moves (CPI, WASDE, Cattle on Feed) are
*estimated* and flagged so the UI can say "confirm on the official calendar". Holiday
shifts are not modelled - a production build should pull an official calendar feed.
All times are US Eastern.
"""
from __future__ import annotations

from datetime import date, timedelta

ALL = ["ES", "NQ", "YM", "CL", "NG", "RB", "HO", "GC", "SI", "HG", "PL", "ZC", "ZS", "ZW", "KC", "SB", "CC", "CT", "LE", "HE"]
INDICES = ["ES", "NQ", "YM"]
PETROLEUM = ["CL", "RB", "HO"]
GRAINS = ["ZC", "ZS", "ZW"]
METALS = ["GC", "SI", "HG", "PL"]

# 2026 FOMC decision days (second day of each meeting, 2:00 pm ET). Verify against federalreserve.gov.
FOMC_2026 = {date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
             date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)}

# Liquid "regular" session windows (ET) where day traders concentrate. Globex trades ~23h.
SESSIONS = {
    "Indices": ("09:30", "16:00"), "Energy": ("09:00", "14:30"), "Metals": ("08:20", "13:30"),
    "Grains": ("09:30", "14:20"), "Livestock": ("09:30", "14:05"),
    "KC": ("04:15", "13:30"), "SB": ("03:30", "13:00"), "CC": ("04:45", "13:30"), "CT": ("10:30", "14:20"),
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(y, month, (h + l - 7 * m + 33 * month + 19) % 32)


def holiday(d: date) -> str | None:
    """U.S. exchange holidays (regular session closed). Early closes are reported by early_close()."""
    y = d.year
    days = {
        _observed(date(y, 1, 1)): "New Year's Day", _nth_weekday(y, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(y, 2, 0, 3): "Presidents' Day", _easter(y) - timedelta(days=2): "Good Friday",
        _last_weekday(y, 5, 0): "Memorial Day", _observed(date(y, 6, 19)): "Juneteenth",
        _observed(date(y, 7, 4)): "Independence Day", _nth_weekday(y, 9, 0, 1): "Labor Day",
        _nth_weekday(y, 11, 3, 4): "Thanksgiving", _observed(date(y, 12, 25)): "Christmas",
    }
    return days.get(d)


def early_close(d: date) -> str | None:
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return "Day after Thanksgiving: early close (~13:00 ET for most CME products), thin liquidity"
    if d == date(d.year, 12, 24) and d.weekday() < 5:
        return "Christmas Eve: early close, thin liquidity"
    return None


def _ev(time, name, markets, stars, note="", estimated=False):
    return {"time": time, "name": name, "markets": markets, "stars": stars, "note": note, "estimated": estimated}


def events_for(d: date) -> list[dict]:
    wd = d.weekday()  # Mon=0
    ev: list[dict] = []
    if wd >= 5 or holiday(d):
        return ev
    if early_close(d):
        ev.append(_ev("", early_close(d), ALL, 2, "Spreads widen and moves can be erratic: trade smaller or skip."))
    # ---- weekly
    if wd == 0 and 4 <= d.month <= 11:
        ev.append(_ev("16:00", "USDA Crop Progress", GRAINS + ["CT"], 2, "After the close: moves the overnight session"))
    if wd == 0:
        ev.append(_ev("11:00", "USDA Export Inspections", GRAINS, 1))
    if wd == 1:
        ev.append(_ev("16:30", "API weekly crude inventories (private)", PETROLEUM, 2, "After the close: sets the tone for Wednesday"))
    if wd == 2:
        ev.append(_ev("10:30", "EIA Weekly Petroleum Status Report", PETROLEUM, 3,
                      "Crude/gasoline/distillate stocks. Spikes of 1+ ATR in minutes are common: be flat or tighten up before."))
        ev.append(_ev("10:30", "EIA ethanol production", ["ZC"], 1))
    if wd == 3:
        ev.append(_ev("08:30", "USDA Weekly Export Sales", GRAINS + ["CT", "LE", "HE"], 2))
        ev.append(_ev("08:30", "U.S. initial jobless claims", INDICES + ["GC"], 1))
        ev.append(_ev("10:30", "EIA Natural Gas Storage Report", ["NG"], 3,
                      "The NG event of the week: compare the build/draw with the consensus and the 5-year average."))
    if wd == 4:
        ev.append(_ev("13:00", "Baker Hughes rig count", ["CL", "NG"], 1))
        ev.append(_ev("15:30", "CFTC Commitments of Traders", ALL, 1, "Positioning as of Tuesday: see the COT screen"))
    # ---- monthly / scheduled macro
    if d == _nth_weekday(d.year, d.month, 4, 1):
        ev.append(_ev("08:30", "U.S. Employment Situation (payrolls)", INDICES + ["GC", "SI", "CL"], 3,
                      "Usually the first Friday. Expect a fast, two-sided move at 8:30; liquidity is thin in the first minute.",
                      estimated=True))
    if d in FOMC_2026:
        ev.append(_ev("14:00", "FOMC rate decision", INDICES + ["GC", "SI", "CL"], 3,
                      "Press conference at 14:30. Volatility often reverses the first move."))
        ev.append(_ev("14:30", "Fed Chair press conference", INDICES + ["GC"], 3))
    if 10 <= d.day <= 15 and wd in (1, 2, 3) and d == _first_weekday_in(d, 10, (1, 2, 3)):
        ev.append(_ev("08:30", "U.S. CPI", INDICES + ["GC", "SI"], 3, "Date estimated: confirm on bls.gov", estimated=True))
    if 8 <= d.day <= 12 and d == _first_weekday_in(d, 9, (1, 2, 3, 4)):
        ev.append(_ev("12:00", "USDA WASDE", GRAINS + ["CT", "SB", "LE", "HE"], 3,
                      "Monthly supply/demand balance. Date estimated: confirm on usda.gov", estimated=True))
    third_friday = _nth_weekday(d.year, d.month, 4, 3)
    if d == third_friday:
        ev.append(_ev("15:00", "USDA Cattle on Feed", ["LE"], 2, "Date estimated", estimated=True))
        if d.month in (3, 6, 9, 12):
            ev.append(_ev("09:30", "Quarterly index futures & options expiration", INDICES, 2,
                          "Settlement flows at the open; volume rolls to the next contract the week before."))
    if wd == 0 and d.day <= 7 and d.month in (1, 4, 7, 10):
        ev.append(_ev("10:00", "ISM Manufacturing PMI", INDICES + ["HG", "CL"], 2, "Date estimated", estimated=True))
    return sorted(ev, key=lambda e: e["time"] or "00")  # all-day notices first


def _first_weekday_in(d: date, start_day: int, weekdays: tuple[int, ...]) -> date:
    x = date(d.year, d.month, start_day)
    while x.weekday() not in weekdays:
        x += timedelta(days=1)
    return x


def for_market(d: date, symbol: str) -> list[dict]:
    return [e for e in events_for(d) if symbol in e["markets"]]


def next_session_date(now_et) -> date:
    """The trading day the briefing is for: today on weekdays before ~17:00 ET, else the next weekday."""
    d = now_et.date()
    if now_et.weekday() < 5 and now_et.hour < 17 and not holiday(d):
        return d
    d += timedelta(days=1)
    while d.weekday() >= 5 or holiday(d):
        d += timedelta(days=1)
    return d


def session_window(sector: str, symbol: str) -> tuple[str, str]:
    return SESSIONS.get(symbol) or SESSIONS.get(sector, ("09:30", "16:00"))
