from datetime import date, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.store import MarketData
from app.main import app
from app.natgas import contracts as ngc
from app.natgas import seasonality as ngs


@pytest.mark.parametrize("year,month,expected", [
    (2026, 12, date(2026, 11, 25)),   # Thanksgiving Thu Nov 26 is skipped
    (2026, 1, date(2025, 12, 29)),    # New Year's Day skipped
    (2026, 6, date(2026, 5, 27)),     # Memorial Day (May 25) skipped
    (2026, 7, date(2026, 6, 26)),     # Juneteenth / Independence Day observed handled
])
def test_last_trade_dates(year, month, expected):
    assert ngc.last_trade_date(year, month) == expected


def test_contract_info_around_expiry():
    tue = ngc.contract_info(date(2026, 11, 24))
    assert tue["front"] == "NGZ26" and tue["trading_days_left"] == 1 and tue["roll_window"]
    assert tue["options_expiry"] == "2026-11-24" and tue["next"] == "NGF27"
    assert ngc.contract_info(date(2026, 11, 25))["expiry_day"]
    assert ngc.contract_info(date(2026, 11, 27))["front"] == "NGF27"


def test_roll_days_excluded_from_returns():
    md = MarketData(Settings())
    md.load()
    d = ngs.frame(md.daily["NG"])
    assert d.loc[d["roll"], "ret"].isna().all()
    assert d["ret"].notna().sum() > 2000


def test_outlook_and_seasonality():
    md = MarketData(Settings())
    md.load()
    o = ngs.outlook(md, Settings(), datetime(2026, 11, 23, 18, 0))
    assert o["date"] == "2026-11-24" and o["contract"]["front"] == "NGZ26"
    e = o["expected"]
    assert 0 < e["pct"] < 20 and e["usd"] == pytest.approx(e["usd_mng"] * 10, rel=0.01)
    assert e["low"] < o["price"] < e["high"] and e["p80_pct"] >= e["p50_pct"]
    assert any("options" in b for b in o["bullets"]) and any("Thanksgiving" in b for b in o["bullets"])
    s = ngs.build(md, Settings())
    assert len(s["profile"]) == 12 and [p["front"] for p in s["profile"]][10] == "Dec"
    assert len(s["weekday_month"]["table"]) == 12 and all(len(r["cells"]) == 5 for r in s["weekday_month"]["table"])
    assert s["expiry_curve"][0]["tdte"] == 0
    # simulator embeds wider Thursday and winter ranges: the analytics must recover them
    wd = {x["wd"]: x["factor"] for x in s["weekday_month"]["overall"]}
    assert wd[3] == max(wd.values())
    prof = {p["month"]: p["range_pct"] for p in s["profile"]}
    assert np.mean([prof[1], prof[12]]) > np.mean([prof[6], prof[7]])


def test_natgas_api():
    with TestClient(app) as c:
        assert c.get("/api/natgas/outlook").json()["bullets"]
        s = c.get("/api/natgas/seasonality").json()
        assert "_frame" not in s and s["analogs"]["lines"]
        w = c.get("/api/natgas/weather").json()
        assert w["source"] == "SIM" and len(w["daily"]) >= 14 and w["bias"] in ("BULLISH", "BEARISH", "NEUTRAL")
        k = c.get("/api/natgas/contracts").json()
        assert len(k["strip"]) == 6 and k["strip"][0]["code"].startswith("NG")
        ins = c.get("/api/insider/NG").json()
        assert ins["natgas"]["contract"]["front"].startswith("NG") and ins["risk"]["micro"]["symbol"] == "MNG"
