import pytest

from app.prop.brokers import Order, PaperBroker, front_month
from app.prop.rules import PRESETS, PropAccount, PropRules

PX = {"CL": 70.0, "GC": 4000.0}


def acct(**kw):
    return PropAccount(PropRules(commission_per_side=0, **kw))


def test_round_trip_pnl_and_commission():
    a = PropAccount(PropRules(commission_per_side=2.5))
    a.on_fill("CL", 2, 70.00, 0)
    a.on_fill("CL", -2, 70.50, 1)
    assert a.balance == pytest.approx(50_000 + 0.5 * 1000 * 2 - 10)
    assert a.positions["CL"].qty == 0


def test_micro_uses_parent_price_and_weight():
    a = acct()
    a.on_fill("MCL", 10, 70.0, 0)
    assert a.unrealized({"CL": 71.0}) == pytest.approx(10 * 100)
    assert a.open_contracts() == pytest.approx(1.0)


def test_trailing_eod_threshold_and_lock():
    a = acct(drawdown_mode="trailing_eod", max_drawdown=2000)
    assert a.threshold() == 48_000
    a.balance = 51_000
    a.end_of_day(PX)
    assert a.threshold() == 49_000
    a.balance = 53_000
    a.end_of_day(PX)
    assert a.threshold() == 50_000  # locked at starting balance


def test_trailing_intraday_follows_open_equity():
    a = acct(drawdown_mode="trailing_intraday", max_drawdown=2500, trail_locks_at_start=False, daily_loss_limit=None)
    a.on_fill("CL", 1, 70.0, 0)
    a.on_mark({"CL": 71.0})       # +1000 open profit
    assert a.threshold() == pytest.approx(51_000 - 2500)
    a.on_mark({"CL": 68.4})       # equity 48,400 < 48,500
    assert a.breached and "drawdown" in a.breached


def test_daily_loss_limit_breach():
    a = acct(daily_loss_limit=1000, max_drawdown=5000)
    a.on_fill("CL", 1, 70.0, 0)
    a.on_mark({"CL": 68.9})
    assert a.breached and "Daily loss" in a.breached


def test_pre_trade_check_blocks_oversize_and_breaching_stop():
    a = acct(max_contracts=5, daily_loss_limit=1000)
    chk = a.pre_trade_check("CL", 6, 10, PX)
    assert not chk["ok"] and any("max contracts" in r for r in chk["reasons"])
    chk = a.pre_trade_check("CL", 1, 150, PX)  # $1,500 stop > $1,000 daily allowance
    assert not chk["ok"]
    assert a.pre_trade_check("CL", 1, 20, PX)["ok"]


def test_risk_size_respects_budget_and_cap():
    a = acct(max_contracts=5, daily_loss_limit=1000)
    r = a.risk_size("CL", 10, 50, PX)           # budget $500, $100/contract
    assert r["contracts"] == 5 and not r["capped_by_max_contracts"]
    r = a.risk_size("CL", 2, 100, PX)           # budget $1000 / $20 = 50 -> capped at 5
    assert r["contracts"] == 5 and r["capped_by_max_contracts"]


def test_consistency_rule_and_pass():
    a = acct(profit_target=3000, consistency_pct=50, min_trading_days=2)
    a.balance = 53_000
    a.end_of_day(PX, "d2")
    assert not a.status(PX)["passed"]          # one 3000 day = 100% of profit
    a.balance = 56_000
    a.end_of_day(PX, "d3")
    st = a.status(PX)
    assert st["consistency_ratio"] == pytest.approx(50.0) and st["passed"]


def test_paper_bracket_oco():
    a = acct()
    prices = {"CL": 70.0}
    b = PaperBroker(lambda s: prices["CL"], lambda o: a.on_fill(o.symbol, o.qty, o.fill_price, 0), slippage_ticks=0)
    b.place(Order("CL", 1, stop_ticks=20, target_ticks=40))
    assert len(b.working) == 2
    filled = b.on_prices({"CL": 70.45})
    assert len(filled) == 1 and filled[0].type == "LIMIT"
    assert not b.working                      # stop cancelled (OCO)
    assert a.balance == pytest.approx(50_000 + 400)


def test_presets_valid():
    for p in PRESETS.values():
        PropAccount(PropRules(**p.to_dict()))


def test_front_month_format():
    import time
    t = time.strptime("2026-11-22", "%Y-%m-%d")
    assert front_month("CL", t) == "CLF7"
    assert front_month("ZC", t) == "ZCZ6" or front_month("ZC", t) == "ZCH7"
