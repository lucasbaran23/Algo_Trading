"""Stripe subscriptions (Checkout + Customer Portal + webhooks), over plain HTTPS.

Setup (Stripe dashboard → Products): create one recurring price per paid tier, then set
  STRIPE_SECRET_KEY=sk_live_... (or sk_test_...)
  STRIPE_WEBHOOK_SECRET=whsec_...           (Developers → Webhooks → endpoint https://YOUR_DOMAIN/api/billing/webhook,
                                             events: checkout.session.completed, customer.subscription.updated,
                                             customer.subscription.deleted)
  STRIPE_PRICE_PRO=price_...  STRIPE_PRICE_PREMIUM=price_...  STRIPE_PRICE_ULTIMATE=price_...
  PUBLIC_URL=https://YOUR_DOMAIN
The webhook is the source of truth: a tier changes only when Stripe confirms the subscription.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from . import store

API = "https://api.stripe.com/v1/"
PAID = ("pro", "premium", "ultimate")


def _price(tier: str) -> str:
    return os.getenv(f"STRIPE_PRICE_{tier.upper()}", "").strip()


def configured() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY")) and all(_price(t) for t in PAID)


def tier_for_price(price_id: str) -> str | None:
    return next((t for t in PAID if _price(t) == price_id), None)


def _post(path: str, data: dict) -> dict:
    import httpx
    r = httpx.post(API + path, data=data, auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe {r.status_code}: {r.text[:300]}")
    return r.json()


def checkout_url(user: store.User, tier: str) -> str:
    if tier not in PAID:
        raise ValueError("choose pro, premium or ultimate")
    base = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    data = {"mode": "subscription", "line_items[0][price]": _price(tier), "line_items[0][quantity]": "1",
            "client_reference_id": str(user.id), "success_url": f"{base}/?billing=success",
            "cancel_url": f"{base}/?billing=cancel", "allow_promotion_codes": "true",
            "subscription_data[metadata][uid]": str(user.id)}
    if user.stripe_customer:
        data["customer"] = user.stripe_customer
    else:
        data["customer_email"] = user.email
    return _post("checkout/sessions", data)["url"]


def portal_url(user: store.User) -> str:
    if not user.stripe_customer:
        raise ValueError("no subscription yet")
    base = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return _post("billing_portal/sessions", {"customer": user.stripe_customer, "return_url": base})["url"]


def verify(payload: bytes, sig_header: str, secret: str, tolerance: int = 300) -> dict:
    """Stripe-Signature: t=timestamp,v1=hex(hmac_sha256(secret, f"{t}.{payload}"))."""
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    t = parts.get("t")
    sigs = [v for k, v in (p.split("=", 1) for p in sig_header.split(",") if "=" in p) if k == "v1"]
    if not t or not sigs:
        raise ValueError("bad signature header")
    if abs(time.time() - int(t)) > tolerance:
        raise ValueError("signature too old")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise ValueError("signature mismatch")
    return json.loads(payload)


def handle(event: dict) -> str:
    """Apply a verified Stripe event. Returns a short description for logs."""
    typ, obj = event.get("type", ""), event.get("data", {}).get("object", {})
    if typ == "checkout.session.completed":
        uid = int(obj.get("client_reference_id") or 0)
        sub_id = obj.get("subscription")
        tier = None
        if sub_id and os.getenv("STRIPE_SECRET_KEY"):
            import httpx
            r = httpx.get(API + f"subscriptions/{sub_id}", auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=30)
            items = r.json().get("items", {}).get("data", [])
            tier = tier_for_price(items[0]["price"]["id"]) if items else None
        if uid and tier:
            store.set_tier(uid, tier, obj.get("customer"), sub_id)
            return f"user {uid} -> {tier}"
        return "checkout completed (no tier change)"
    if typ in ("customer.subscription.updated", "customer.subscription.deleted"):
        u = store.user_by_customer(obj.get("customer", ""))
        if u is None:
            uid = int((obj.get("metadata") or {}).get("uid") or 0)
            u = store.get_user(uid) if uid else None
        if u is None:
            return "subscription event for unknown customer"
        active = obj.get("status") in ("active", "trialing", "past_due") and typ.endswith("updated")
        items = obj.get("items", {}).get("data", [])
        tier = tier_for_price(items[0]["price"]["id"]) if (active and items) else None
        store.set_tier(u.id, tier or "free", obj.get("customer"), obj.get("id"))
        return f"user {u.id} -> {tier or 'free'}"
    return f"ignored {typ}"
