"""NG futures curve (the strip): real contract-by-contract prices from Yahoo in live mode.

Yahoo lists the live NYMEX contracts as e.g. NGF27.NYM (expired ones are not available). Each
contract's last price and prior close come from its chart endpoint, polled in parallel and cached
for 10 minutes. In SIM mode a synthetic curve with a typical winter premium is returned, labelled.

Read-outs a gas trader watches:
  M1-M2 spread       front vs second month: steep contango = comfortable supply, backwardation = tight
  Winter strip       average of the next Dec-Mar contracts
  Summer strip       average of the next Apr-Oct contracts
  Winter premium     winter strip minus summer strip
  H/J spread         March vs April of next year ("widowmaker"): the market's bet on end-of-winter storage
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import contracts as ngc

_CACHE: dict = {}
WINTER = (12, 1, 2, 3)


def _months(start: date, n: int):
    y, m = ngc.front_contract(start)
    for _ in range(n):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _quote(code: str) -> dict | None:
    import httpx
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.NYM", params={"range": "5d", "interval": "1d"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    if r.status_code != 200:
        return None
    res = r.json()["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0].get("close") or [] if c is not None]
    meta = res["meta"]
    last = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
    return {"last": float(last) if last else None, "prev": float(prev) if prev else None, "time": meta.get("regularMarketTime")}


def strip(md, n: int = 18, today: date | None = None) -> dict:
    today = today or date.today()
    key = (md.settings.data_mode, today.isoformat(), n)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    months = list(_months(today, n))
    rows = []
    source = "SIM"
    if md.settings.live:
        codes = [ngc.code(y, m) for y, m in months]
        with ThreadPoolExecutor(max_workers=6) as pool:
            quotes = list(pool.map(lambda c: _safe(_quote, c), codes))
        if sum(q is not None and q.get("last") for q in quotes) >= n // 2:
            source = "LIVE (Yahoo, delayed)"
            for (y, m), q in zip(months, quotes):
                rows.append(_row(y, m, today, q))
    if not rows:
        front = float(md.sessions["NG"].last)
        for k, (y, m) in enumerate(months):
            seas = 0.18 if m in (12, 1, 2) else 0.08 if m in (11, 3) else -0.04 if m in (4, 5, 9, 10) else 0.0
            px = front * (1 + seas - (0.18 if months[0][1] in (12, 1, 2) else 0)) * (1 + 0.01 * k)
            rows.append(_row(y, m, today, {"last": round(px, 3), "prev": round(px * 0.99, 3)}))
    valid = [r for r in rows if r["last"]]
    front = valid[0] if valid else None
    for r in rows:
        r["vs_front"] = round(r["last"] - front["last"], 3) if r["last"] and front else None
    nxt_winter = [r for r in valid if r["month"] in WINTER and (r["month"] == 12 or r["year"] > today.year or today.month < 4)][:4]
    summer = [r for r in valid if 4 <= r["month"] <= 10][:7]
    avg = lambda xs: round(sum(r["last"] for r in xs) / len(xs), 3) if xs else None  # noqa: E731
    h = next((r for r in valid if r["month"] == 3), None)
    j = next((r for r in valid if r["month"] == 4 and h and r["year"] == h["year"]), None)
    m1m2 = round(valid[1]["last"] - valid[0]["last"], 3) if len(valid) > 1 else None
    out = {"source": source, "asof": today.isoformat(), "contracts": rows,
           "summary": {"front": front["code"] if front else None, "m1_m2": m1m2,
                       "m1_m2_pct": round(100 * m1m2 / valid[0]["last"], 2) if m1m2 is not None else None,
                       "shape": None if m1m2 is None else "CONTANGO" if m1m2 > 0.02 else "BACKWARDATION" if m1m2 < -0.02 else "FLAT",
                       "winter_strip": avg(nxt_winter), "winter_codes": [r["code"] for r in nxt_winter],
                       "summer_strip": avg(summer), "summer_codes": [r["code"] for r in summer],
                       "winter_premium": round(avg(nxt_winter) - avg(summer), 3) if nxt_winter and summer else None,
                       "h_j": round(h["last"] - j["last"], 3) if h and j else None, "h_j_codes": [h["code"], j["code"]] if h and j else None,
                       "cal_strip_next": avg([r for r in valid if r["year"] == today.year + 1])}}
    if md.settings.live and source == "SIM":
        out["note"] = "Yahoo contract quotes unavailable right now: showing a synthetic curve."
    _CACHE[key] = (time.time(), out)
    return out


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def _row(y: int, m: int, today: date, q: dict | None) -> dict:
    ltd = ngc.last_trade_date(y, m)
    last, prev = (q or {}).get("last"), (q or {}).get("prev")
    return {"code": ngc.code(y, m), "year": y, "month": m, "label": f"{ngc.MONTH_NAMES[m - 1][:3]} {str(y)[2:]}",
            "last_trade": ltd.isoformat(), "days_to_ltd": (ltd - today).days, "winter": m in WINTER,
            "last": last, "prev": prev, "chg": round(last - prev, 3) if last and prev else None,
            "chg_pct": round(100 * (last / prev - 1), 2) if last and prev else None}
