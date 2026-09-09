from dataclasses import replace

from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.sizing import compute_position_size


def test_basic_sizing_binds_on_risk_per_trade():
    # NLV=100,000, risk_per_trade=0.5% -> max_trade_loss=500. entry=100, stop=98 -> risk_per_share=2.
    # raw_size = 500/2 = 250 shares. Widen the other constraints (default max_symbol_exposure_pct=10%
    # alone would cap this at 100 shares) so risk_per_trade is genuinely the tightest one.
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0)
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=policy,
        buying_power=100_000.0,
    )
    assert result.shares == 250
    assert result.binding_constraint == "risk_per_trade"
    assert result.capital_at_risk == 500.0
    assert result.position_value == 25_000.0


def test_position_value_and_capital_at_risk_are_never_confused():
    result = compute_position_size(
        entry=50.0, stop=49.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=100_000.0,
    )
    assert result.position_value != result.capital_at_risk
    assert result.position_value == result.shares * 50.0
    assert result.capital_at_risk == result.shares * 1.0


def test_buying_power_constraint_binds_when_tighter():
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=1_000.0,  # only enough for 10 shares at $100
    )
    assert result.binding_constraint == "buying_power"
    assert result.shares == 10


def test_symbol_exposure_constraint_binds_when_tighter():
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=1.0)  # 1% of 100k = $1,000
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=policy,
        buying_power=1_000_000.0,
    )
    assert result.binding_constraint == "symbol_exposure"
    assert result.shares == 10  # $1,000 / $100


def test_portfolio_heat_constraint_binds_when_existing_open_risk_present():
    # heat budget = 3% of 100k = $3,000; already $2,900 of open risk used -> only $100 remaining
    # remaining/risk_per_share(2) = 50 shares
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=1_000_000.0, current_open_risk=2_900.0,
    )
    assert result.binding_constraint == "portfolio_heat"
    assert result.shares == 50


def test_zero_shares_when_no_heat_budget_remains():
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=1_000_000.0, current_open_risk=3_000.0,  # heat budget already fully used
    )
    assert result.shares == 0
    assert result.capital_at_risk == 0.0


def test_invalid_when_entry_equals_stop():
    result = compute_position_size(
        entry=100.0, stop=100.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=100_000.0,
    )
    assert result.shares == 0
    assert result.binding_constraint == "INVALID_INPUT"


def test_position_size_never_negative():
    result = compute_position_size(
        entry=100.0, stop=200.0,  # a nonsensical/inverted risk still just floors to a large risk_per_share
        net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY, buying_power=100_000.0,
    )
    assert result.shares >= 0


def test_max_shares_override_can_bind():
    result = compute_position_size(
        entry=100.0, stop=98.0, net_liquidation_value=100_000.0, risk_policy=DEFAULT_RISK_POLICY,
        buying_power=1_000_000.0, max_shares_override=5,
    )
    assert result.binding_constraint == "max_shares_override"
    assert result.shares == 5
