"""High-level Trade Planner orchestrator (§30).

Ties the candidate engine, invalidation, targets, quality score, risk sizing,
and decision engine into one TradePlanningSnapshot. Consumes an
already-built MarketIntelligenceSnapshot (Phase 2) — never re-derives
market-structure logic. This is the ONLY module that composes the others.

Statistical validation, event risk, and liquidity are hard-coded to their
"not available" states — no backtesting (Phase 5) or calendar/depth
integration exists yet, and this module must never fabricate them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import KillSwitchEvaluation, evaluate_kill_switch
from engine.risk.policy import RiskPolicy
from engine.trade.candidate import TradeCandidate, detect_candidates
from engine.trade.decision import TradeDecisionResult, make_trade_decision
from engine.trade.invalidation import InvalidationResult, compute_long_invalidation, compute_short_invalidation
from engine.trade.quality import QualityScoreResult, compute_quality_score
from engine.trade.targets import TargetResult, compute_targets

STATISTICAL_VALIDATION_STATUS = "NOT_YET_AVAILABLE"
EVENT_RISK_STATUS = "NOT_AVAILABLE"
LIQUIDITY_STATUS = "LIMITED (volume-based context only — no bid/ask/depth data)"

_BOS_STRENGTH_CONFIDENCE = {"STRONG": 1.0, "MODERATE": 0.6, "WEAK": 0.2}
_DEFAULT_SETUP_CONFIDENCE = 0.6
_MAX_FUTURE_TOLERANCE_SECONDS = 300  # allows for reasonable clock skew, not a real "future" snapshot


def _is_future_timestamped(as_of: datetime | None, now: datetime | None = None) -> bool:
    """§21 of the final hardening audit: a market snapshot timestamped in the
    future is nonsensical and must fail closed, folded into data_quality_ok
    below (the existing DATA_QUALITY_FAILURE -> FULL_LOCKOUT path)."""
    if as_of is None:
        return False
    now = now or datetime.now(timezone.utc)
    ts = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
    return (ts - now).total_seconds() > _MAX_FUTURE_TOLERANCE_SECONDS


@dataclass(frozen=True)
class TradePlanningSnapshot:
    symbol: str
    timeframe: str
    as_of: datetime | None
    data_source: str

    candidate: TradeCandidate | None
    invalidation: InvalidationResult | None
    targets: list[TargetResult]
    quality: QualityScoreResult | None

    risk_policy: RiskPolicy
    account: AccountRiskState
    kill_switch: KillSwitchEvaluation

    decision: TradeDecisionResult

    entry_price: float | None
    current_price: float

    statistical_validation: str = STATISTICAL_VALIDATION_STATUS
    event_risk: str = EVENT_RISK_STATUS
    liquidity: str = LIQUIDITY_STATUS

    evidence: dict = field(default_factory=dict)


def build_trade_plan(
    market_snapshot,
    current_price: float,
    risk_policy: RiskPolicy,
    account: AccountRiskState,
    existing_symbol_notional_signed: float = 0.0,
    existing_symbol_shares_signed: float = 0.0,  # display-only signed share count — see PositionSizeResult
    open_risk_complete: bool = True,
) -> TradePlanningSnapshot:
    # Gross/net exposure are derived directly from `account`
    # (account.gross_exposure_notional / account.net_exposure_notional).
    # Per-symbol signed exposure (existing_symbol_notional_signed) still has
    # to be supplied by the caller — it requires knowing what candidate/symbol
    # is even being evaluated, which `account` alone doesn't carry; see
    # app/pages/trade_planner.py, which now looks this up from
    # account_positions for the symbol being analyzed before calling this.
    #
    # `open_risk_complete=False` means account.open_risk could not be
    # reliably derived from positions (one or more lack a usable stop) —
    # propagated straight to the kill switch, which fails closed rather than
    # trusting a possibly-understated figure. See engine/risk/positions.py.
    existing_gross_exposure_value = account.gross_exposure_notional
    existing_net_exposure_value = account.net_exposure_notional
    data_quality_ok = market_snapshot.data_quality_ok and not _is_future_timestamped(market_snapshot.as_of)

    kill_switch = evaluate_kill_switch(
        account, risk_policy, data_quality_ok=data_quality_ok, open_risk_complete=open_risk_complete,
        volatility_level=market_snapshot.volatility.level if market_snapshot.volatility else None,
    )

    candidates = detect_candidates(market_snapshot, current_price) if data_quality_ok else []
    # Prefer a TRIGGERED candidate over a merely FORMING one; first-found among ties.
    triggered = [c for c in candidates if c.status == "TRIGGERED"]
    forming = [c for c in candidates if c.status == "FORMING"]
    candidate = triggered[0] if triggered else (forming[0] if forming else None)

    invalidation: InvalidationResult | None = None
    targets: list[TargetResult] = []
    quality: QualityScoreResult | None = None
    entry_price: float | None = None

    if candidate is not None and candidate.status == "TRIGGERED":
        atr_value = market_snapshot.atr_value or 0.0
        if candidate.direction == "LONG":
            invalidation = compute_long_invalidation(candidate.structural_level, atr_value)
        else:
            invalidation = compute_short_invalidation(candidate.structural_level, atr_value)

        entry_price = candidate.entry_zone_high if candidate.direction == "LONG" else candidate.entry_zone_low
        if entry_price is None:
            entry_price = current_price

        targets = compute_targets(
            candidate.direction, entry_price, invalidation.stop_price,
            resistance_zones=market_snapshot.resistance_zones, support_zones=market_snapshot.support_zones,
        )

        setup_confidence = _DEFAULT_SETUP_CONFIDENCE
        if market_snapshot.latest_bos is not None:
            setup_confidence = _BOS_STRENGTH_CONFIDENCE.get(market_snapshot.latest_bos.strength.value, _DEFAULT_SETUP_CONFIDENCE)

        quality = compute_quality_score(
            trend_quality=market_snapshot.trend_quality.quality if market_snapshot.trend_quality else None,
            multi_timeframe_level=market_snapshot.multi_timeframe_alignment.level if market_snapshot.multi_timeframe_alignment else None,
            volume_level=market_snapshot.volume_level,
            volatility_level=market_snapshot.volatility.level if market_snapshot.volatility else None,
            rr1=targets[0].r_multiple if targets else None,
            min_rr=risk_policy.min_rr,
            setup_confidence=setup_confidence,
        )

    decision = make_trade_decision(
        data_quality_ok=data_quality_ok, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=risk_policy, account=account, kill_switch=kill_switch, quality=quality,
        entry_price=entry_price, existing_symbol_notional_signed=existing_symbol_notional_signed,
        existing_symbol_shares_signed=existing_symbol_shares_signed,
        existing_gross_exposure_value=existing_gross_exposure_value,
        existing_net_exposure_value=existing_net_exposure_value,
    )

    return TradePlanningSnapshot(
        symbol=market_snapshot.symbol, timeframe=market_snapshot.timeframe, as_of=market_snapshot.as_of,
        data_source=market_snapshot.data_source, candidate=candidate, invalidation=invalidation, targets=targets,
        quality=quality, risk_policy=risk_policy, account=account, kill_switch=kill_switch, decision=decision,
        entry_price=entry_price, current_price=current_price,
        evidence={"candidate_count": len(candidates)},
    )
