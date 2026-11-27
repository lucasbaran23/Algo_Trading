"""Offline tests for the keyed / external connectors (responses are mocked)."""
import json

import pandas as pd
import pytest

from app.config import Settings
from app.data import databento
from app.natgas import eia_api
from app.natgas import weather as wx


class _Resp:
    def __init__(self, payload, status=200, text=None):
        self._p, self.status_code, self.text = payload, status, text if text is not None else json.dumps(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_databento_csv_to_bar_close_eastern():
    csv = ("ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
           "2026-11-20T14:30:00.000000000Z,33,1,123,3.400,3.410,3.395,3.405,120,NGF7\n"
           "2026-11-20T14:31:00.000000000Z,33,1,123,3.405,3.420,3.400,3.418,95,NGF7\n")
    df = databento.parse_csv(csv)
    assert list(df.index.strftime("%H:%M")) == ["09:31", "09:32"]          # 14:30Z bar opens 09:30 ET, closes 09:31
    assert df["close"].iloc[-1] == pytest.approx(3.418) and (df["res"] == 1).all()


def test_databento_needs_key(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    out = databento.backfill(None, "NG", 1)
    assert not out["ok"] and "signup" in out


def test_databento_spend_guard(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    monkeypatch.setattr(databento, "cost", lambda *a: 4.0)
    called = []
    monkeypatch.setattr(databento, "fetch", lambda *a: called.append(a))
    out = databento.backfill(None, "NG", 2, max_usd=5)                       # 2 chunks x $4 = $8 > $5
    assert not out["ok"] and out["cost_usd"] == 8.0 and not called


def test_eia_power_burn_calibrates_to_steo(monkeypatch, tmp_path):
    days = pd.date_range("2025-06-01", "2026-10-31", freq="D")
    rows = [{"period": str(d.date()), "value": 4_400_000.0} for d in days]  # MWh per day
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp({"response": {"data": rows}}))
    import dataclasses
    s = dataclasses.replace(Settings(), eia_api_key="k", cache_dir=tmp_path)
    steo = pd.DataFrame({"NGEPCON": 33.0}, index=pd.date_range("2025-06-01", "2026-10-01", freq="MS"))
    pb = eia_api.power_burn(s, steo)
    assert pb["available"] and pb["last_bcfd"] == pytest.approx(33.0, abs=0.01)
    assert "calibrated" in pb["conversion"]


def test_eia_without_key_points_to_registration():
    import dataclasses
    s = dataclasses.replace(Settings(), eia_api_key="")
    assert eia_api.power_burn(s)["register"].startswith("https://www.eia.gov/opendata")


def test_weather_model_runs_parse(monkeypatch, tmp_path):
    t = pd.date_range(pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=17 * 24, freq="h")
    city = {"hourly": {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t],
                       "temperature_2m_gfs_seamless": [30.0] * len(t), "temperature_2m_previous_day1_gfs_seamless": [35.0] * len(t),
                       "temperature_2m_ecmwf_ifs025": [40.0] * len(t), "temperature_2m_previous_day1_ecmwf_ifs025": [40.0] * len(t)}}
    monkeypatch.setattr(wx, "_get", lambda url, params: [city] * len(wx.CITIES))
    out = wx._forecast_models(tmp_path)
    g, e = out["models"]["gfs"], out["models"]["ecmwf"]
    assert g["next14_hdd"] == pytest.approx(14 * 35, abs=1) and g["chg_vs_prev_run"] > 60     # 5 °F colder for 14 days
    assert e["chg_vs_prev_run"] == pytest.approx(0, abs=0.5)
