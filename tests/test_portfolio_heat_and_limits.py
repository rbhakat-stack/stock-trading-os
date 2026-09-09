from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.limits import (
    daily_loss_limit_hit,
    max_positions_reached,
    max_trades_reached,
    portfolio_heat_exceeded,
    weekly_loss_limit_hit,
)
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.portfolio_heat import compute_portfolio_heat_pct, compute_post_trade_heat_pct


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def test_compute_portfolio_heat_pct():
    assert compute_portfolio_heat_pct(3_000.0, 100_000.0) == 3.0


def test_compute_post_trade_heat_pct():
    assert compute_post_trade_heat_pct(2_000.0, 500.0, 100_000.0) == 2.5


def test_daily_loss_limit_hit_exactly_at_threshold():
    account = _account(net_liquidation_value=98_000.0, daily_start_equity=100_000.0)  # -2.0%
    assert daily_loss_limit_hit(account, DEFAULT_RISK_POLICY) is True  # policy default max_daily_loss_pct=2.0


def test_daily_loss_limit_not_hit_when_within_bounds():
    account = _account(net_liquidation_value=99_000.0, daily_start_equity=100_000.0)  # -1.0%
    assert daily_loss_limit_hit(account, DEFAULT_RISK_POLICY) is False


def test_weekly_loss_limit_hit():
    account = _account(net_liquidation_value=94_000.0, weekly_start_equity=100_000.0)  # -6.0%
    assert weekly_loss_limit_hit(account, DEFAULT_RISK_POLICY) is True  # policy default max_weekly_loss_pct=5.0


# ===================== Daily/weekly loss-limit boundary tests =====================
# Reproduces the exact acceptance-test scenario reported: max_daily_loss_pct=1.00%,
# start-of-day equity 100000, NLV 98500 (-1.50% drawdown) must trip the limit.
# Sign convention: current_daily_drawdown_pct is NEGATIVE when losing, 0 when flat
# (never a positive loss-magnitude) — see AccountRiskState.current_daily_drawdown_pct.
# The limit condition is `drawdown_pct <= -max_loss_pct`, so the limit trips AT
# exactly the threshold, not only past it.

_POLICY_1PCT_DAILY = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.0)
_POLICY_2_5PCT_WEEKLY = replace(DEFAULT_RISK_POLICY, max_weekly_loss_pct=2.5)


def _account_with_daily_drawdown_pct(pct: float):
    # NLV chosen so (NLV - 100000) / 100000 * 100 == pct exactly.
    nlv = 100_000.0 * (1 + pct / 100)
    return _account(net_liquidation_value=nlv, daily_start_equity=100_000.0)


def _account_with_weekly_drawdown_pct(pct: float):
    nlv = 100_000.0 * (1 + pct / 100)
    return _account(net_liquidation_value=nlv, weekly_start_equity=100_000.0)


def test_daily_drawdown_sign_convention_negative_when_losing():
    assert _account_with_daily_drawdown_pct(0.0).current_daily_drawdown_pct == 0.0
    assert round(_account_with_daily_drawdown_pct(-1.0).current_daily_drawdown_pct, 4) == -1.0
    assert round(_account_with_daily_drawdown_pct(-1.5).current_daily_drawdown_pct, 4) == -1.5


def test_daily_loss_limit_boundary_0pct_is_normal():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(0.0), _POLICY_1PCT_DAILY) is False


def test_daily_loss_limit_boundary_minus_half_pct_is_normal():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(-0.5), _POLICY_1PCT_DAILY) is False


def test_daily_loss_limit_boundary_minus_099pct_is_normal():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(-0.99), _POLICY_1PCT_DAILY) is False


def test_daily_loss_limit_boundary_exactly_minus_1pct_trips():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(-1.00), _POLICY_1PCT_DAILY) is True


def test_daily_loss_limit_boundary_minus_101pct_trips():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(-1.01), _POLICY_1PCT_DAILY) is True


def test_daily_loss_limit_boundary_minus_150pct_trips():
    # Exact reported acceptance-bug scenario: NLV 98500 vs start 100000, max daily loss 1.00%.
    account = _account(net_liquidation_value=98_500.0, daily_start_equity=100_000.0)
    assert round(account.current_daily_drawdown_pct, 4) == -1.5
    assert daily_loss_limit_hit(account, _POLICY_1PCT_DAILY) is True


def test_daily_loss_limit_never_trips_on_positive_pnl():
    assert daily_loss_limit_hit(_account_with_daily_drawdown_pct(1.0), _POLICY_1PCT_DAILY) is False


def test_weekly_drawdown_sign_convention_negative_when_losing():
    assert _account_with_weekly_drawdown_pct(0.0).current_weekly_drawdown_pct == 0.0
    assert round(_account_with_weekly_drawdown_pct(-3.0).current_weekly_drawdown_pct, 4) == -3.0


def test_weekly_loss_limit_boundary_0pct_is_normal():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(0.0), _POLICY_2_5PCT_WEEKLY) is False


def test_weekly_loss_limit_boundary_minus_1_25pct_is_normal():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(-1.25), _POLICY_2_5PCT_WEEKLY) is False


def test_weekly_loss_limit_boundary_minus_249pct_is_normal():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(-2.49), _POLICY_2_5PCT_WEEKLY) is False


def test_weekly_loss_limit_boundary_exactly_minus_2_5pct_trips():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(-2.50), _POLICY_2_5PCT_WEEKLY) is True


def test_weekly_loss_limit_boundary_minus_251pct_trips():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(-2.51), _POLICY_2_5PCT_WEEKLY) is True


def test_weekly_loss_limit_boundary_minus_3pct_trips():
    # §4 example: weekly_start_equity 100000, NLV 97000, max weekly loss 2.5%.
    account = _account(net_liquidation_value=97_000.0, weekly_start_equity=100_000.0)
    assert round(account.current_weekly_drawdown_pct, 4) == -3.0
    assert weekly_loss_limit_hit(account, _POLICY_2_5PCT_WEEKLY) is True


def test_weekly_loss_limit_never_trips_on_positive_pnl():
    assert weekly_loss_limit_hit(_account_with_weekly_drawdown_pct(1.0), _POLICY_2_5PCT_WEEKLY) is False


def test_portfolio_heat_exceeded():
    account = _account(net_liquidation_value=100_000.0, open_risk=3_500.0)  # 3.5% > default max 3.0%
    assert portfolio_heat_exceeded(account, DEFAULT_RISK_POLICY) is True


def test_max_positions_reached():
    account = _account(open_positions=5)  # policy default max_open_positions=5
    assert max_positions_reached(account, DEFAULT_RISK_POLICY) is True


def test_max_trades_reached():
    account = _account(trades_today=5)  # policy default max_trades_per_day=5
    assert max_trades_reached(account, DEFAULT_RISK_POLICY) is True
