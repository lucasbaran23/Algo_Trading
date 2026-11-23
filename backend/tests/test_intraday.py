import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data import intraday
from app.data.store import MarketData
from app.intraday import research, topdown
from app.intraday.strategies import opening_range_breakout, intraday_momentum
from app.main import app


@pytest.fixture(scope="module")
def md():
    m = MarketData(Settings())
    m.load()
    return m


def test_sim_minutes_reproduce_daily_bars(md):
    df, src = intraday.bars(md, "CL")
    assert src == "SIM"
    g = df.groupby("session").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    d = md.daily["CL"].set_index("date").tail(len(g))
    d.index = d.index.normalize()
    j = g.join(d, how="inner")
    assert len(j) > 200
    assert np.allclose(j.o, j.open) and np.allclose(j.h, j.high) and np.allclose(j.l, j.low) and np.allclose(j.c, j.close)
    assert (df.high >= df[["open", "close"]].max(axis=1) - 1e-9).all() and (df.low <= df[["open", "close"]].min(axis=1) + 1e-9).all()


def test_csv_loader_roundtrip(tmp_path, monkeypatch, md):
    df, _ = intraday.bars(md, "GC")
    one = df.head(3000).reset_index().rename(columns={"ts": "timestamp"})[["timestamp", "open", "high", "low", "close", "volume"]]
    one.to_csv(tmp_path / "GC_1m.csv", index=False)
    monkeypatch.setattr(intraday, "DATA_DIR", tmp_path)
    intraday._CACHE.clear()
    got, src = intraday.bars(md, "GC")
    assert src == "CSV" and len(got) == 3000 and got["close"].iloc[-1] == one["close"].iloc[-1]
    intraday._CACHE.clear()


def _session(o, h, l, c, prev_close=100.0):
    n = len(c)
    return {"m": np.arange(1, n + 1), "o": np.array(o, float), "h": np.array(h, float), "l": np.array(l, float),
            "c": np.array(c, float), "vwap": np.array(c, float), "prev_close": prev_close, "weekday": 1}


def test_orb_breakout_and_stop():
    n = 120
    c = np.full(n, 100.0); h = c + 0.2; l = c - 0.2; o = c.copy()
    h[:15] = 101; l[:15] = 99                                # 15-minute opening range 99-101
    c[40:] = 102; h[40:] = 102.2; o[40] = 100.5; l[40] = 100.4
    tr = opening_range_breakout(_session(o, h, l, c), 15)
    assert tr == [(1, 101.0, 102.0)]                        # long at the range high, held to the close
    l2 = l.copy(); l2[60] = 98.5                             # later collapse through the range low = stop
    assert opening_range_breakout(_session(o, h, l2, c), 15) == [(1, 101.0, 99.0)]


def test_intraday_momentum_direction():
    n = 390
    c = np.linspace(101, 102, n)                             # up vs prior close 100 -> long the last 30 minutes
    tr = intraday_momentum(_session(c, c, c, c), 30)
    assert tr[0][0] == 1 and tr[0][2] == c[-1]


def test_null_data_finds_no_edge(md):
    out = research.research(md, "ES")
    assert out["source"] == "SIM"
    assert not any(s["verdict"] == "EDGE" for s in out["strategies"])


def test_positive_control_detects_planted_intraday_momentum(md, monkeypatch):
    """Plant a real effect (last 30 min follow the first-30-min direction) and check the engine finds it."""
    df, _ = intraday.bars(md, "ES")
    df = df.copy()
    mins = df.index.hour * 60 + df.index.minute
    rth = (mins > 570) & (mins <= 960)
    for _, g in df[rth].groupby(df[rth].index.normalize()):
        first = g[(g.index.hour * 60 + g.index.minute) <= 600]
        prev = df[df.index < g.index[0]]
        if prev.empty or first.empty:
            continue
        sign = np.sign(first["close"].iloc[-1] - prev[(prev.index.hour * 60 + prev.index.minute) == 960]["close"].iloc[-1]
                       if ((prev.index.hour * 60 + prev.index.minute) == 960).any() else 0)
        last = g.index[(g.index.hour * 60 + g.index.minute) > 930]
        ramp = sign * 0.0008 * g["close"].iloc[0] * np.linspace(1 / len(last), 1, len(last))
        for col in ("open", "high", "low", "close"):
            df.loc[last, col] += ramp
    monkeypatch.setattr(intraday, "bars", lambda md_, sym, sessions=250: (df, "CSV"))
    research._CACHE.clear()
    out = research.research(md, "ES")
    mom = next(s for s in out["strategies"] if s["key"] == "intraday_momentum")
    assert mom["verdict"] in ("EDGE", "PROMISING"), mom["verdict"]
    assert mom["best_tf"] == 30 and mom["oos"]["t_stat"] > 2
    assert out["strategies"][0]["key"] == "intraday_momentum"
    research._CACHE.clear()


def test_topdown_structure(md):
    t = topdown.topdown(md, "NQ")
    assert [r["tf"] for r in t["rows"]] == ["W", "D", "4H", "1H", "15m", "5m", "1m"]
    assert -100 <= t["alignment"] <= 100 and t["plan"] and t["exec_tf"] in ("5m", "15m")
    for r in t["rows"]:
        assert -2 <= r["score"] <= 2 and len(r["spark"]) <= 60


def test_api_endpoints():
    with TestClient(app) as c:
        r = c.get("/api/intraday/research/MES").json()        # micro maps to parent data
        assert r["data_symbol"] == "ES" and r["strategies"]
        assert c.get("/api/intraday/topdown/CL").status_code == 200
        assert c.get("/api/intraday/research/XXX").status_code == 404
        d = c.get("/api/insider/ES").json()
        assert d["topdown"]["rows"] and any("Intraday research" in p for p in d["plan"])
