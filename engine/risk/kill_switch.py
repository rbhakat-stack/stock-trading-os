"""Deterministic kill-switch state machine (§16).

Phase 3 kill switches gate candidate QUALIFICATION only — they do not (and
cannot yet, with no broker integration) touch any existing position.
Fail-closed: any ambiguity about data quality escalates to FULL_LOCKOUT,
never NORMAL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from . import limits
from .account_state import AccountRiskState
from .policy import RiskPolicy


class KillSwitchState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    REDUCE_RISK = "REDUCE_RISK"
    NO_NEW_TRADES = "NO_NEW_TRADES"
    FULL_LOCKOUT = "FULL_LOCKOUT"


@dataclass(frozen=True)
class KillSwitchEvaluation:
    state: str
    triggers: list[str]
    evidence: dict = field(default_factory=dict)

    @property
    def blocks_new_trades(self) -> bool:
        return self.state in (KillSwitchState.NO_NEW_TRADES.value, KillSwitchState.FULL_LOCKOUT.value)


def evaluate_kill_switch(
    account: AccountRiskState,
    policy: RiskPolicy,
    data_quality_ok: bool,
    volatility_level: str | None = None,
    open_risk_complete: bool = True,
) -> KillSwitchEvaluation:
    """`open_risk_complete=False` means account.open_risk could not be
    reliably derived (e.g. one or more positions lack a usable stop) — this
    fails closed exactly like a market data-quality failure, blocking new
    trades, rather than silently trusting a possibly-understated open_risk.
    See engine/risk/positions.py::derive_open_risk and §8 of the final
    hardening audit."""
    triggers: list[str] = []

    if not data_quality_ok:
        triggers.append("DATA_QUALITY_FAILURE")
    if not open_risk_complete:
        triggers.append("OPEN_RISK_DATA_INCOMPLETE")

    if limits.daily_loss_limit_hit(account, policy):
        triggers.append("DAILY_LOSS_LIMIT_REACHED")
    if limits.weekly_loss_limit_hit(account, policy):
        triggers.append("WEEKLY_LOSS_LIMIT_REACHED")
    if limits.daily_realized_loss_limit_hit(account, policy):
        triggers.append("DAILY_REALIZED_LOSS_LIMIT_REACHED")
    if account.consecutive_losses >= policy.max_consecutive_losses:
        triggers.append("MAX_CONSECUTIVE_LOSSES")
    elif account.consecutive_losses >= policy.cooldown_after_losses > 0:
        triggers.append("COOLDOWN_AFTER_LOSSES")
    if open_risk_complete and limits.portfolio_heat_exceeded(account, policy):
        triggers.append("PORTFOLIO_HEAT_EXCEEDED")
    if volatility_level == "EXTREME":
        triggers.append("EXTREME_VOLATILITY")

    if "DATA_QUALITY_FAILURE" in triggers:
        state = KillSwitchState.FULL_LOCKOUT
    elif any(
        t in triggers
        for t in (
            "DAILY_LOSS_LIMIT_REACHED", "WEEKLY_LOSS_LIMIT_REACHED", "DAILY_REALIZED_LOSS_LIMIT_REACHED",
            "MAX_CONSECUTIVE_LOSSES", "OPEN_RISK_DATA_INCOMPLETE",
        )
    ):
        state = KillSwitchState.NO_NEW_TRADES
    elif "PORTFOLIO_HEAT_EXCEEDED" in triggers:
        state = KillSwitchState.REDUCE_RISK
    elif triggers:
        state = KillSwitchState.WARNING
    else:
        state = KillSwitchState.NORMAL

    return KillSwitchEvaluation(
        state=state.value,
        triggers=triggers,
        evidence={
            "daily_drawdown_pct": round(account.current_daily_drawdown_pct, 3),
            "weekly_drawdown_pct": round(account.current_weekly_drawdown_pct, 3),
            "realized_loss_pct": round(account.realized_loss_pct, 3),
            "portfolio_heat_pct": round(account.portfolio_heat_pct, 3) if open_risk_complete else None,
            "consecutive_losses": account.consecutive_losses,
            "open_risk_complete": open_risk_complete,
        },
    )
