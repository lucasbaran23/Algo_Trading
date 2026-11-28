"""Hosted mode: accounts, tier gating, per-user workspaces and Stripe webhooks."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.accounts import billing, store, tiers
from app.main import app


@pytest.fixture()
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "on")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    store.reset_for_tests()
    with TestClient(app) as c:
        yield c
    store.reset_for_tests()


def _signup(c, email):
    r = c.post("/api/auth/signup", json={"email": email, "password": "correct horse"})
    assert r.status_code == 200, r.text
    return r


def test_password_hashing():
    h = store.hash_password("s3cret-pass")
    assert store.check_password("s3cret-pass", h) and not store.check_password("wrong", h)
    assert h.startswith("scrypt$") and "s3cret" not in h


def test_tier_paths():
    assert tiers.required_tier("/api/natgas/balance") == "free"
    assert tiers.required_tier("/api/insider/NG") == "pro"
    assert tiers.required_tier("/api/intraday/research/NG") == "premium"   # longest prefix wins over /api/intraday/
    assert tiers.required_tier("/api/backtest/gammascalp") == "premium"
    assert tiers.allows("ultimate", "premium") and not tiers.allows("pro", "premium")


def test_signup_login_and_gating(hosted):
    c = hosted
    assert c.get("/api/natgas/balance").status_code == 200                 # free tier without an account
    assert c.get("/api/prop").status_code == 401                           # pro feature: sign in first
    _signup(c, "trader@example.com")
    me = c.get("/api/auth/me").json()
    assert me["user"]["email"] == "trader@example.com" and me["tier"] == "free" and "DT" in me["locked_views"]
    r = c.get("/api/insider/NG")
    assert r.status_code == 402 and r.json()["detail"]["upgrade"] == "pro"
    u = store.get_user_by_email("trader@example.com")
    store.set_tier(u.id, "pro")
    assert c.get("/api/prop").status_code == 200
    assert c.get("/api/options/NG").status_code == 402                     # premium
    c.post("/api/auth/logout")
    assert c.get("/api/prop").status_code == 401
    assert c.post("/api/auth/login", json={"email": "trader@example.com", "password": "nope-nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "Trader@Example.com", "password": "correct horse"}).status_code == 200
    assert c.post("/api/auth/signup", json={"email": "trader@example.com", "password": "correct horse"}).status_code == 400


def test_workspaces_are_isolated(hosted, tmp_path, monkeypatch):
    c = hosted
    for email in ("a@x.com", "b@x.com"):
        _signup(c, email)
        store.set_tier(store.get_user_by_email(email).id, "pro")
    c.post("/api/auth/login", json={"email": "a@x.com", "password": "correct horse"})
    c.post("/api/prop/rules", json={"preset": "eval_100k"} if "eval_100k" in c.get("/api/prop/presets").json() else
           {"preset": next(iter(c.get("/api/prop/presets").json()))})
    c.post("/api/orders", json={"symbol": "ES", "side": "BUY", "qty": 1, "stop_ticks": 20, "override": True})
    a = c.get("/api/prop").json()
    c.post("/api/auth/login", json={"email": "b@x.com", "password": "correct horse"})
    b = c.get("/api/prop").json()
    assert a["positions"] and not b["positions"]


def test_stripe_webhook_signature_and_tier_change(hosted, monkeypatch):
    c = hosted
    _signup(c, "payer@x.com")
    u = store.get_user_by_email("payer@x.com")
    store.set_tier(u.id, "free", customer="cus_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_PREMIUM", "price_prem")
    ev = {"type": "customer.subscription.updated",
          "data": {"object": {"id": "sub_1", "customer": "cus_123", "status": "active",
                              "items": {"data": [{"price": {"id": "price_prem"}}]}}}}
    body = json.dumps(ev).encode()
    t = str(int(time.time()))
    sig = hmac.new(b"whsec_test", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    bad = c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1=deadbeef"})
    assert bad.status_code == 400 and store.get_user(u.id).tier == "free"
    ok = c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1={sig}"})
    assert ok.status_code == 200 and store.get_user(u.id).tier == "premium"
    ev["type"] = "customer.subscription.deleted"
    body = json.dumps(ev).encode()
    sig = hmac.new(b"whsec_test", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1={sig}"})
    assert store.get_user(u.id).tier == "free"
    assert billing.tier_for_price("price_prem") == "premium"


def test_local_mode_is_unlocked():
    with TestClient(app) as c:
        me = c.get("/api/auth/me").json()
        assert me["auth_mode"] == "off" and me["tier"] == "ultimate" and me["locked_views"] == []
        assert c.get("/api/prop").status_code == 200
