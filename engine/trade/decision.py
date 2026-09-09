"""Decision engine (§18/§31) — the exact 14-step order of operations:

  1. Data quality valid?
  2/3. Recognized setup?
  4/5. Appropriate regime / good location -- handled by the setup matcher
       itself (engine.trade.setups), which never returns a candidate at all
       if the regime/location gate fails.
  (setup FORMING, not yet TRIGGERED -> WAIT here)
  6/7. Clear invalidation / logical stop?
  8. Logical target?
  9. R:R acceptable?
  10/11. Risk policy / portfolio heat permits? (via position sizing)
  12. Daily/weekly limits / kill switch permits?
  13. Candidate quality acceptable?
  14. REJECT / WAIT / CONDITIONAL / QUALIFIED

Never starts with position sizing (it's step 10, after target/RR are already
confirmed acceptable). Decision labels are REJECT/WAIT/CONDITIONAL/QUALIFIED
only — never BUY/SELL. QUALIFIED never submits an order; that enforcement
lives in the UI layer ("MANUAL REVIEW REQUIRED" on every plan), not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.risk import limits
from engine.risk.account_state import AccountRiskState, validate_account_state
from engine.risk.kill_switch import KillSwitchEvaluation
from engine.risk.policy import RiskPolicy, validate_risk_policy
from engine.risk.positions import RISK_INCREASING, RISK_REDUCING, classify_trade_risk
from engine.risk.sizing import PositionSizeResult, compute_position_size
from engine.trade.candidate import TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult

DECISION_REJECT = "REJECT"
DECISION_WAIT = "WAIT"
DECISION_CONDITIONAL = "CONDITIONAL"
DECISION_QUALIFIED = "QUALIFIED"

# Maps a PositionSizeResult.binding_constraint that floored to 0 shares to its
# dedicated NoTradeReason. Exhaustive over every value compute_position_size
# can produce — no fallback to an unrelated reason (final hardening audit §26).
_REASON_BY_BINDING_CONSTRAINT = {
    "INVALID_INPUT": NoTradeReason.NO_STRUCTURAL_STOP.value,
    "INVALID_NUMERIC_INPUT": NoTradeReason.INVALID_NUMERIC_INPUT.value,
    "risk_per_trade": NoTradeReason.RISK_BUDGET_EXCEEDED.value,
    "buying_power": NoTradeReason.INSUFFICIENT_BUYING_POWER.value,
    "symbol_exposure": NoTradeReason.SYMBOL_EXPOSURE_EXCEEDED.value,
    "gross_exposure": NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value,  # no dedicated gross-only reason; see final hardening report
    "net_exposure": NoTradeReason.NET_EXPOSURE_EXCEEDED.value,
    "portfolio_heat": NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value,
    "leverage": NoTradeReason.LEVERAGE_LIMIT_EXCEEDED.value,
}

# Maps every kill_switch.blocks_new_trades trigger to its dedicated NoTradeReason.
# Exhaustive — see test_decision_reason_mapping_audit for a test that fails
# loudly if a new trigger is ever added to kill_switch.py without a matching
# entry here.
_REASON_BY_KILL_SWITCH_TRIGGER = {
    "DAILY_LOSS_LIMIT_REACHED": NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value,
    "WEEKLY_LOSS_LIMIT_REACHED": NoTradeReason.WEEKLY_LOSS_LIMIT_REACHED.value,
    "DAILY_REALIZED_LOSS_LIMIT_REACHED": NoTradeReason.DAILY_REALIZED_LOSS_LIMIT_REACHED.value,
    "DATA_QUALITY_FAILURE": NoTradeReason.DATA_QUALITY_FAILURE.value,
    "MAX_CONSECUTIVE_LOSSES": NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED.value,
    "OPEN_RISK_DATA_INCOMPLETE": NoTradeReason.OPEN_RISK_DATA_INCOMPLETE.value,
}


@dataclass(frozen=True)
class TradeDecisionResult:
    decision: str
    no_trade_reasons: list[str] = field(default_factory=list)
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    conditions_to_wait_for: list[str] = field(default_factory=list)
    position_size: PositionSizeResult | None = None
    quality: QualityScoreResult | None = None
    evidence: dict = field(default_factory=dict)


def make_trade_decision(
    data_quality_ok: bool,
    candidate: TradeCandidate | None,
    invalidation: InvalidationResult | None,
    targets: list[TargetResult],
    risk_policy: RiskPolicy,
    account: AccountRiskState,
    kill_switch: KillSwitchEvaluation,
    quality: QualityScoreResult | None,
    entry_price: float | None,
    existing_symbol_notional_signed: float = 0.0,  # + = existing LONG in this symbol, - = existing SHORT, 0 = flat
    existing_symbol_shares_signed: float = 0.0,  # + = existing LONG shares, - = existing SHORT shares, 0 = flat (display only — see PositionSizeResult)
    existing_gross_exposure_value: float = 0.0,
    existing_net_exposure_value: float = 0.0,  # signed: positive = net long, negative = net short
) -> TradeDecisionResult:
    reasons_for = list(candidate.reasons_for) if candidate else []
    reasons_against = list(candidate.reasons_against) if candidate else []
    conditions = list(candidate.conditions_to_wait_for) if candidate else []

    # 0. Valid risk policy / account state? Checked before EVERYTHING else,
    # including data quality — a malformed policy or account state (negative
    # field, NaN, NLV<=0, ...) must never reach any downstream arithmetic.
    # Analyze-time validation, not just Save-time (see the final hardening
    # audit §E: this was a fail-open gap — an edited-but-unsaved policy field
    # was never re-validated before being used to size a trade).
    policy_errors = validate_risk_policy(risk_policy)
    if policy_errors:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.INVALID_RISK_POLICY.value],
            evidence={"validation_errors": policy_errors},
        )
    account_errors = validate_account_state(account)
    if account_errors:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.INVALID_ACCOUNT_STATE.value],
            evidence={"validation_errors": account_errors},
        )

    # 1. Data quality valid?
    if not data_quality_ok:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.DATA_QUALITY_FAILURE.value],
        )

    # 2/3. Recognized setup? (4/5 regime/location already enforced by the setup matcher)
    if candidate is None:
        return TradeDecisionResult(decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.NO_VALID_SETUP.value])

    if candidate.status == "FORMING":
        return TradeDecisionResult(
            decision=DECISION_WAIT, reasons_for=reasons_for, reasons_against=reasons_against,
            conditions_to_wait_for=conditions,
        )

    # candidate.status == "TRIGGERED" from here.
    no_trade_reasons: list[str] = []

    # 6/7. Clear invalidation / logical stop?
    if invalidation is None:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.NO_STRUCTURAL_STOP.value],
            reasons_for=reasons_for, reasons_against=reasons_against,
        )

    # A stop on the wrong side of (or equal to) entry is not a logical stop at
    # all, regardless of direction — risk_per_share = abs(entry - stop) would
    # still compute a positive magnitude and silently mask this, sizing a
    # position against a nonsensical invalidation level. Caught here, before
    # RR/sizing ever run, so it can never reach QUALIFIED.
    if entry_price is not None:
        if candidate.direction == "LONG" and invalidation.stop_price >= entry_price:
            return TradeDecisionResult(
                decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.NO_STRUCTURAL_STOP.value],
                reasons_for=reasons_for, reasons_against=reasons_against,
            )
        if candidate.direction == "SHORT" and invalidation.stop_price <= entry_price:
            return TradeDecisionResult(
                decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.NO_STRUCTURAL_STOP.value],
                reasons_for=reasons_for, reasons_against=reasons_against,
            )

    # 8. Logical target?
    if not targets:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.NO_VALID_TARGET.value],
            reasons_for=reasons_for, reasons_against=reasons_against,
        )

    # 9. R:R acceptable?
    rr1 = targets[0].r_multiple
    if rr1 < risk_policy.min_rr:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.RR_BELOW_MINIMUM.value],
            reasons_for=reasons_for, reasons_against=reasons_against,
            evidence={"rr1": rr1, "min_rr": risk_policy.min_rr},
        )

    # 12. Daily/weekly limits / kill switch permits? (checked before sizing —
    # sizing is moot if new trades are blocked outright)
    if kill_switch.blocks_new_trades:
        for trig in kill_switch.triggers:
            reason = _REASON_BY_KILL_SWITCH_TRIGGER.get(trig)
            if reason is not None:
                no_trade_reasons.append(reason)
        if not no_trade_reasons:
            # Only reachable if blocks_new_trades is True for a trigger not in
            # the exhaustive map above — see test_decision_reason_mapping_audit,
            # which fails loudly the moment this happens rather than silently
            # falling back to an unrelated reason.
            no_trade_reasons.append(NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value)
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=no_trade_reasons,
            reasons_for=reasons_for, reasons_against=reasons_against,
        )

    # A trade that opposes an existing position in THIS symbol is capable of
    # reducing it — per §20/§27, max_open_positions/max_trades_per_day (both
    # operational, not exposure, gates) must not block a genuinely
    # risk-reducing trade. This is a pre-sizing, direction-only signal (0 vs
    # opposing sign) — the actual share count is still fully bounded by every
    # exposure constraint in compute_position_size below regardless.
    trade_risk_classification = (
        RISK_REDUCING
        if (existing_symbol_notional_signed > 0 and candidate.direction == "SHORT")
        or (existing_symbol_notional_signed < 0 and candidate.direction == "LONG")
        else RISK_INCREASING
    )

    if trade_risk_classification != RISK_REDUCING:
        if limits.max_positions_reached(account, risk_policy):
            no_trade_reasons.append(NoTradeReason.MAX_POSITIONS_REACHED.value)
        if limits.max_trades_reached(account, risk_policy):
            no_trade_reasons.append(NoTradeReason.MAX_TRADES_REACHED.value)
    if no_trade_reasons:
        return TradeDecisionResult(
            decision=DECISION_REJECT, no_trade_reasons=no_trade_reasons,
            reasons_for=reasons_for, reasons_against=reasons_against,
        )

    # 10/11. Risk policy / portfolio heat permits? (via position sizing)
    position_size = None
    if entry_price is not None and entry_price > 0:
        position_size = compute_position_size(
            entry=entry_price, stop=invalidation.stop_price, net_liquidation_value=account.net_liquidation_value,
            risk_policy=risk_policy, buying_power=account.buying_power, direction=candidate.direction,
            existing_symbol_notional_signed=existing_symbol_notional_signed,
            existing_symbol_shares_signed=existing_symbol_shares_signed,
            existing_gross_exposure_value=existing_gross_exposure_value,
            existing_net_exposure_value=existing_net_exposure_value, current_open_risk=account.open_risk,
        )
        if position_size.shares <= 0:
            reason = _REASON_BY_BINDING_CONSTRAINT.get(
                position_size.binding_constraint, NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value
            )
            return TradeDecisionResult(
                decision=DECISION_REJECT, no_trade_reasons=[reason],
                reasons_for=reasons_for, reasons_against=reasons_against, position_size=position_size,
            )
        # Refine the classification now that we know the actual share count —
        # a proposed size that overshoots past flat into new opposite-side
        # exposure is correctly RISK_INCREASING, not reducing (see
        # engine/risk/positions.py::classify_trade_risk).
        trade_risk_classification = classify_trade_risk(existing_symbol_notional_signed, candidate.direction, position_size.shares)

    # 13. Candidate quality acceptable? -> QUALIFIED vs CONDITIONAL
    soft_conditions = list(reasons_against)
    if quality and quality.band == "LOW":
        soft_conditions.append("trade quality score is LOW")

    decision = DECISION_CONDITIONAL if soft_conditions else DECISION_QUALIFIED

    # Hard invariant: a daily/weekly loss-limit lockout must never coexist with
    # QUALIFIED, regardless of setup quality — risk lockout always overrides
    # market setup. Structurally unreachable today (kill_switch.blocks_new_trades
    # already forces REJECT above), but asserted explicitly so a future refactor
    # of the ordering above can never silently reintroduce this class of bug.
    _hard_lockout_triggers = (
        "DAILY_LOSS_LIMIT_REACHED", "WEEKLY_LOSS_LIMIT_REACHED", "DAILY_REALIZED_LOSS_LIMIT_REACHED",
        "MAX_CONSECUTIVE_LOSSES", "OPEN_RISK_DATA_INCOMPLETE",
    )
    if decision == DECISION_QUALIFIED and any(t in kill_switch.triggers for t in _hard_lockout_triggers):
        raise AssertionError(
            "invariant violated: QUALIFIED must never be returned while a hard-lockout kill-switch trigger is active"
        )

    return TradeDecisionResult(
        decision=decision,
        no_trade_reasons=[],
        reasons_for=reasons_for,
        reasons_against=reasons_against,
        conditions_to_wait_for=soft_conditions if decision == DECISION_CONDITIONAL else [],
        position_size=position_size,
        quality=quality,
        evidence={"rr1": rr1, "min_rr": risk_policy.min_rr, "trade_risk_classification": trade_risk_classification},
    )
