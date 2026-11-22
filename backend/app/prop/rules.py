"""Prop-firm evaluation / funded-account rule engine.

Models the rule families used across futures prop firms: profit target, daily loss
limit, max drawdown (static, trailing intraday, trailing end-of-day), trailing
lock at the starting balance, contract limits, consistency rule and minimum
trading days. Firms change their rules often - presets here are GENERIC
templates; users must enter their own firm's current numbers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .. import universe


@dataclass
class PropRules:
    name: str = "Eval 50K - Trailing EOD"
    account_size: float = 50_000
    profit_target: float = 3_000          # 0 for funded accounts
    daily_loss_limit: float | None = 1_000
    max_drawdown: float = 2_000
    drawdown_mode: str = "trailing_eod"   # static | trailing_intraday | trailing_eod
    trail_locks_at_start: bool = True     # threshold stops trailing once it reaches start balance
    max_contracts: int = 5                # in full-size contracts; micros count 1/10
    consistency_pct: float | None = 50.0  # best day must be <= X% of total profit
    min_trading_days: int = 2
    commission_per_side: float = 2.50     # per contract, round-turn = 2x

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS: dict[str, PropRules] = {
    "eval_50k_eod": PropRules(),
    "eval_100k_eod": PropRules("Eval 100K - Trailing EOD", 100_000, 6_000, 2_000, 3_000, max_contracts=10),
    "eval_150k_eod": PropRules("Eval 150K - Trailing EOD", 150_000, 9_000, 3_000, 4_500, max_contracts=15),
    "eval_50k_intraday": PropRules("Eval 50K - Trailing Intraday", 50_000, 3_000, None, 2_500,
                                   "trailing_intraday", max_contracts=10, consistency_pct=None,
                                   min_trading_days=7),
    "static_100k": PropRules("Eval 100K - Static DD", 100_000, 8_000, 2_500, 5_000, "static",
                             trail_locks_at_start=False, max_contracts=8, consistency_pct=None),
    "funded_50k": PropRules("Funded 50K - Trailing EOD", 50_000, 0, 1_000, 2_000, max_contracts=5,
                            consistency_pct=40.0, min_trading_days=0),
}


def contract_weight(symbol: str) -> float:
    return 0.1 if universe.get(symbol).parent else 1.0


@dataclass
class Position:
    qty: int = 0
    avg: float = 0.0


@dataclass
class PropAccount:
    rules: PropRules = field(default_factory=PropRules)
    balance: float = 0.0
    hwm: float = 0.0                      # high-water mark used for trailing drawdown
    day: str = field(default_factory=lambda: date.today().isoformat())
    day_start_balance: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    daily_pnl: dict[str, float] = field(default_factory=dict)  # closed days: date -> pnl
    trades: list[dict] = field(default_factory=list)
    breached: str | None = None

    def __post_init__(self):
        if not self.balance:
            self.reset()

    def reset(self, rules: PropRules | None = None) -> None:
        if rules:
            self.rules = rules
        self.balance = self.rules.account_size
        self.hwm = self.rules.account_size
        self.day_start_balance = self.balance
        self.day = date.today().isoformat()
        self.positions = {}
        self.daily_pnl = {}
        self.trades = []
        self.breached = None

    # ------------------------------------------------------------------ marking
    def unrealized(self, prices: dict[str, float]) -> float:
        tot = 0.0
        for sym, p in self.positions.items():
            if p.qty:
                c = universe.get(sym)
                tot += (prices[c.data_symbol] - p.avg) * p.qty * c.point_value
        return tot

    def equity(self, prices: dict[str, float]) -> float:
        return self.balance + self.unrealized(prices)

    def threshold(self) -> float:
        r = self.rules
        if r.drawdown_mode == "static":
            return r.account_size - r.max_drawdown
        t = self.hwm - r.max_drawdown
        if r.trail_locks_at_start:
            t = min(t, r.account_size)
        return t

    def open_contracts(self) -> float:
        return sum(abs(p.qty) * contract_weight(s) for s, p in self.positions.items())

    # ------------------------------------------------------------------ events
    def on_fill(self, symbol: str, qty: int, price: float, ts: float, tag: str = "") -> float:
        """Apply a fill (qty>0 buy, <0 sell). Returns realised P&L net of commission."""
        c = universe.get(symbol)
        commission = self.rules.commission_per_side * abs(qty)
        pos = self.positions.setdefault(symbol, Position())
        realized = 0.0
        if pos.qty and (pos.qty > 0) != (qty > 0):
            closing = min(abs(qty), abs(pos.qty)) * (1 if qty > 0 else -1)
            realized = (price - pos.avg) * (-closing) * c.point_value
            pos.qty += closing
            qty -= closing
            if pos.qty == 0:
                pos.avg = 0.0
        if qty:
            new_qty = pos.qty + qty
            pos.avg = (pos.avg * pos.qty + price * qty) / new_qty
            pos.qty = new_qty
        net = realized - commission
        self.balance += net
        self.trades.append({"ts": ts, "symbol": symbol, "price": price, "realized": round(net, 2), "tag": tag})
        return net

    def on_mark(self, prices: dict[str, float]) -> None:
        eq = self.equity(prices)
        if self.rules.drawdown_mode == "trailing_intraday":
            self.hwm = max(self.hwm, eq)
        if self.breached:
            return
        if eq <= self.threshold():
            self.breached = f"Max drawdown breached: equity {eq:,.2f} <= threshold {self.threshold():,.2f}"
        elif self.rules.daily_loss_limit and eq - self.day_start_balance <= -self.rules.daily_loss_limit:
            self.breached = f"Daily loss limit hit: {eq - self.day_start_balance:,.2f}"

    def end_of_day(self, prices: dict[str, float], new_day: str | None = None) -> None:
        pnl = self.equity(prices) - self.day_start_balance
        self.daily_pnl[self.day] = self.daily_pnl.get(self.day, 0.0) + pnl
        if self.rules.drawdown_mode == "trailing_eod":
            self.hwm = max(self.hwm, self.equity(prices))
        self.day = new_day or date.today().isoformat()
        self.day_start_balance = self.equity(prices)

    # ------------------------------------------------------------------ checks
    def pre_trade_check(self, symbol: str, qty: int, stop_ticks: int | None, prices: dict[str, float]) -> dict:
        c = universe.get(symbol)
        reasons, warnings = [], []
        if self.breached:
            reasons.append(f"Account breached: {self.breached}")
        pos = self.positions.get(symbol, Position())
        new_open = self.open_contracts() - abs(pos.qty) * contract_weight(symbol) + abs(pos.qty + qty) * contract_weight(symbol)
        if new_open > self.rules.max_contracts + 1e-9:
            reasons.append(f"Exceeds max contracts ({new_open:g} > {self.rules.max_contracts})")
        st = self.status(prices)
        worst = None
        if stop_ticks:
            worst = abs(qty) * stop_ticks * c.tick_value + 2 * self.rules.commission_per_side * abs(qty)
            if worst >= st["distance_to_breach"]:
                reasons.append(f"Stop-out loss ${worst:,.0f} would breach max drawdown (buffer ${st['distance_to_breach']:,.0f})")
            if st["daily_loss_remaining"] is not None and worst >= st["daily_loss_remaining"]:
                reasons.append(f"Stop-out loss ${worst:,.0f} would hit daily loss limit (left ${st['daily_loss_remaining']:,.0f})")
            elif st["daily_loss_remaining"] is not None and worst >= 0.5 * st["daily_loss_remaining"]:
                warnings.append("Trade risks more than 50% of remaining daily loss allowance")
        else:
            warnings.append("No stop defined - risk is unbounded")
        return {"ok": not reasons, "reasons": reasons, "warnings": warnings, "worst_case_loss": worst}

    def risk_size(self, symbol: str, stop_ticks: int, risk_pct_of_buffer: float, prices: dict[str, float]) -> dict:
        """Contracts so that a stop-out costs `risk_pct_of_buffer`% of the smaller of the DD / daily buffers."""
        c = universe.get(symbol)
        st = self.status(prices)
        buffers = [st["distance_to_breach"]]
        if st["daily_loss_remaining"] is not None:
            buffers.append(st["daily_loss_remaining"])
        budget = max(0.0, min(buffers)) * risk_pct_of_buffer / 100
        per_contract = stop_ticks * c.tick_value + 2 * self.rules.commission_per_side
        n = int(budget // per_contract) if per_contract > 0 else 0
        cap = int(self.rules.max_contracts / contract_weight(symbol))
        return {
            "contracts": min(n, cap), "risk_budget": budget, "risk_per_contract": per_contract,
            "capped_by_max_contracts": n > cap,
        }

    def status(self, prices: dict[str, float]) -> dict:
        r = self.rules
        eq = self.equity(prices)
        today = eq - self.day_start_balance
        thr = self.threshold()
        profit = eq - r.account_size
        days = dict(self.daily_pnl)
        days[self.day] = days.get(self.day, 0.0) + today
        trading_days = sum(1 for v in days.values() if abs(v) > 1e-9)
        best_day = max(days.values()) if days else 0.0
        consistency_ok = True
        consistency_ratio = None
        if r.consistency_pct and profit > 0:
            consistency_ratio = best_day / profit * 100
            consistency_ok = consistency_ratio <= r.consistency_pct
        target_hit = r.profit_target > 0 and profit >= r.profit_target
        passed = bool(target_hit and consistency_ok and trading_days >= r.min_trading_days and not self.breached)
        dl_left = (r.daily_loss_limit + today) if r.daily_loss_limit else None
        return {
            "rules": r.to_dict(),
            "balance": round(self.balance, 2),
            "equity": round(eq, 2),
            "unrealized": round(eq - self.balance, 2),
            "today_pnl": round(today, 2),
            "total_pnl": round(profit, 2),
            "threshold": round(thr, 2),
            "high_water_mark": round(self.hwm, 2),
            "distance_to_breach": round(eq - thr, 2),
            "daily_loss_remaining": round(dl_left, 2) if dl_left is not None else None,
            "target_progress_pct": round(100 * profit / r.profit_target, 1) if r.profit_target else None,
            "trading_days": trading_days,
            "best_day": round(best_day, 2),
            "consistency_ratio": round(consistency_ratio, 1) if consistency_ratio is not None else None,
            "consistency_ok": consistency_ok,
            "open_contracts": self.open_contracts(),
            "positions": {s: {"qty": p.qty, "avg": p.avg} for s, p in self.positions.items() if p.qty},
            "breached": self.breached,
            "passed": passed,
            "trades": self.trades[-50:],
        }
