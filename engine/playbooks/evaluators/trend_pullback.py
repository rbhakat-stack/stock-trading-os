"""TREND_PULLBACK_LONG / TREND_PULLBACK_SHORT — wraps engine.trade.setups'
existing match_trend_pullback_long/short matchers unchanged.
"""
from __future__ import annotations

from engine.trade import setups
from engine.trade.candidate import SetupType

from .._shared import not_eligible_evaluation, wrap_candidate_into_evaluation
from ..evidence import EvidenceItem
from ..registry import get_definition


def evaluate_trend_pullback_long(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.TREND_PULLBACK_LONG.value)
    regime_ok = snapshot.market_state == "UPTREND_CONFIRMED"
    regime_item = EvidenceItem(
        code="REGIME", label="Confirmed uptrend", observed_value=snapshot.market_state,
        expected_value="UPTREND_CONFIRMED", passed=regime_ok, source="market_state",
    )
    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    tq_ok = tq not in ("FAILURE_RISK", "REVERSAL_DEVELOPING")
    tq_item = EvidenceItem(
        code="TREND_QUALITY", label="Trend quality not failing/reversing", observed_value=tq,
        expected_value="not FAILURE_RISK/REVERSAL_DEVELOPING", passed=tq_ok, source="trend_quality",
    )
    zone = None
    if regime_ok and tq_ok:
        from engine.trade.setups import _nearest_zone_below
        zone = _nearest_zone_below(snapshot.support_zones, current_price)
    zone_item = EvidenceItem(
        code="SUPPORT_ZONE", label="A support zone exists below current price",
        observed_value=(f"[{zone.lower_boundary},{zone.upper_boundary}]" if zone else "NONE"),
        expected_value="at least one support zone below price", passed=zone is not None, source="support_zones",
    )
    items = (regime_item, tq_item, zone_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_trend_pullback_long(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)


def evaluate_trend_pullback_short(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.TREND_PULLBACK_SHORT.value)
    regime_ok = snapshot.market_state == "DOWNTREND_CONFIRMED"
    regime_item = EvidenceItem(
        code="REGIME", label="Confirmed downtrend", observed_value=snapshot.market_state,
        expected_value="DOWNTREND_CONFIRMED", passed=regime_ok, source="market_state",
    )
    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    tq_ok = tq not in ("FAILURE_RISK", "REVERSAL_DEVELOPING")
    tq_item = EvidenceItem(
        code="TREND_QUALITY", label="Trend quality not failing/reversing", observed_value=tq,
        expected_value="not FAILURE_RISK/REVERSAL_DEVELOPING", passed=tq_ok, source="trend_quality",
    )
    zone = None
    if regime_ok and tq_ok:
        from engine.trade.setups import _nearest_zone_above
        zone = _nearest_zone_above(snapshot.resistance_zones, current_price)
    zone_item = EvidenceItem(
        code="RESISTANCE_ZONE", label="A resistance zone exists above current price",
        observed_value=(f"[{zone.lower_boundary},{zone.upper_boundary}]" if zone else "NONE"),
        expected_value="at least one resistance zone above price", passed=zone is not None, source="resistance_zones",
    )
    items = (regime_item, tq_item, zone_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_trend_pullback_short(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)
