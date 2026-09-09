"""User-configurable risk policy (§11).

All risk math elsewhere derives its dollar figures from
`current_net_liquidation_value x these percentages` — never a hard-coded
account size. `DEFAULT_RISK_POLICY` is a documented example starting point,
not a universally-correct setting.

Platform ceilings (`PLATFORM_MAX_*`) are hard-coded constants for Phase 3 —
there is no admin UI yet to configure them per-platform; that's a known,
flagged simplification, not a claim that these are the "correct" limits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PLATFORM_MAX_RISK_PER_TRADE_PCT = 5.0
PLATFORM_MAX_PORTFOLIO_HEAT_PCT = 20.0
PLATFORM_MAX_DAILY_LOSS_PCT = 20.0
PLATFORM_MAX_WEEKLY_LOSS_PCT = 40.0
PLATFORM_MAX_DAILY_REALIZED_LOSS_PCT = 20.0
PLATFORM_MAX_LEVERAGE = 4.0
PLATFORM_MAX_OPEN_POSITIONS = 50
PLATFORM_MAX_TRADES_PER_DAY = 50


@dataclass(frozen=True)
class RiskPolicy:
    risk_per_trade_pct: float
    max_daily_loss_pct: float  # DAILY EQUITY DRAWDOWN limit: (NLV - daily_start_equity) / daily_start_equity
    max_weekly_loss_pct: float
    max_portfolio_heat_pct: float
    max_symbol_exposure_pct: float
    max_gross_exposure_pct: float
    max_net_exposure_pct: float
    max_open_positions: int
    max_trades_per_day: int
    min_rr: float
    max_leverage: float
    cooldown_after_losses: int
    max_consecutive_losses: int
    # DAILY REALIZED-LOSS limit: realized_pnl_today / daily_start_equity — a
    # DIFFERENT quantity from max_daily_loss_pct's equity drawdown (§9 of the
    # final hardening audit). Equity drawdown can read 0% while the account
    # has already locked in a real trading loss offset by unrealized gains;
    # this control catches that case independently. See
    # engine/risk/limits.py::daily_realized_loss_limit_hit.
    max_daily_realized_loss_pct: float


# A conservative example starting point — NOT a universally "correct" setting.
# Every field is user-configurable within the platform ceilings above.
DEFAULT_RISK_POLICY = RiskPolicy(
    risk_per_trade_pct=0.5,
    max_daily_loss_pct=2.0,
    max_weekly_loss_pct=5.0,
    max_portfolio_heat_pct=3.0,
    max_symbol_exposure_pct=10.0,
    max_gross_exposure_pct=100.0,
    max_net_exposure_pct=100.0,
    max_open_positions=5,
    max_trades_per_day=5,
    min_rr=1.5,
    max_leverage=1.0,
    cooldown_after_losses=2,
    max_consecutive_losses=3,
    max_daily_realized_loss_pct=2.0,
)


_NUMERIC_POLICY_FIELDS = (
    "risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_portfolio_heat_pct",
    "max_symbol_exposure_pct", "max_gross_exposure_pct", "max_net_exposure_pct", "min_rr", "max_leverage",
    "max_daily_realized_loss_pct",
)


def validate_risk_policy(policy: RiskPolicy) -> list[str]:
    """Returns a list of human-readable validation errors — empty if valid.
    Pure validation only; never mutates or "fixes" an invalid policy."""
    errors: list[str] = []

    # NaN/inf must be caught explicitly and FIRST — `nan <= 0` and `nan > X`
    # are both always False in Python, so every `<= 0` / `> PLATFORM_MAX_*`
    # check below would silently let a non-finite field straight through
    # without this (a real gap the final-hardening fuzz test found: a NaN
    # risk_per_trade_pct previously passed validation, then propagated into
    # position-sizing arithmetic and was misreported as RISK_BUDGET_EXCEEDED
    # instead of INVALID_RISK_POLICY).
    for field_name in _NUMERIC_POLICY_FIELDS:
        if not math.isfinite(getattr(policy, field_name)):
            errors.append(f"{field_name} must be a finite number (got {getattr(policy, field_name)!r}).")
    if errors:
        return errors

    if policy.risk_per_trade_pct <= 0:
        errors.append("risk_per_trade_pct must be greater than 0.")
    elif policy.risk_per_trade_pct > PLATFORM_MAX_RISK_PER_TRADE_PCT:
        errors.append(f"risk_per_trade_pct cannot exceed the platform maximum of {PLATFORM_MAX_RISK_PER_TRADE_PCT}%.")

    if policy.max_daily_loss_pct <= 0:
        errors.append("max_daily_loss_pct must be greater than 0.")
    elif policy.max_daily_loss_pct > PLATFORM_MAX_DAILY_LOSS_PCT:
        errors.append(f"max_daily_loss_pct cannot exceed the platform maximum of {PLATFORM_MAX_DAILY_LOSS_PCT}%.")

    if policy.max_weekly_loss_pct <= 0:
        errors.append("max_weekly_loss_pct must be greater than 0.")
    elif policy.max_weekly_loss_pct > PLATFORM_MAX_WEEKLY_LOSS_PCT:
        errors.append(f"max_weekly_loss_pct cannot exceed the platform maximum of {PLATFORM_MAX_WEEKLY_LOSS_PCT}%.")

    if policy.max_portfolio_heat_pct <= 0:
        errors.append("max_portfolio_heat_pct must be greater than 0.")
    elif policy.max_portfolio_heat_pct > PLATFORM_MAX_PORTFOLIO_HEAT_PCT:
        errors.append(f"max_portfolio_heat_pct cannot exceed the platform maximum of {PLATFORM_MAX_PORTFOLIO_HEAT_PCT}%.")
    elif policy.max_portfolio_heat_pct < policy.risk_per_trade_pct:
        errors.append("max_portfolio_heat_pct must be at least risk_per_trade_pct (a single trade must fit within the heat budget).")

    if policy.max_symbol_exposure_pct <= 0:
        errors.append("max_symbol_exposure_pct must be greater than 0.")
    if policy.max_gross_exposure_pct <= 0:
        errors.append("max_gross_exposure_pct must be greater than 0.")
    if policy.max_net_exposure_pct <= 0:
        errors.append("max_net_exposure_pct must be greater than 0.")

    if policy.max_open_positions <= 0:
        errors.append("max_open_positions must be greater than 0.")
    elif policy.max_open_positions > PLATFORM_MAX_OPEN_POSITIONS:
        errors.append(f"max_open_positions cannot exceed the platform maximum of {PLATFORM_MAX_OPEN_POSITIONS}.")

    if policy.max_trades_per_day <= 0:
        errors.append("max_trades_per_day must be greater than 0.")
    elif policy.max_trades_per_day > PLATFORM_MAX_TRADES_PER_DAY:
        errors.append(f"max_trades_per_day cannot exceed the platform maximum of {PLATFORM_MAX_TRADES_PER_DAY}.")

    if policy.min_rr <= 0:
        errors.append("min_rr must be greater than 0.")

    if policy.max_leverage <= 0:
        errors.append("max_leverage must be greater than 0.")
    elif policy.max_leverage > PLATFORM_MAX_LEVERAGE:
        errors.append(f"max_leverage cannot exceed the platform maximum of {PLATFORM_MAX_LEVERAGE}x.")

    if policy.cooldown_after_losses < 0:
        errors.append("cooldown_after_losses cannot be negative.")
    if policy.max_consecutive_losses <= 0:
        errors.append("max_consecutive_losses must be greater than 0.")
    elif policy.cooldown_after_losses > policy.max_consecutive_losses:
        errors.append("cooldown_after_losses cannot exceed max_consecutive_losses.")

    if policy.max_daily_realized_loss_pct <= 0:
        errors.append("max_daily_realized_loss_pct must be greater than 0.")
    elif policy.max_daily_realized_loss_pct > PLATFORM_MAX_DAILY_REALIZED_LOSS_PCT:
        errors.append(f"max_daily_realized_loss_pct cannot exceed the platform maximum of {PLATFORM_MAX_DAILY_REALIZED_LOSS_PCT}%.")

    # ===== Cross-field validation (§16 of the final hardening audit) =====
    # (risk_per_trade_pct vs max_portfolio_heat_pct was already checked above.)
    if policy.max_symbol_exposure_pct > policy.max_gross_exposure_pct:
        errors.append("max_symbol_exposure_pct cannot exceed max_gross_exposure_pct (one symbol cannot need more room than the whole account).")
    if policy.max_net_exposure_pct > policy.max_gross_exposure_pct:
        errors.append("max_net_exposure_pct cannot exceed max_gross_exposure_pct (net exposure is always <= gross exposure by definition).")

    return errors
