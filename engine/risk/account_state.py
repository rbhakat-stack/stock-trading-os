"""Account risk state (§13).

Until broker integration exists, every field here is manual/simulated —
`source` must always be shown alongside any figure derived from this object
so the UI never implies live broker connectivity that doesn't exist yet.

EXPOSURE MODEL (added in the exposure/leverage hardening round): only the two
raw notional totals are stored — `long_exposure_notional` and
`short_exposure_notional` — both always >= 0 (a magnitude, not a signed
figure). Gross exposure, net exposure, and leverage are all pure derivations
of those two numbers (see the properties below) rather than separately
stored fields, so they can never drift out of sync with each other. This
mirrors the existing pattern already used for drawdown/heat: computed
properties, not duplicated stored state.

Definitions (§5 of the exposure/leverage hardening spec):
  - gross exposure %  = (long + short) / NLV * 100   -- total notional deployed
  - net exposure %    = abs(long - short) / NLV * 100 -- absolute directional bias
  - leverage          = (long + short) / NLV          -- gross notional as a
                         multiple of NLV. Numerically related to gross exposure
                         (leverage = gross_exposure_pct / 100) but kept as a
                         DISTINCT policy concept from max_gross_exposure_pct,
                         per the hardening spec — a ratio ceiling, not a
                         percentage-of-NLV ceiling, even though they measure
                         the same underlying quantity.

In manual/simulated mode (source=MANUAL/PAPER) these two fields are
user-entered in Account State, same as every other figure here. Once broker
integration exists (source=BROKER, a future phase — not built here), these
would instead be computed by summing each open position's notional value by
side directly from the broker's live position list; this dataclass's shape
does not need to change for that, only how the two fields get populated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AccountSource(str, Enum):
    MANUAL = "MANUAL"
    PAPER = "PAPER"
    BROKER = "BROKER"


@dataclass(frozen=True)
class AccountRiskState:
    source: str  # an AccountSource value
    net_liquidation_value: float
    cash: float
    buying_power: float
    realized_pnl_today: float
    unrealized_pnl: float
    daily_start_equity: float
    weekly_start_equity: float
    open_risk: float  # sum of $ planned-loss-to-stop across open positions
    open_positions: int
    trades_today: int
    consecutive_losses: int
    as_of: datetime
    # Defaulted (not every existing fixture/caller needs to set these) —
    # 0.0/0.0 represents a flat account with no existing exposure.
    long_exposure_notional: float = 0.0  # sum of $ notional value of open LONG positions (>= 0)
    short_exposure_notional: float = 0.0  # sum of $ notional value of open SHORT positions (>= 0)

    @property
    def current_daily_drawdown_pct(self) -> float:
        """Negative when the account is down for the day; 0 if flat/up."""
        if self.daily_start_equity <= 0:
            return 0.0
        return (self.net_liquidation_value - self.daily_start_equity) / self.daily_start_equity * 100

    @property
    def current_weekly_drawdown_pct(self) -> float:
        if self.weekly_start_equity <= 0:
            return 0.0
        return (self.net_liquidation_value - self.weekly_start_equity) / self.weekly_start_equity * 100

    @property
    def portfolio_heat_pct(self) -> float:
        if self.net_liquidation_value <= 0:
            return 0.0
        return (self.open_risk / self.net_liquidation_value) * 100

    @property
    def gross_exposure_notional(self) -> float:
        """Total notional deployed, both directions summed: long + short."""
        return self.long_exposure_notional + self.short_exposure_notional

    @property
    def net_exposure_notional(self) -> float:
        """Signed directional bias: positive = net long, negative = net short, 0 = flat/hedged."""
        return self.long_exposure_notional - self.short_exposure_notional

    @property
    def gross_exposure_pct(self) -> float:
        if self.net_liquidation_value <= 0:
            return 0.0
        return (self.gross_exposure_notional / self.net_liquidation_value) * 100

    @property
    def net_exposure_pct(self) -> float:
        """Always >= 0 — an absolute directional-bias magnitude, not signed
        (see net_exposure_notional for the signed figure)."""
        if self.net_liquidation_value <= 0:
            return 0.0
        return (abs(self.net_exposure_notional) / self.net_liquidation_value) * 100

    @property
    def current_leverage(self) -> float:
        """Gross notional as a multiple of NLV (e.g. 1.5 = 1.5x). Distinct
        from gross_exposure_pct only in units (ratio vs percentage) — see the
        module docstring for why both are kept as separate policy concepts."""
        if self.net_liquidation_value <= 0:
            return 0.0
        return self.gross_exposure_notional / self.net_liquidation_value

    @property
    def realized_loss_pct(self) -> float:
        """Negative when realized_pnl_today is a loss, 0 if flat/positive —
        same sign convention as current_daily_drawdown_pct, but a DIFFERENT
        quantity: this is realized trading P&L only, independent of
        unrealized gains/losses that can offset it in the equity figure.
        See engine/risk/limits.py::daily_realized_loss_limit_hit."""
        if self.daily_start_equity <= 0:
            return 0.0
        return (self.realized_pnl_today / self.daily_start_equity) * 100


_NUMERIC_FIELDS = (
    "net_liquidation_value", "cash", "buying_power", "realized_pnl_today", "unrealized_pnl",
    "daily_start_equity", "weekly_start_equity", "open_risk", "long_exposure_notional", "short_exposure_notional",
)


def validate_account_state(account: AccountRiskState) -> list[str]:
    """Returns a list of human-readable validation errors — empty if valid.
    Pure validation only; never mutates or "fixes" a malformed account state.
    Mirrors engine/risk/policy.py::validate_risk_policy — both are called
    at Analyze time (not just at Save time) so a malformed, edited-but-
    unsaved value can never reach position-sizing math. See
    NoTradeReason.INVALID_ACCOUNT_STATE."""
    errors: list[str] = []

    for field_name in _NUMERIC_FIELDS:
        value = getattr(account, field_name)
        if not math.isfinite(value):
            errors.append(f"{field_name} must be a finite number (got {value!r}).")

    if math.isfinite(account.net_liquidation_value) and account.net_liquidation_value <= 0:
        errors.append("net_liquidation_value must be greater than 0.")
    if math.isfinite(account.cash) and account.cash < 0:
        errors.append("cash cannot be negative.")
    if math.isfinite(account.buying_power) and account.buying_power < 0:
        errors.append("buying_power cannot be negative.")
    if math.isfinite(account.open_risk) and account.open_risk < 0:
        errors.append("open_risk cannot be negative.")
    if math.isfinite(account.long_exposure_notional) and account.long_exposure_notional < 0:
        errors.append("long_exposure_notional cannot be negative.")
    if math.isfinite(account.short_exposure_notional) and account.short_exposure_notional < 0:
        errors.append("short_exposure_notional cannot be negative.")
    if math.isfinite(account.daily_start_equity) and account.daily_start_equity <= 0:
        errors.append("daily_start_equity must be greater than 0.")
    if math.isfinite(account.weekly_start_equity) and account.weekly_start_equity <= 0:
        errors.append("weekly_start_equity must be greater than 0.")

    if account.open_positions < 0:
        errors.append("open_positions cannot be negative.")
    if account.trades_today < 0:
        errors.append("trades_today cannot be negative.")
    if account.consecutive_losses < 0:
        errors.append("consecutive_losses cannot be negative.")

    if account.source not in (AccountSource.MANUAL.value, AccountSource.PAPER.value, AccountSource.BROKER.value):
        errors.append(f"source must be one of MANUAL/PAPER/BROKER (got {account.source!r}).")

    return errors
