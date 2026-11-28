"""Request authentication and tier gating.

AUTH_MODE=off (default): everyone is the local owner (uid 0) with LOCAL_TIER (default "ultimate").
AUTH_MODE=on: the "session" cookie (or Authorization: Bearer <token>) identifies the user; /api paths
are checked against tiers.FEATURE_PATHS and return 401 (sign in) or 402 (upgrade) as needed.
"""
from __future__ import annotations

import os

from . import store, tiers

COOKIE = "session"
PUBLIC_PATHS = ("/api/auth/", "/api/plans", "/api/health", "/api/billing/webhook")


def auth_on() -> bool:
    return os.getenv("AUTH_MODE", "off").strip().lower() == "on"


def local_user() -> store.User:
    t = os.getenv("LOCAL_TIER", "ultimate").strip().lower()
    return store.User(id=0, email="local@owner", tier=t if t in tiers.TIERS else "ultimate", is_admin=True)


def token_from(headers, cookies) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return cookies.get(COOKIE)


def user_from(headers, cookies) -> store.User | None:
    if not auth_on():
        return local_user()
    return store.user_for_token(token_from(headers, cookies))


def gate(path: str, user: store.User | None) -> tuple[int, dict] | None:
    """None if allowed, else (status, body)."""
    if not path.startswith("/api/") or path.startswith(PUBLIC_PATHS):
        return None
    need = tiers.required_tier(path)
    if user is None:
        if need == "free" and os.getenv("ANON_FREE", "on").lower() == "on":
            return None                                  # browse the free tier without an account
        return 401, {"detail": {"message": "Sign in to continue.", "signin": True, "tier": need}}
    if not tiers.allows(user.tier, need):
        return 402, {"detail": {"message": f"This feature is part of the {tiers.PLANS[need]['name']} plan.",
                                "upgrade": need, "tier": user.tier}}
    return None
