from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.daytrade import calendar as cal
from app.daytrade.playbook import run_path
from app.main import app


def test_path_model_entries_and_exits():
    assert run_path([100, 99, 103, 102], 1, "stop", level=101, stop_dist=1) == (101, 102, "close")
    assert run_path([102, 101.5, 104, 103], 1, "stop", level=101, stop_dist=1) == (102, 103, "close")  # gap fill
    assert run_path([101, 103.5, 99, 100], -1, "limit", level=103, stop_px=104, target=100) == (103, 100, "target")
    assert run_path([100, 103, 98, 99], 1, "market", stop_dist=1, target=105) == (100, 99, "stop")
    assert run_path([100, 101, 99, 100.5], 1, "stop", level=105, stop_dist=1) is None   # never triggered


def test_calendar_rules_and_holidays():
    wed = [e["name"] for e in cal.events_for(date(2026, 11, 25))]
    assert "EIA Weekly Petroleum Status Report" in wed
    assert any("Natural Gas Storage" in e["name"] for e in cal.for_market(date(2026, 12, 3), "NG"))
    assert cal.holiday(date(2026, 11, 26)) == "Thanksgiving" and cal.events_for(date(2026, 11, 26)) == []
    assert cal.holiday(date(2026, 4, 3)) == "Good Friday"
    assert cal.next_session_date(datetime(2026, 11, 25, 18, 0)) == date(2026, 11, 27)   # skips Thanksgiving
    assert any(e["name"].startswith("FOMC") for e in cal.events_for(date(2026, 12, 9)))


@pytest.mark.parametrize("sym", ["ES", "CL", "ZC", "MNQ", "KC"])
def test_insider_briefing(sym):
    with TestClient(app) as c:
        d = c.get(f"/api/insider/{sym}").json()
        assert d["plan"] and d["checklist"] and d["levels"]
        prices = [lv["price"] for lv in d["levels"]]
        assert prices == sorted(prices, reverse=True)
        assert d["regime"]["kind"] in ("trend", "range", "transition")
        rows = d["playbook"]["rows"]
        assert len(rows) == 6 and all(r["verdict"] in ("PREFERRED", "WORKS", "MARGINAL", "AVOID") for r in rows)
        assert [r["score"] for r in rows] == sorted([r["score"] for r in rows], reverse=True)
        r = d["risk"]
        assert 0 < r["size_multiplier"] <= 1 and r["stop_ticks"] >= 4
        assert r["risk_per_contract"] * max(r["contracts"], 0) <= r["risk_budget"] + 1e-6
        vp = d["profile"]
        assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_insider_unknown_symbol():
    with TestClient(app) as c:
        assert c.get("/api/insider/XYZ").status_code == 404
