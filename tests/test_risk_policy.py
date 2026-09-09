from dataclasses import replace

from engine.risk.policy import DEFAULT_RISK_POLICY, validate_risk_policy


def test_default_policy_is_valid():
    assert validate_risk_policy(DEFAULT_RISK_POLICY) == []


def test_zero_risk_per_trade_rejected():
    policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=0)
    errors = validate_risk_policy(policy)
    assert any("risk_per_trade_pct" in e for e in errors)


def test_risk_per_trade_above_platform_ceiling_rejected():
    policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=10.0)
    errors = validate_risk_policy(policy)
    assert any("platform maximum" in e for e in errors)


def test_negative_min_rr_rejected():
    policy = replace(DEFAULT_RISK_POLICY, min_rr=-1.0)
    errors = validate_risk_policy(policy)
    assert any("min_rr" in e for e in errors)


def test_portfolio_heat_below_risk_per_trade_rejected():
    # A single trade must be able to fit within the heat budget.
    policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=2.0, max_portfolio_heat_pct=1.0)
    errors = validate_risk_policy(policy)
    assert any("max_portfolio_heat_pct" in e for e in errors)


def test_cooldown_exceeding_max_consecutive_losses_rejected():
    policy = replace(DEFAULT_RISK_POLICY, cooldown_after_losses=5, max_consecutive_losses=3)
    errors = validate_risk_policy(policy)
    assert any("cooldown_after_losses" in e for e in errors)


def test_max_leverage_above_platform_ceiling_rejected():
    policy = replace(DEFAULT_RISK_POLICY, max_leverage=100.0)
    errors = validate_risk_policy(policy)
    assert any("max_leverage" in e for e in errors)
