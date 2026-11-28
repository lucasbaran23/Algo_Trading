"""Subscription tiers and what each one unlocks.

Gating is by API path prefix (see FEATURE_PATHS), enforced once in middleware, so new endpoints only
need a line here. The frontend reads /api/auth/me to show locks and the plans page.
"""
from __future__ import annotations

TIERS = ["free", "pro", "premium", "ultimate"]
RANK = {t: i for i, t in enumerate(TIERS)}

PLANS = {
    "free": {"name": "Free", "price_month": 0, "tagline": "Fundamentals and the Nat Gas Desk",
             "features": ["Market monitor (delayed)", "Nat Gas Desk: curve, storage, supply & demand, weather, seasonality",
                          "COT positioning", "Supply & demand screen", "News feed and market alerts"]},
    "pro": {"name": "Pro", "price_month": 34, "tagline": "The day-trading workflow",
            "features": ["Everything in Free", "Day-Trade Insider for every market", "Trade signals with entry, stop, targets",
                         "Notifications (Discord, Telegram, webhook)", "25 custom price/indicator alerts", "Backtest lab: run, optimise, walk-forward, Monte Carlo",
                         "Prop desk: rules monitor, risk sizing, paper trading", "Signal matrix and forecasts"]},
    "premium": {"name": "Premium", "price_month": 89, "tagline": "Edge research and the AI copilot",
                "features": ["Everything in Pro", "Prop Copilot AI (200 questions / month)", "Options & gamma (GEX, walls, skew)",
                             "Gamma scalp lab", "Levels & σ-fade, vol-divergence strategies", "Intraday research and top-down analysis"]},
    "ultimate": {"name": "Ultimate", "price_month": 229, "tagline": "Multi-account prop trading",
                 "features": ["Everything in Premium", "Prop Copilot fair-use (1,000 questions / month)",
                              "Multiple prop accounts and firms", "Live order routing through your own broker / prop API",
                              "Priority support and feature requests"]},
}

LIMITS = {
    "free": {"copilot_month": 0, "alerts": 1, "workspaces": 1},
    "pro": {"copilot_month": 0, "alerts": 25, "workspaces": 1},
    "premium": {"copilot_month": 200, "alerts": 100, "workspaces": 1},
    "ultimate": {"copilot_month": 1000, "alerts": 500, "workspaces": 5},
}

# (path prefix, minimum tier). The longest matching prefix wins; unlisted /api paths are free.
FEATURE_PATHS: list[tuple[str, str]] = [
    ("/api/insider", "pro"), ("/api/tsignals", "pro"), ("/api/matrix", "pro"), ("/api/forecast", "pro"),
    ("/api/backtest", "pro"), ("/api/prop", "pro"), ("/api/orders", "pro"), ("/api/intraday/", "pro"),
    ("/api/alerts/rules", "pro"),
    ("/api/options", "premium"), ("/api/backtest/gammascalp", "premium"), ("/api/backtest/gamma-regime", "premium"),
    ("/api/strategy", "premium"), ("/api/intraday/research", "premium"), ("/api/intraday/topdown", "premium"),
    ("/api/copilot", "premium"),
    ("/api/workspaces", "ultimate"),
]

# Frontend views -> minimum tier (locks shown on the tabs)
VIEW_TIERS = {"DT": "pro", "TS": "pro", "SIG": "pro", "FCST": "pro", "PROP": "pro", "BT": "pro",
              "OPT": "premium", "GS": "premium", "LX": "premium", "IR": "premium", "COP": "premium"}


def required_tier(path: str) -> str:
    best, tier = -1, "free"
    for prefix, t in FEATURE_PATHS:
        if path.startswith(prefix) and len(prefix) > best:
            best, tier = len(prefix), t
    return tier


def allows(user_tier: str, needed: str) -> bool:
    return RANK.get(user_tier, 0) >= RANK[needed]
