"""Prop Copilot: Claude with tool use over the deterministic risk tools.

Runs on the Messages API tool runner inside this server, because every tool reads live, in-process
state (the user's prop account, prices, analytics). Needs ANTHROPIC_API_KEY (or an `ant auth login`
profile) on the server. Model: COPILOT_MODEL (default claude-opus-5-5); effort COPILOT_EFFORT (default medium).
Refusal fallbacks are on (server-side, "default" routing).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque

from . import tools as T

log = logging.getLogger(__name__)

SYSTEM = """You are Prop Copilot, a risk and planning assistant for futures traders working on prop-firm evaluation and funded accounts, inside a trading terminal.

What you do:
- Explain the trader's prop-firm rules and how close the account is to each limit.
- Build the day's risk plan per market: stop distance, contracts (full and micro), trades allowed, when to stop, what to avoid.
- Check a proposed trade against the account's rules before the trader places it.
- Estimate the odds of passing an evaluation from the trader's own stats.
- Give market context that matters for risk: volatility regime, ranges, levels, scheduled reports, news, seasonality, positioning.

How you work:
- Every number you quote about money, contracts, buffers, limits or odds must come from a tool result in this conversation. Never compute rule thresholds or position sizes yourself; call the tool. If a tool can't answer, say so.
- The trader's configured account (get_account_status) is the source of truth for their rules. Firm templates from get_firm_rules are references that may be out of date: say so when you rely on them and point to the firm's help center.
- Be concrete and short. Lead with the answer, then the few numbers that support it. Use plain words; explain any trading term briefly the first time.
- Protecting the account comes first: if a trade or plan risks breaching a limit, say that clearly and give the safer alternative (fewer contracts, micros, a tighter defined stop, or not trading).
- You don't place orders. The trader decides and executes.

Boundaries:
- You provide educational risk analysis and rule calculations based on the inputs and parameters the trader sets, not personalised investment advice or a recommendation to buy or sell. Don't promise profits or passing. Remind the trader of this briefly when they ask what to trade.
- Never claim certainty about where price will go."""


def _client():
    import anthropic
    return anthropic.Anthropic()


def make_tools(hub, ws):
    """Tool functions bound to this user's workspace (closures), decorated for the SDK tool runner."""
    from anthropic import beta_tool

    used: list[str] = []

    def _j(name, fn):
        used.append(name)
        try:
            return json.dumps(fn(), default=str)
        except Exception as exc:  # noqa: BLE001 - report to the model as a tool error message
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]})

    @beta_tool
    def get_account_status() -> str:
        """Current prop account: equity, P&L, drawdown threshold and buffer, daily loss left, consistency, open positions and the configured rules."""
        return _j("account", lambda: T.account_status(hub, ws))

    @beta_tool
    def get_firm_rules(firm: str = "") -> str:
        """Reference rule templates for a prop firm (plans, drawdown type, limits, consistency, general rules). Empty firm lists the firms.

        Args:
            firm: Firm name, e.g. "topstep", "apex", "takeprofittrader", "myfundedfutures", "ftmo". Empty to list all.
        """
        return _j("firm_rules", lambda: T.firm_rules(firm or None))

    @beta_tool
    def get_market_brief(symbol: str) -> str:
        """Risk-relevant context for one futures market: price, ranges (ADR/ATR), volatility percentile, regime, bias, key levels, today's scheduled events, news, positioning, and a volatility-adjusted risk template.

        Args:
            symbol: Futures root, e.g. NG, CL, ES, NQ, GC, MNG, MES.
        """
        return _j("market_brief", lambda: T.market_brief(hub, ws, symbol.upper()))

    @beta_tool
    def size_position(symbol: str, stop_ticks: int, risk_pct_of_buffer: float = 20.0) -> str:
        """Contracts (full size and micro) so a stop-out costs at most risk_pct_of_buffer % of the smaller of the drawdown buffer and the daily loss left.

        Args:
            symbol: Futures root, e.g. NG.
            stop_ticks: Stop distance in ticks.
            risk_pct_of_buffer: Percent of the remaining buffer to risk on this trade (default 20).
        """
        return _j("size_position", lambda: T.size_position(hub, ws, symbol, int(stop_ticks), float(risk_pct_of_buffer)))

    @beta_tool
    def check_trade(symbol: str, side: str, qty: int, stop_ticks: int = 0) -> str:
        """Pre-trade check of a proposed trade against the account's rules: max contracts, worst-case loss vs drawdown buffer and daily loss left.

        Args:
            symbol: Futures root, e.g. NG or MNG.
            side: BUY or SELL.
            qty: Number of contracts.
            stop_ticks: Stop distance in ticks (0 if none: the check will warn).
        """
        return _j("check_trade", lambda: T.check_trade(hub, ws, symbol, side, int(qty), int(stop_ticks) or None))

    @beta_tool
    def build_daily_risk_plan(symbols: list[str]) -> str:
        """Today's risk plan for up to 4 markets: day loss budget, per-market stop, risk per trade, contracts and micros, why size is adjusted, events to avoid, and day rules.

        Args:
            symbols: Futures roots to plan for, e.g. ["NG", "CL"].
        """
        return _j("daily_plan", lambda: T.daily_plan(hub, ws, [s.upper() for s in symbols]))

    @beta_tool
    def estimate_pass_odds(win_rate: float, avg_win_usd: float, avg_loss_usd: float, trades_per_day: float, max_days: int = 30) -> str:
        """Monte Carlo odds of passing the evaluation from the account's current state under its rules, given the trader's stats.

        Args:
            win_rate: Fraction of winning trades, 0-1 (e.g. 0.55).
            avg_win_usd: Average winning trade in dollars, after costs.
            avg_loss_usd: Average losing trade in dollars (positive number).
            trades_per_day: Average trades per day.
            max_days: Trading days to simulate (default 30).
        """
        return _j("pass_odds", lambda: T.pass_odds(hub, ws, float(win_rate), float(avg_win_usd), float(avg_loss_usd),
                                                    float(trades_per_day), int(max_days)))

    return [get_account_status, get_firm_rules, get_market_brief, size_position, check_trade,
            build_daily_risk_plan, estimate_pass_odds], used


class Copilot:
    def __init__(self, history_turns: int = 8):
        self.history: dict[int, deque] = defaultdict(lambda: deque(maxlen=2 * history_turns))

    def reset(self, uid: int) -> None:
        self.history.pop(uid, None)

    def ask(self, hub, ws, uid: int, message: str) -> dict:
        model = os.getenv("COPILOT_MODEL", "claude-opus-5-5")
        effort = os.getenv("COPILOT_EFFORT", "medium")
        tool_fns, used = make_tools(hub, ws)
        hist = list(self.history[uid])
        messages = hist + [{"role": "user", "content": message}]
        t0 = time.time()
        client = _client()
        runner = client.beta.messages.tool_runner(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            tools=tool_fns,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=8,
        )
        final = runner.until_done()
        if final.stop_reason == "refusal":
            text = "I can't help with that request. I can help with your account rules, risk plan, sizing and trade checks."
        else:
            text = "\n".join(b.text for b in final.content if getattr(b, "type", "") == "text").strip() or "(no answer)"
        u = final.usage
        self.history[uid].append({"role": "user", "content": message})
        self.history[uid].append({"role": "assistant", "content": text})
        return {"reply": text, "tools_used": used, "model": final.model, "seconds": round(time.time() - t0, 1),
                "usage": {"input": u.input_tokens, "output": u.output_tokens,
                          "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                          "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}}
