"""RANGE_MEAN_REVERSION_LONG / _SHORT — a NEW Phase 4 playbook (no Phase 3
equivalent existed). Built entirely on engine.market_state.consolidation's
existing ConsolidationEvaluation (state=CONSOLIDATION, range_high/range_low/
range_midpoint) — never re-derives range detection. Target/stop are anchored
to the SAME consolidation range boundaries the engine already computed from
real price action (never a fabricated measured-move projection), mirroring
Phase 3's "never fabricate a target" principle (see engine/trade/targets.py).
"""
from __future__ import annotations

from engine.trade.candidate import CandidateStatus, EntryMethod, TradeCandidate
from engine.trade.invalidation import compute_long_invalidation, compute_short_invalidation
from engine.trade.quality import compute_quality_score
from engine.trade.targets import TargetResult

from .._shared import quality_breakdown_from_score, ranking_metadata, soft_concerns_from_reasons
from ..evaluation import PlaybookEvaluation
from ..evidence import EvidenceItem
from ..registry import get_definition
from ..taxonomy import EligibilityStatus, PlaybookId

TOUCH_PROXIMITY_ATR = 0.35


def _not_eligible(definition, snapshot, satisfied, missing):
    from .._shared import not_eligible_evaluation
    return not_eligible_evaluation(definition, snapshot, satisfied, missing)


def evaluate_range_mean_reversion_long(snapshot, current_price: float, min_rr: float):
    definition = get_definition(PlaybookId.RANGE_MEAN_REVERSION_LONG.value)
    cons = snapshot.consolidation
    range_ok = cons is not None and cons.state == "CONSOLIDATION" and cons.range_low is not None and cons.range_high is not None
    range_item = EvidenceItem(
        code="ACTIVE_RANGE", label="A recent consolidation range is active",
        observed_value=(cons.state if cons else "NONE"), expected_value="CONSOLIDATION", passed=range_ok,
        source="consolidation",
    )
    if not range_ok:
        return _not_eligible(definition, snapshot, (), (range_item,))

    atr_value = snapshot.atr_value if snapshot.atr_value and snapshot.atr_value > 0 else 0.0
    near_low = atr_value > 0 and (current_price - cons.range_low) <= TOUCH_PROXIMITY_ATR * atr_value and current_price >= cons.range_low - (0.1 * atr_value)
    near_item = EvidenceItem(
        code="NEAR_RANGE_LOW", label="Price is near the range low",
        observed_value=f"price={current_price} range_low={cons.range_low}",
        expected_value=f"within {TOUCH_PROXIMITY_ATR} ATR of range low", passed=near_low, source="consolidation",
    )
    satisfied = (range_item,) + ((near_item,) if near_low else ())
    missing = () if near_low else (near_item,)

    reasons_for = [f"a recent consolidation range is active (range_low={cons.range_low}, range_high={cons.range_high})"]
    conditions = []
    if near_low:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price is near the range low, favoring a mean-reversion long")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price pulls back to the range low near {cons.range_low}")

    candidate = TradeCandidate(
        setup_type=PlaybookId.RANGE_MEAN_REVERSION_LONG.value, direction="LONG", status=status,
        structural_level=cons.range_low, entry_zone_low=cons.range_low, entry_zone_high=cons.range_midpoint,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=[],
        conditions_to_wait_for=conditions,
        evidence={"range_high": cons.range_high, "range_low": cons.range_low, "range_midpoint": cons.range_midpoint},
    )

    if status != CandidateStatus.TRIGGERED.value:
        # FORMING — mirror wrap_candidate_into_evaluation's own "no
        # invalidation/targets/quality for a non-triggered candidate" rule,
        # but this playbook's target isn't zone-based so it can't reuse
        # wrap_candidate_into_evaluation directly (see the module docstring).
        from .._shared import wrap_candidate_into_evaluation
        return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)

    invalidation = compute_long_invalidation(cons.range_low, atr_value)
    entry_price = cons.range_low
    target_price = cons.range_midpoint if cons.range_midpoint > entry_price else cons.range_high
    risk_per_unit = abs(entry_price - invalidation.stop_price)
    targets: list[TargetResult] = []
    if risk_per_unit > 0 and target_price > entry_price:
        reward = target_price - entry_price
        targets.append(TargetResult(
            price=round(target_price, 4), reason="consolidation range midpoint/high", distance=round(reward, 4),
            reward_per_unit=round(reward, 4), r_multiple=round(reward / risk_per_unit, 3),
            evidence={"range_high": cons.range_high, "range_low": cons.range_low},
        ))
        if cons.range_high > target_price:
            reward2 = cons.range_high - entry_price
            targets.append(TargetResult(
                price=round(cons.range_high, 4), reason="consolidation range high", distance=round(reward2, 4),
                reward_per_unit=round(reward2, 4), r_multiple=round(reward2 / risk_per_unit, 3), evidence={},
            ))

    trend_quality = snapshot.trend_quality.quality if snapshot.trend_quality else None
    mtf_level = snapshot.multi_timeframe_alignment.level if snapshot.multi_timeframe_alignment else None
    rr1 = targets[0].r_multiple if targets else None
    quality = compute_quality_score(
        trend_quality=trend_quality, multi_timeframe_level=mtf_level, volume_level=snapshot.volume_level,
        volatility_level=(snapshot.volatility.level if snapshot.volatility else None), rr1=rr1, min_rr=min_rr,
        setup_confidence=0.5,  # mean reversion is inherently counter-trend within the range; conservative default
    )
    soft_concerns = soft_concerns_from_reasons([])

    return PlaybookEvaluation(
        playbook_id=definition.playbook_id, playbook_version=definition.version, name=definition.name,
        direction=definition.direction, family=definition.family,
        eligibility_status=EligibilityStatus.ELIGIBLE.value, setup_status=status,
        prerequisites_satisfied=satisfied, prerequisites_missing=(),
        trigger_status=True,
        trigger_conditions_satisfied=(EvidenceItem(code="TRIGGER", label="Price at range low", observed_value=str(current_price), expected_value=f"<= {cons.range_low + TOUCH_PROXIMITY_ATR * atr_value:.4f}", passed=True, source="consolidation"),),
        reasons_for=tuple(reasons_for), soft_concerns=soft_concerns,
        quality_score=quality.score, quality_band=quality.band, quality_breakdown=quality_breakdown_from_score(quality),
        candidate=candidate, invalidation=invalidation, targets=tuple(targets),
        entry_price=entry_price, entry_zone_low=candidate.entry_zone_low, entry_zone_high=candidate.entry_zone_high,
        entry_type=definition.entry_type, entry_explanation=f"{definition.entry_type}: enter at the range low, targeting a reversion toward the range midpoint/high.",
        manual_review_notes=definition.manual_review_notes, statistical_validation_status=definition.statistical_validation_status,
        ranking=ranking_metadata(
            symbol=snapshot.symbol, timeframe=snapshot.timeframe, playbook_id=definition.playbook_id,
            playbook_version=definition.version, family=definition.family, direction=definition.direction,
            setup_status=status, quality_score=quality.score, quality_band=quality.band, hard_disqualifier_count=0,
            soft_concern_count=len(soft_concerns), entry_price=entry_price, stop_price=invalidation.stop_price,
            target1=(targets[0].price if targets else None), rr1=rr1,
            data_quality_status="OK" if snapshot.data_quality_ok else "DATA_QUALITY_FAILURE",
        ),
        evidence=dict(candidate.evidence),
    )


def evaluate_range_mean_reversion_short(snapshot, current_price: float, min_rr: float):
    definition = get_definition(PlaybookId.RANGE_MEAN_REVERSION_SHORT.value)
    cons = snapshot.consolidation
    range_ok = cons is not None and cons.state == "CONSOLIDATION" and cons.range_low is not None and cons.range_high is not None
    range_item = EvidenceItem(
        code="ACTIVE_RANGE", label="A recent consolidation range is active",
        observed_value=(cons.state if cons else "NONE"), expected_value="CONSOLIDATION", passed=range_ok,
        source="consolidation",
    )
    if not range_ok:
        return _not_eligible(definition, snapshot, (), (range_item,))

    atr_value = snapshot.atr_value if snapshot.atr_value and snapshot.atr_value > 0 else 0.0
    near_high = atr_value > 0 and (cons.range_high - current_price) <= TOUCH_PROXIMITY_ATR * atr_value and current_price <= cons.range_high + (0.1 * atr_value)
    near_item = EvidenceItem(
        code="NEAR_RANGE_HIGH", label="Price is near the range high",
        observed_value=f"price={current_price} range_high={cons.range_high}",
        expected_value=f"within {TOUCH_PROXIMITY_ATR} ATR of range high", passed=near_high, source="consolidation",
    )
    satisfied = (range_item,) + ((near_item,) if near_high else ())
    missing = () if near_high else (near_item,)

    reasons_for = [f"a recent consolidation range is active (range_low={cons.range_low}, range_high={cons.range_high})"]
    conditions = []
    if near_high:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price is near the range high, favoring a mean-reversion short")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price rallies to the range high near {cons.range_high}")

    candidate = TradeCandidate(
        setup_type=PlaybookId.RANGE_MEAN_REVERSION_SHORT.value, direction="SHORT", status=status,
        structural_level=cons.range_high, entry_zone_low=cons.range_midpoint, entry_zone_high=cons.range_high,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=[],
        conditions_to_wait_for=conditions,
        evidence={"range_high": cons.range_high, "range_low": cons.range_low, "range_midpoint": cons.range_midpoint},
    )

    if status != CandidateStatus.TRIGGERED.value:
        from .._shared import wrap_candidate_into_evaluation
        return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)

    invalidation = compute_short_invalidation(cons.range_high, atr_value)
    entry_price = cons.range_high
    target_price = cons.range_midpoint if cons.range_midpoint < entry_price else cons.range_low
    risk_per_unit = abs(invalidation.stop_price - entry_price)
    targets: list[TargetResult] = []
    if risk_per_unit > 0 and target_price < entry_price:
        reward = entry_price - target_price
        targets.append(TargetResult(
            price=round(target_price, 4), reason="consolidation range midpoint/low", distance=round(reward, 4),
            reward_per_unit=round(reward, 4), r_multiple=round(reward / risk_per_unit, 3),
            evidence={"range_high": cons.range_high, "range_low": cons.range_low},
        ))
        if cons.range_low < target_price:
            reward2 = entry_price - cons.range_low
            targets.append(TargetResult(
                price=round(cons.range_low, 4), reason="consolidation range low", distance=round(reward2, 4),
                reward_per_unit=round(reward2, 4), r_multiple=round(reward2 / risk_per_unit, 3), evidence={},
            ))

    trend_quality = snapshot.trend_quality.quality if snapshot.trend_quality else None
    mtf_level = snapshot.multi_timeframe_alignment.level if snapshot.multi_timeframe_alignment else None
    rr1 = targets[0].r_multiple if targets else None
    quality = compute_quality_score(
        trend_quality=trend_quality, multi_timeframe_level=mtf_level, volume_level=snapshot.volume_level,
        volatility_level=(snapshot.volatility.level if snapshot.volatility else None), rr1=rr1, min_rr=min_rr,
        setup_confidence=0.5,
    )
    soft_concerns = soft_concerns_from_reasons([])

    return PlaybookEvaluation(
        playbook_id=definition.playbook_id, playbook_version=definition.version, name=definition.name,
        direction=definition.direction, family=definition.family,
        eligibility_status=EligibilityStatus.ELIGIBLE.value, setup_status=status,
        prerequisites_satisfied=satisfied, prerequisites_missing=(),
        trigger_status=True,
        trigger_conditions_satisfied=(EvidenceItem(code="TRIGGER", label="Price at range high", observed_value=str(current_price), expected_value=f">= {cons.range_high - TOUCH_PROXIMITY_ATR * atr_value:.4f}", passed=True, source="consolidation"),),
        reasons_for=tuple(reasons_for), soft_concerns=soft_concerns,
        quality_score=quality.score, quality_band=quality.band, quality_breakdown=quality_breakdown_from_score(quality),
        candidate=candidate, invalidation=invalidation, targets=tuple(targets),
        entry_price=entry_price, entry_zone_low=candidate.entry_zone_low, entry_zone_high=candidate.entry_zone_high,
        entry_type=definition.entry_type, entry_explanation=f"{definition.entry_type}: enter at the range high, targeting a reversion toward the range midpoint/low.",
        manual_review_notes=definition.manual_review_notes, statistical_validation_status=definition.statistical_validation_status,
        ranking=ranking_metadata(
            symbol=snapshot.symbol, timeframe=snapshot.timeframe, playbook_id=definition.playbook_id,
            playbook_version=definition.version, family=definition.family, direction=definition.direction,
            setup_status=status, quality_score=quality.score, quality_band=quality.band, hard_disqualifier_count=0,
            soft_concern_count=len(soft_concerns), entry_price=entry_price, stop_price=invalidation.stop_price,
            target1=(targets[0].price if targets else None), rr1=rr1,
            data_quality_status="OK" if snapshot.data_quality_ok else "DATA_QUALITY_FAILURE",
        ),
        evidence=dict(candidate.evidence),
    )
