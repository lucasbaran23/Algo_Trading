import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data.store import MarketData
from app.intraday import extremes as ex
from app.main import app
from app.options import levels as lv


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def test_reference_uses_only_past_data(md):
    d = ex.daily_frame(md, "NG", "month")
    # changing a future return must not change today's reference sigma
    i = len(d) - 300
    sd_before = d["sd"].iloc[i]
    md2 = md.daily["NG"].copy()
    md2.loc[md2.index[i + 10], "close"] *= 1.5
    class M: daily = {"NG": md2}
    d2 = ex.daily_frame(M, "NG", "month")
    assert d2["sd"].iloc[i] == pytest.approx(sd_before)
    z = d["z"].dropna()
    assert abs(z.mean()) < 0.2 and 0.8 < z.std() < 1.3


def test_swing_trades_fade_the_shock(md):
    d = ex.daily_frame(md, "ES", "month")
    for conf in ("none", "reversal"):
        tr = ex._swing_trades(d, 2.5, conf, 0.25)
        assert tr
        for i, side, entry, exit_px, _, z in tr:
            assert side == (-1 if z > 0 else 1) and abs(z) >= 2.5


def test_backtest_and_today_shapes(md):
    b = ex.backtest(md, "NG")
    assert len(b["swing"]["grid"]) == 6 and len(b["intraday"]["grid"]) == 6
    t = ex.today(md, "NG")
    assert [x["k"] for x in t["bands"]] == [2.0, 2.5, 3.0]
    b0 = t["bands"][0]
    assert b0["now_down"] < t["prev_close"] < b0["now_up"]
    assert t["ref_stats"]["month"]["n"] > 100


def test_wall_fade_rule_on_a_synthetic_session():
    # price rallies into a 100 call wall, rejects, and falls back: the fade should short and win
    m = np.arange(1, 391)
    path = np.concatenate([np.linspace(97, 100.2, 60), np.linspace(100.2, 97, 330)])
    s = {"m": m, "o": path, "h": path + 0.02, "l": path - 0.02, "c": path, "v": np.ones(390), "vwap": path,
         "prev_close": 97.0, "date": None}
    t = lv._session_trades(s, {"call_wall": 100.0, "put_wall": 94.0, "flip": 95.0, "max_pain": 97.0, "gex": 1e6, "front_days": 10}, u=1.0)
    side, entry, exit_px = t["wall_fade"]
    assert side == -1 and exit_px < entry
    assert t["wall_break"] is None                   # positive gamma: no breakout trades


def test_wall_break_rule_in_negative_gamma():
    m = np.arange(1, 391)
    path = np.concatenate([np.linspace(96, 93.5, 120), np.linspace(93.5, 90, 270)])
    s = {"m": m, "o": path, "h": path + 0.02, "l": path - 0.02, "c": path, "v": np.ones(390), "vwap": path, "prev_close": 96.0, "date": None}
    t = lv._session_trades(s, {"call_wall": 100.0, "put_wall": 94.0, "flip": 98.0, "max_pain": 96.0, "gex": -1e6, "front_days": 10}, u=1.0)
    side, entry, exit_px = t["wall_break"]
    assert side == -1 and exit_px < entry and t["wall_fade"] is None


def test_api():
    with TestClient(app) as c:
        r = c.get("/api/strategy/sigma/NG?ref=month_weekday&k=3")
        assert r.status_code == 200 and r.json()["today"]["k"] == 3
        assert c.get("/api/strategy/sigma/NG?ref=nope").status_code == 400
        r = c.get("/api/strategy/optlevels/ES")
        assert r.status_code == 200 and len(r.json()["backtest"]["rules"]) == 4
        assert c.get("/api/strategy/optlevels/ZC").status_code == 404
