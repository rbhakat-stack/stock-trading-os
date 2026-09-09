from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.formatting import format_drawdown_line
from engine.risk.limits import daily_loss_limit_hit
from engine.risk.policy import DEFAULT_RISK_POLICY


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def test_format_drawdown_line_shows_at_least_3_decimals():
    line = format_drawdown_line("DAILY DRAWDOWN", -0.999, 1.0)
    assert "-0.999%" in line


def test_minus_0_999_displays_distinctly_from_minus_1_000():
    # The reported bug: a 2-decimal display rounds both -0.999% and -1.000% to
    # "-1.00%", visually implying a breach even when the kill switch correctly
    # reports NORMAL for the former. At 3-decimal precision they must differ.
    line_a = format_drawdown_line("DAILY DRAWDOWN", -0.999, 1.0)
    line_b = format_drawdown_line("DAILY DRAWDOWN", -1.000, 1.0)
    assert line_a != line_b
    assert "-0.999%" in line_a
    assert "-1.000%" in line_b


def test_format_drawdown_line_includes_configured_threshold():
    line = format_drawdown_line("DAILY DRAWDOWN", -1.5, 1.0)
    assert "-1.000%" in line  # threshold shown as a negative limit, matching the drawdown sign convention


def test_format_drawdown_line_includes_remaining_buffer():
    # Still inside the limit -> positive buffer (room remaining before tripping).
    line = format_drawdown_line("DAILY DRAWDOWN", -0.999, 1.0)
    assert "buffer: 0.001%" in line

    # Already past the limit -> negative buffer (amount over the limit).
    line_breached = format_drawdown_line("DAILY DRAWDOWN", -1.5, 1.0)
    assert "buffer: -0.500%" in line_breached


# ===================== Exact-boundary regression (unchanged risk logic) =====================
# Reproduces the reported Case A / Case B exactly: start-of-day equity 100000,
# max daily loss 1.00%.


def test_case_a_nlv_99000_is_exactly_at_limit_and_trips():
    policy = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.00)
    account = _account(net_liquidation_value=99_000.0, daily_start_equity=100_000.0)
    assert round(account.current_daily_drawdown_pct, 3) == -1.000
    assert daily_loss_limit_hit(account, policy) is True


def test_case_b_nlv_99001_is_just_inside_limit_and_stays_normal():
    policy = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.00)
    account = _account(net_liquidation_value=99_001.0, daily_start_equity=100_000.0)
    assert round(account.current_daily_drawdown_pct, 3) == -0.999
    assert daily_loss_limit_hit(account, policy) is False


def test_case_a_and_case_b_display_lines_are_visually_distinct():
    policy = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.00)
    account_a = _account(net_liquidation_value=99_000.0, daily_start_equity=100_000.0)
    account_b = _account(net_liquidation_value=99_001.0, daily_start_equity=100_000.0)

    line_a = format_drawdown_line("DAILY DRAWDOWN", account_a.current_daily_drawdown_pct, policy.max_daily_loss_pct)
    line_b = format_drawdown_line("DAILY DRAWDOWN", account_b.current_daily_drawdown_pct, policy.max_daily_loss_pct)

    assert line_a != line_b
    assert "-1.000%" in line_a
    assert "-0.999%" in line_b
