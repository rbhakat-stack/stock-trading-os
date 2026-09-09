"""Daily/weekly loss limit checks (§15)."""
from __future__ import annotations

from dataclasses import dataclass

from .account_state import AccountRiskState
from .policy import RiskPolicy
from .positions import AccountPosition


def daily_loss_limit_hit(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.current_daily_drawdown_pct <= -policy.max_daily_loss_pct


def weekly_loss_limit_hit(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.current_weekly_drawdown_pct <= -policy.max_weekly_loss_pct


def daily_realized_loss_limit_hit(account: AccountRiskState, policy: RiskPolicy) -> bool:
    """A SEPARATE control from daily_loss_limit_hit (equity drawdown) — see
    RiskPolicy.max_daily_realized_loss_pct's docstring. Deliberately does NOT
    let unrealized gains suppress this: it only ever looks at realized P&L."""
    return account.realized_loss_pct <= -policy.max_daily_realized_loss_pct


def portfolio_heat_exceeded(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.portfolio_heat_pct >= policy.max_portfolio_heat_pct


def max_positions_reached(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.open_positions >= policy.max_open_positions


def max_trades_reached(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.trades_today >= policy.max_trades_per_day


def gross_exposure_exceeded(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.gross_exposure_pct >= policy.max_gross_exposure_pct


def net_exposure_exceeded(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.net_exposure_pct >= policy.max_net_exposure_pct


def leverage_exceeded(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.current_leverage >= policy.max_leverage


def consecutive_loss_lockout(account: AccountRiskState, policy: RiskPolicy) -> bool:
    return account.consecutive_losses >= policy.max_consecutive_losses


def symbol_exposure_pct(position: AccountPosition, net_liquidation_value: float) -> float:
    """abs notional in ONE symbol / NLV * 100 — the same definition
    engine/risk/sizing.py's `symbol_exposure` binding constraint uses, and
    the same notional (`position.notional_abs`) engine/risk/positions.py's
    derive_exposure() already sums into gross/net exposure, so this can
    never disagree with either. Returns 0.0 when NLV <= 0 (fail-closed —
    same defensive pattern as AccountRiskState.gross_exposure_pct/
    net_exposure_pct; never a fabricated NLV=1 fallback)."""
    if net_liquidation_value <= 0:
        return 0.0
    return position.notional_abs / net_liquidation_value * 100


@dataclass(frozen=True)
class PolicyBreach:
    """One already-breached risk-policy limit, for DISPLAY/explainability
    only — see evaluate_current_policy_breaches below."""
    code: str
    message: str
    # Optional structured identification for a breach tied to a specific
    # symbol (currently only SINGLE_SYMBOL_EXPOSURE_EXCEEDED) — None for
    # every account-wide breach type. Lets a caller identify/filter a
    # specific breached symbol without parsing `message`.
    symbol: str | None = None
    current_value: float | None = None
    limit_value: float | None = None


def evaluate_current_policy_breaches(
    account: AccountRiskState,
    policy: RiskPolicy,
    open_risk_complete: bool = True,
    positions: list[AccountPosition] | None = None,
) -> list[PolicyBreach]:
    """Pure, display-only evaluation of which risk-policy limits the CURRENT
    account state already breaches, independent of any trade candidate.

    This exists purely to surface an already-breached limit to the user even
    when nothing was just analyzed as TRIGGERED (e.g. a FORMING/WAIT setup
    never reaches engine.trade.decision's max_positions_reached check at
    all, since it short-circuits to WAIT first — see the Phase 3
    policy-visibility bug report). It is NOT part of enforcement: it reuses
    the exact same boolean checks (above, and mirrored from kill_switch.py)
    that the decision engine and kill switch already use, so it can never
    disagree with them or duplicate their logic — the decision engine and
    kill switch remain the sole enforcement authority. Callers must not use
    this list to gate anything; it is for messages only.

    `open_risk_complete=False` suppresses the portfolio-heat check, mirroring
    evaluate_kill_switch's own fail-closed handling — heat can't be reliably
    judged as breached or not when open risk itself is unknown.

    `positions`, when given, is the SAME resolved/effective positions list
    already used to derive `account`'s gross/net exposure (see
    engine/risk/positions.py::derive_exposure and
    app/pages/trade_planner.py's _resolved_positions) — never a separately
    fetched or recomputed list. It adds per-symbol standing exposure
    breaches: a symbol whose CURRENT notional already exceeds
    policy.max_single_symbol_exposure_pct, independent of any trade
    candidate (see the Phase 3 per-symbol standing-breach-visibility
    report). Candidate-level sizing already enforces this limit correctly
    (engine/risk/sizing.py's `symbol_exposure` binding constraint) — this is
    purely an additional, non-enforcing display surface, same as every
    other check in this function. Omitted (None/empty) simply skips this
    check, exactly as before this parameter existed.
    """
    breaches: list[PolicyBreach] = []

    if max_positions_reached(account, policy):
        if account.open_positions > policy.max_open_positions:
            breaches.append(PolicyBreach(
                "MAX_POSITIONS_REACHED",
                f"OPEN POSITION LIMIT EXCEEDED — current: {account.open_positions} / "
                f"limit: {policy.max_open_positions}. New risk-increasing positions are blocked.",
            ))
        else:
            breaches.append(PolicyBreach(
                "MAX_POSITIONS_REACHED",
                f"OPEN POSITION LIMIT REACHED — current: {account.open_positions} / "
                f"limit: {policy.max_open_positions}. No additional risk-increasing position allowed.",
            ))

    if max_trades_reached(account, policy):
        breaches.append(PolicyBreach(
            "MAX_TRADES_REACHED",
            f"MAX TRADES PER DAY REACHED — current: {account.trades_today} / "
            f"limit: {policy.max_trades_per_day}. New trades are blocked for the rest of today.",
        ))

    if open_risk_complete and portfolio_heat_exceeded(account, policy):
        breaches.append(PolicyBreach(
            "PORTFOLIO_HEAT_EXCEEDED",
            f"PORTFOLIO HEAT LIMIT EXCEEDED — current: {account.portfolio_heat_pct:.3f}% / "
            f"limit: {policy.max_portfolio_heat_pct:.3f}%.",
        ))

    if gross_exposure_exceeded(account, policy):
        breaches.append(PolicyBreach(
            "GROSS_EXPOSURE_EXCEEDED",
            f"GROSS EXPOSURE LIMIT EXCEEDED — current: {account.gross_exposure_pct:.3f}% / "
            f"limit: {policy.max_gross_exposure_pct:.3f}%.",
        ))

    if net_exposure_exceeded(account, policy):
        breaches.append(PolicyBreach(
            "NET_EXPOSURE_EXCEEDED",
            f"NET EXPOSURE LIMIT EXCEEDED — current: {account.net_exposure_pct:.3f}% / "
            f"limit: {policy.max_net_exposure_pct:.3f}%.",
        ))

    if leverage_exceeded(account, policy):
        breaches.append(PolicyBreach(
            "LEVERAGE_LIMIT_EXCEEDED",
            f"LEVERAGE LIMIT EXCEEDED — current: {account.current_leverage:.3f}x / "
            f"limit: {policy.max_leverage:.3f}x.",
        ))

    # Per-symbol standing exposure — a symbol whose notional ALREADY exceeds
    # the single-symbol limit, independent of any trade candidate. Strict
    # `>` (not `>=`): exactly-at-limit is OK, never flagged as EXCEEDED (see
    # the Phase 3 per-symbol standing-breach report §6's boundary semantics
    # — deliberately different from the `>=` used above for the other
    # checks, per that report's explicit instruction).
    for _position in positions or []:
        _pct = symbol_exposure_pct(_position, account.net_liquidation_value)
        if _pct > policy.max_symbol_exposure_pct:
            breaches.append(PolicyBreach(
                "SINGLE_SYMBOL_EXPOSURE_EXCEEDED",
                f"SINGLE-SYMBOL EXPOSURE LIMIT EXCEEDED — {_position.symbol}: {_pct:.3f}% / "
                f"limit: {policy.max_symbol_exposure_pct:.3f}%.",
                symbol=_position.symbol, current_value=round(_pct, 3), limit_value=policy.max_symbol_exposure_pct,
            ))

    if daily_loss_limit_hit(account, policy):
        breaches.append(PolicyBreach(
            "DAILY_LOSS_LIMIT_REACHED",
            f"DAILY LOSS LIMIT REACHED — daily drawdown {account.current_daily_drawdown_pct:.3f}% "
            f"exceeds the configured maximum of {policy.max_daily_loss_pct:.3f}%.",
        ))

    if daily_realized_loss_limit_hit(account, policy):
        breaches.append(PolicyBreach(
            "DAILY_REALIZED_LOSS_LIMIT_REACHED",
            f"DAILY REALIZED LOSS LIMIT REACHED — realized loss today {account.realized_loss_pct:.3f}% "
            f"exceeds the configured maximum of {policy.max_daily_realized_loss_pct:.3f}%.",
        ))

    if weekly_loss_limit_hit(account, policy):
        breaches.append(PolicyBreach(
            "WEEKLY_LOSS_LIMIT_REACHED",
            f"WEEKLY LOSS LIMIT REACHED — weekly drawdown {account.current_weekly_drawdown_pct:.3f}% "
            f"exceeds the configured maximum of {policy.max_weekly_loss_pct:.3f}%.",
        ))

    if consecutive_loss_lockout(account, policy):
        breaches.append(PolicyBreach(
            "MAX_CONSECUTIVE_LOSSES_REACHED",
            f"CONSECUTIVE LOSS LOCKOUT — {account.consecutive_losses} consecutive losses reached the "
            f"lockout threshold of {policy.max_consecutive_losses}. New trades are blocked.",
        ))

    return breaches
