"""Offline tests for the live-data plumbing (no network needed)."""
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.data import rolls
from app.main import app
from app.natgas import contracts as ngc


def _series_with_roll():
    days = pd.bdate_range("2026-01-02", "2026-03-31")
    close = np.full(len(days), 3.0)
    roll_day = pd.Timestamp(ngc.add_business_days(ngc.last_trade_date(2026, 2), 1))
    i = list(days).index(roll_day)
    close[i:] = 3.6                                    # +20% jump that is really the Feb->Mar spread
    open_ = close.copy()
    return pd.DataFrame({"date": days, "open": open_, "high": close, "low": close, "close": close}), i


def test_roll_detected_and_removed():
    df, i = _series_with_roll()
    r = rolls.detect_rolls(df, "NG")
    assert any(x["i"] == i for x in r)
    adj = rolls.back_adjust(df, r)
    ret = np.log(adj["close"]).diff().dropna()
    assert ret.abs().max() < 1e-9                       # no fake move left
    assert adj["close"].iloc[-1] == 3.6                 # latest price stays real
    assert adj["raw_close"].iloc[0] == 3.0


def test_no_rolls_for_other_roots():
    df, _ = _series_with_roll()
    assert rolls.detect_rolls(df, "CL") == []


def test_sessions_accept_five_minute_bars():
    from app.intraday import research as ir
    idx = pd.date_range("2026-11-02 09:05", "2026-11-02 14:30", freq="5min")
    df = pd.DataFrame({"open": 3.0, "high": 3.01, "low": 2.99, "close": 3.0, "volume": 10.0, "res": 5}, index=idx)
    s = ir.prepare_sessions(df, "NG")
    assert len(s) == 1 and len(s[0]["c"]) == len(idx)


def test_data_status_and_curve_in_sim_mode():
    with TestClient(app) as c:
        ds = c.get("/api/datastatus/NG").json()
        names = {r["what"] for r in ds["rows"]}
        assert {"Daily prices", "Intraday history", "Futures curve", "Storage (EIA weekly)", "Options chain"} <= names
        k = c.get("/api/natgas/contracts").json()
        assert k["curve"]["source"] == "SIM" and len(k["curve"]["contracts"]) == 18
        assert k["curve"]["summary"]["shape"] in ("CONTANGO", "BACKWARDATION", "FLAT")
        st = c.get("/api/natgas/storage").json()
        assert st["sd"]["stocks"] > 0
