from datetime import datetime

from engine.risk.account_state import AccountRiskState


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def test_daily_drawdown_negative_when_down():
    account = _account(net_liquidation_value=98_000.0, daily_start_equity=100_000.0)
    assert account.current_daily_drawdown_pct == -2.0


def test_daily_drawdown_zero_when_flat():
    account = _account(net_liquidation_value=100_000.0, daily_start_equity=100_000.0)
    assert account.current_daily_drawdown_pct == 0.0


def test_daily_drawdown_positive_when_up():
    account = _account(net_liquidation_value=101_000.0, daily_start_equity=100_000.0)
    assert account.current_daily_drawdown_pct == 1.0


def test_weekly_drawdown():
    account = _account(net_liquidation_value=95_000.0, weekly_start_equity=100_000.0)
    assert account.current_weekly_drawdown_pct == -5.0


def test_portfolio_heat_pct():
    account = _account(net_liquidation_value=100_000.0, open_risk=3_000.0)
    assert account.portfolio_heat_pct == 3.0


def test_portfolio_heat_zero_when_no_open_risk():
    account = _account(net_liquidation_value=100_000.0, open_risk=0.0)
    assert account.portfolio_heat_pct == 0.0


def test_drawdown_safe_with_zero_start_equity():
    # Defensive: must not divide by zero.
    account = _account(daily_start_equity=0.0, weekly_start_equity=0.0)
    assert account.current_daily_drawdown_pct == 0.0
    assert account.current_weekly_drawdown_pct == 0.0
