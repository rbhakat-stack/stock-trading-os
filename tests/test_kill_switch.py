from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import KillSwitchState, evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def test_normal_state_with_no_triggers():
    account = _account()
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.NORMAL.value
    assert result.triggers == []
    assert result.blocks_new_trades is False


def test_data_quality_failure_forces_full_lockout_regardless_of_other_state():
    account = _account()  # otherwise perfectly healthy
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=False)
    assert result.state == KillSwitchState.FULL_LOCKOUT.value
    assert "DATA_QUALITY_FAILURE" in result.triggers
    assert result.blocks_new_trades is True


def test_daily_loss_limit_forces_no_new_trades():
    account = _account(net_liquidation_value=97_000.0, daily_start_equity=100_000.0)  # -3% > 2% default limit
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.NO_NEW_TRADES.value
    assert "DAILY_LOSS_LIMIT_REACHED" in result.triggers
    assert result.blocks_new_trades is True


def test_weekly_loss_limit_forces_no_new_trades():
    account = _account(net_liquidation_value=90_000.0, weekly_start_equity=100_000.0)  # -10% > 5% default limit
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.NO_NEW_TRADES.value
    assert "WEEKLY_LOSS_LIMIT_REACHED" in result.triggers


def test_max_consecutive_losses_forces_no_new_trades():
    account = _account(consecutive_losses=3)  # policy default max_consecutive_losses=3
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.NO_NEW_TRADES.value
    assert "MAX_CONSECUTIVE_LOSSES" in result.triggers


def test_cooldown_after_losses_is_only_a_warning_below_the_hard_max():
    account = _account(consecutive_losses=2)  # policy default cooldown_after_losses=2, max=3
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.WARNING.value
    assert "COOLDOWN_AFTER_LOSSES" in result.triggers
    assert result.blocks_new_trades is False


def test_portfolio_heat_exceeded_forces_reduce_risk_not_full_lockout():
    account = _account(open_risk=3_500.0)  # 3.5% > default max 3.0%
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert result.state == KillSwitchState.REDUCE_RISK.value
    assert "PORTFOLIO_HEAT_EXCEEDED" in result.triggers
    assert result.blocks_new_trades is False  # REDUCE_RISK doesn't block new trades outright


def test_extreme_volatility_is_a_warning():
    account = _account()
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True, volatility_level="EXTREME")
    assert result.state == KillSwitchState.WARNING.value
    assert "EXTREME_VOLATILITY" in result.triggers


def test_multiple_triggers_all_reported_not_just_one():
    account = _account(
        net_liquidation_value=97_000.0, daily_start_equity=100_000.0,  # daily limit hit
        open_risk=3_500.0,  # heat exceeded
    )
    result = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    assert "DAILY_LOSS_LIMIT_REACHED" in result.triggers
    assert "PORTFOLIO_HEAT_EXCEEDED" in result.triggers
    assert len(result.triggers) >= 2


def test_reported_acceptance_bug_scenario_now_trips_no_new_trades():
    # Exact reproduction: start-of-day equity 100000, NLV 98500 (-1.50% drawdown),
    # policy max_daily_loss_pct=1.00%. Previously reported as incorrectly showing
    # KILL SWITCH STATE: NORMAL.
    policy = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.00)
    account = _account(net_liquidation_value=98_500.0, daily_start_equity=100_000.0, realized_pnl_today=-1500.0)
    assert round(account.current_daily_drawdown_pct, 4) == -1.5
    result = evaluate_kill_switch(account, policy, data_quality_ok=True)
    assert result.state == KillSwitchState.NO_NEW_TRADES.value
    assert "DAILY_LOSS_LIMIT_REACHED" in result.triggers
    assert result.blocks_new_trades is True
