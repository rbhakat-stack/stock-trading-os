"""Shared, pure helpers used by every evaluator in engine/playbooks/evaluators/.

Nothing here re-derives Phase 2/3 logic — every helper is a thin, honest
wrapper around an existing engine.trade.* or engine.risk.* pure function, so
a playbook evaluator can never numerically disagree with what
engine/trade/planner.py would have computed for the same candidate.
"""
from __future__ import annotations

import re

from engine.trade.candidate import TradeCandidate
from engine.trade.invalidation import InvalidationResult, compute_long_invalidation, compute_short_invalidation
from engine.trade.quality import QualityScoreResult, compute_quality_score
from engine.trade.targets import TargetResult, compute_targets

from .definition import PlaybookDefinition
from .evaluation import PlaybookEvaluation, QualityBreakdown, RankingMetadata
from .evidence import EvidenceItem, SoftConcern
from .taxonomy import EligibilityStatus, SetupStatus

# Mirrors engine.trade.planner.build_trade_plan's _BOS_STRENGTH_CONFIDENCE /
# _DEFAULT_SETUP_CONFIDENCE EXACTLY (found to have drifted during the Phase 4
# acceptance audit: this module previously used a flat 0.6 default regardless
# of snapshot.latest_bos, so a playbook's displayed quality_score could
# numerically disagree with plan.quality.score — the authoritative score
# shown lower on the same Trade Plan page — whenever a real BOS with
# STRONG/WEAK strength was present. Deriving it the same way planner.py does
# closes that gap; this reads an EXISTING Phase 2 signal, never invents one.
_BOS_STRENGTH_CONFIDENCE = {"STRONG": 1.0, "MODERATE": 0.6, "WEAK": 0.2}
_DEFAULT_SETUP_CONFIDENCE = 0.6


def _derive_setup_confidence(snapshot) -> float:
    if snapshot.latest_bos is not None:
        return _BOS_STRENGTH_CONFIDENCE.get(snapshot.latest_bos.strength.value, _DEFAULT_SETUP_CONFIDENCE)
    return _DEFAULT_SETUP_CONFIDENCE

# Mirrors engine.trade.quality._WEIGHTS exactly (*100, as point maxima) — see
# QualityComponentConfig's docstring: this is a RELABELING for
# explainability, never a second scoring formula that could drift from the
# score Phase 3's decision engine actually uses.
QUALITY_COMPONENT_LABELS = (
    ("structure", "Structure"),
    ("multi_timeframe", "Multi-timeframe"),
    ("volume", "Volume"),
    ("volatility", "Volatility"),
    ("setup_confidence", "Trigger"),
    ("reward_risk", "Target quality"),
)
QUALITY_COMPONENT_MAX_POINTS = {"structure": 20, "multi_timeframe": 15, "volume": 15, "volatility": 15, "setup_confidence": 15, "reward_risk": 20}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")[:60]


def soft_concerns_from_reasons(reasons_against: list[str]) -> tuple[SoftConcern, ...]:
    """Wraps engine.trade.setups' existing reasons_against strings as
    SoftConcern evidence items — never invents a new concern the matcher
    didn't already surface."""
    return tuple(SoftConcern(code=_slug(r), description=r, severity=0.5, evidence_value=r) for r in reasons_against)


def quality_breakdown_from_score(quality: QualityScoreResult) -> QualityBreakdown:
    rows = []
    total_earned = 0
    for key, label in QUALITY_COMPONENT_LABELS:
        max_points = QUALITY_COMPONENT_MAX_POINTS[key]
        fraction = quality.components.get(key, 0.0)
        earned = round(fraction * max_points)
        total_earned += earned
        rows.append((key, label, earned, max_points))
    return QualityBreakdown(components=tuple(rows), total_score=quality.score, total_max=100, band=quality.band)


def compute_triggered_geometry_and_quality(
    candidate: TradeCandidate,
    atr_value: float,
    current_price: float,
    resistance_zones,
    support_zones,
    min_rr: float,
    trend_quality: str | None,
    multi_timeframe_level: str | None,
    volume_level: str | None,
    volatility_level: str | None,
    setup_confidence: float = 0.6,
) -> tuple[InvalidationResult, list[TargetResult], QualityScoreResult, float]:
    """The EXACT per-candidate computation engine/trade/planner.py::build_trade_plan
    performs for its selected TRIGGERED candidate (invalidation -> entry ->
    targets -> quality) — factored out here so EVERY triggered playbook
    evaluation gets it, not just whichever one planner.py happened to pick.
    Returns (invalidation, targets, quality, entry_price)."""
    if candidate.direction == "LONG":
        invalidation = compute_long_invalidation(candidate.structural_level, atr_value)
    else:
        invalidation = compute_short_invalidation(candidate.structural_level, atr_value)

    entry_price = candidate.entry_zone_high if candidate.direction == "LONG" else candidate.entry_zone_low
    if entry_price is None:
        entry_price = current_price

    targets = compute_targets(
        candidate.direction, entry_price, invalidation.stop_price,
        resistance_zones=resistance_zones, support_zones=support_zones,
    )
    rr1 = targets[0].r_multiple if targets else None
    quality = compute_quality_score(
        trend_quality=trend_quality, multi_timeframe_level=multi_timeframe_level, volume_level=volume_level,
        volatility_level=volatility_level, rr1=rr1, min_rr=min_rr, setup_confidence=setup_confidence,
    )
    return invalidation, targets, quality, entry_price


def ranking_metadata(symbol: str, timeframe: str, playbook_id: str, playbook_version: str, family: str,
                      direction: str, setup_status: str, quality_score: int | None, quality_band: str | None,
                      hard_disqualifier_count: int, soft_concern_count: int, entry_price: float | None,
                      stop_price: float | None, target1: float | None, rr1: float | None,
                      data_quality_status: str = "OK") -> RankingMetadata:
    return RankingMetadata(
        symbol=symbol, timeframe=timeframe, playbook_id=playbook_id, playbook_version=playbook_version,
        family=family, direction=direction, setup_status=setup_status, quality_score=quality_score,
        quality_band=quality_band, hard_disqualifier_count=hard_disqualifier_count,
        soft_concern_count=soft_concern_count, entry_price=entry_price, stop_price=stop_price,
        target1=target1, rr1=rr1, data_quality_status=data_quality_status,
    )


def not_eligible_evaluation(
    definition: PlaybookDefinition, snapshot, prerequisites_satisfied: tuple[EvidenceItem, ...],
    prerequisites_missing: tuple[EvidenceItem, ...],
) -> PlaybookEvaluation:
    """A playbook whose basic regime/direction gate doesn't apply at all —
    mirrors engine.trade.candidate.detect_candidates' existing behavior of
    returning None from a matcher in this exact situation (§7: prerequisites
    that must exist before a setup is even considered)."""
    return PlaybookEvaluation(
        playbook_id=definition.playbook_id, playbook_version=definition.version, name=definition.name,
        direction=definition.direction, family=definition.family,
        eligibility_status=EligibilityStatus.NOT_ELIGIBLE_REGIME.value, setup_status=SetupStatus.NOT_ELIGIBLE.value,
        prerequisites_satisfied=prerequisites_satisfied, prerequisites_missing=prerequisites_missing,
        manual_review_notes=definition.manual_review_notes,
        statistical_validation_status=definition.statistical_validation_status,
        ranking=ranking_metadata(
            symbol=snapshot.symbol, timeframe=snapshot.timeframe, playbook_id=definition.playbook_id,
            playbook_version=definition.version, family=definition.family, direction=definition.direction,
            setup_status=SetupStatus.NOT_ELIGIBLE.value, quality_score=None, quality_band=None,
            hard_disqualifier_count=0, soft_concern_count=0, entry_price=None, stop_price=None, target1=None,
            rr1=None, data_quality_status="OK" if snapshot.data_quality_ok else "DATA_QUALITY_FAILURE",
        ),
    )


def wrap_candidate_into_evaluation(
    definition: PlaybookDefinition, snapshot, current_price: float, candidate: TradeCandidate,
    prerequisites_satisfied: tuple[EvidenceItem, ...], min_rr: float, setup_confidence: float | None = None,
) -> PlaybookEvaluation:
    """Wraps an already-matched TradeCandidate (FORMING or TRIGGERED) into a
    full PlaybookEvaluation. Identical logic for every existing-six
    playbook — the ONLY thing that differs between them is which matcher
    produced `candidate` and which prerequisite evidence was computed
    upstream. For a FORMING candidate, invalidation/targets/quality stay
    None — exactly matching engine/trade/planner.py's own "only compute
    these for a TRIGGERED candidate" rule (see the Phase 3 WAIT/FORMING
    persistence semantics — never fabricate what wasn't genuinely computed).
    """
    is_triggered = candidate.status == "TRIGGERED"
    trigger_evidence = EvidenceItem(
        code="TRIGGER", label="Setup trigger condition", observed_value=candidate.status,
        expected_value="TRIGGERED", passed=is_triggered, source="candidate",
        explanation=(candidate.conditions_to_wait_for[0] if candidate.conditions_to_wait_for else ""),
    )
    trigger_satisfied = (trigger_evidence,) if is_triggered else ()
    trigger_missing = () if is_triggered else (trigger_evidence,)

    soft_concerns = soft_concerns_from_reasons(list(candidate.reasons_against))

    invalidation = targets = quality = None
    entry_price = candidate.entry_zone_high if candidate.direction == "LONG" else candidate.entry_zone_low
    quality_breakdown = None
    if is_triggered:
        atr_value = snapshot.atr_value if snapshot.atr_value and snapshot.atr_value > 0 else 0.0
        trend_quality = snapshot.trend_quality.quality if snapshot.trend_quality else None
        mtf_level = snapshot.multi_timeframe_alignment.level if snapshot.multi_timeframe_alignment else None
        resolved_confidence = setup_confidence if setup_confidence is not None else _derive_setup_confidence(snapshot)
        invalidation, targets, quality, entry_price = compute_triggered_geometry_and_quality(
            candidate=candidate, atr_value=atr_value, current_price=current_price,
            resistance_zones=snapshot.resistance_zones, support_zones=snapshot.support_zones, min_rr=min_rr,
            trend_quality=trend_quality, multi_timeframe_level=mtf_level, volume_level=snapshot.volume_level,
            volatility_level=(snapshot.volatility.level if snapshot.volatility else None),
            setup_confidence=resolved_confidence,
        )
        quality_breakdown = quality_breakdown_from_score(quality)

    return PlaybookEvaluation(
        playbook_id=definition.playbook_id, playbook_version=definition.version, name=definition.name,
        direction=definition.direction, family=definition.family,
        eligibility_status=EligibilityStatus.ELIGIBLE.value, setup_status=candidate.status,
        prerequisites_satisfied=prerequisites_satisfied, prerequisites_missing=(),
        trigger_status=is_triggered, trigger_conditions_satisfied=trigger_satisfied,
        trigger_conditions_missing=trigger_missing,
        reasons_for=tuple(candidate.reasons_for), soft_concerns=soft_concerns,
        quality_score=quality.score if quality else None, quality_band=quality.band if quality else None,
        quality_breakdown=quality_breakdown,
        candidate=candidate, invalidation=invalidation, targets=tuple(targets) if targets else (),
        entry_price=entry_price, entry_zone_low=candidate.entry_zone_low, entry_zone_high=candidate.entry_zone_high,
        entry_type=definition.entry_type,
        entry_explanation=f"{definition.entry_type}: " + "; ".join(definition.entry_rules[:1]),
        manual_review_notes=definition.manual_review_notes,
        statistical_validation_status=definition.statistical_validation_status,
        ranking=ranking_metadata(
            symbol=snapshot.symbol, timeframe=snapshot.timeframe, playbook_id=definition.playbook_id,
            playbook_version=definition.version, family=definition.family, direction=definition.direction,
            setup_status=candidate.status, quality_score=quality.score if quality else None,
            quality_band=quality.band if quality else None, hard_disqualifier_count=0,
            soft_concern_count=len(soft_concerns), entry_price=entry_price,
            stop_price=(invalidation.stop_price if invalidation else None),
            target1=(targets[0].price if targets else None), rr1=(targets[0].r_multiple if targets else None),
            data_quality_status="OK" if snapshot.data_quality_ok else "DATA_QUALITY_FAILURE",
        ),
        evidence=dict(candidate.evidence),
    )
