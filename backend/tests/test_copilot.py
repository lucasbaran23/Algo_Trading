"""Prop Copilot: deterministic tools, agent wiring (fake client, no API calls) and quotas."""
import types

import pytest
from fastapi.testclient import TestClient

from app.accounts import store
from app.copilot import agent, tools as T
from app.main import app, hub
from app.prop.rules import PropRules


@pytest.fixture()
def client():
    with TestClient(app) as c:
        hub.ws(0).prop.reset(PropRules())                 # 50K trailing EOD, $1,000 daily limit, 5 contracts
        yield c


def test_firm_templates_load_and_map_to_rules():
    f = T.firm_rules()
    assert {"topstep", "apex", "ftmo"} <= set(f["firms"])
    r = T.rules_for("apex", "50K")
    assert r.drawdown_mode == "trailing_intraday" and r.max_drawdown == 2500
    assert "unknown" in T.firm_rules("nonexistentfirm")["error"]


def test_sizing_and_trade_check_follow_the_account(client):
    ws = hub.ws(0)
    s = T.size_position(hub, ws, "NG", 25, 20)
    # 20% of min(drawdown buffer 2000, daily left 1000) = $200 → 0 NG ($255/contract) but 6 MNG ($30)
    assert s["full"]["contracts"] == 0 and s["micro"]["contracts"] == 6
    bad = T.check_trade(hub, ws, "NG", "BUY", 6, 25)
    assert not bad["ok"] and any("max contracts" in x for x in bad["reasons"])
    assert T.check_trade(hub, ws, "MNG", "SELL", 2, 25)["ok"]


def test_daily_plan_budget_and_rules(client):
    p = T.daily_plan(hub, hub.ws(0), ["NG", "ES"])
    assert p["day_loss_budget_usd"] == 600                # 60% of the $1,000 daily limit
    assert {m["symbol"] for m in p["markets"]} == {"NG", "ES"}
    assert all(m["contracts"] <= 5 for m in p["markets"] if "contracts" in m)
    assert any("Consistency" in r for r in p["day_rules"])


def test_pass_odds_monotone_in_edge(client):
    ws = hub.ws(0)
    good = T.pass_odds(hub, ws, 0.6, 300, 200, 3, sims=1500)
    bad = T.pass_odds(hub, ws, 0.4, 200, 250, 3, sims=1500)
    assert good["pass_pct"] > bad["pass_pct"] and bad["fail_pct"] > good["fail_pct"]
    assert good["expectancy_per_trade_usd"] > 0 > bad["expectancy_per_trade_usd"]


class _FakeRunner:
    def __init__(self, params):
        self.params = params

    def until_done(self):
        # call one tool the way the model would, to prove the closures reach the user's workspace
        acct = next(t for t in self.params["tools"] if t.name == "get_account_status")
        self.params["_tool_result"] = acct.call({})
        return types.SimpleNamespace(stop_reason="end_turn", model=self.params["model"],
                                     content=[types.SimpleNamespace(type="text", text="Your buffer is fine.")],
                                     usage=types.SimpleNamespace(input_tokens=1200, output_tokens=80,
                                                                 cache_read_input_tokens=900, cache_creation_input_tokens=0))


def test_agent_wiring(monkeypatch, client):
    seen = {}

    def fake_runner(**params):
        seen.update(params)
        return _FakeRunner(seen)

    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=fake_runner)))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    cp = agent.Copilot()
    out = cp.ask(hub, hub.ws(0), 0, "How much room do I have?")
    assert out["reply"] == "Your buffer is fine." and "account" in out["tools_used"]
    assert seen["model"] == "claude-opus-5-5" and seen["thinking"] == {"type": "adaptive"}
    assert seen["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["betas"]
    assert {t.name for t in seen["tools"]} >= {"build_daily_risk_plan", "check_trade", "estimate_pass_odds"}
    assert "distance_to_breach" in str(seen["_tool_result"])
    assert len(cp.history[0]) == 2                        # question + answer kept for the next turn


def test_copilot_quota_in_hosted_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "on")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    store.reset_for_tests()
    with TestClient(app) as c:
        c.post("/api/auth/signup", json={"email": "p@x.com", "password": "correct horse"})
        u = store.get_user_by_email("p@x.com")
        assert c.get("/api/copilot/state").status_code == 402          # free: premium feature
        store.set_tier(u.id, "premium")
        st = c.get("/api/copilot/state").json()
        assert st["quota"]["limit"] == 200 and st["quota"]["remaining"] == 200
        store.add_usage(u.id, "copilot", st["quota"]["period"], 200)
        r = c.post("/api/copilot/chat", json={"message": "hi"})
        assert r.status_code == 402 and "200" in r.json()["detail"]["message"]
        assert c.get("/api/copilot/plan?symbols=NG").status_code == 200
    store.reset_for_tests()
