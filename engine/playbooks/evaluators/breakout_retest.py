"""BREAKOUT_RETEST_LONG / BREAKOUT_RETEST_SHORT — wraps engine.trade.setups'
existing match_breakout_retest_long/short matchers unchanged (see the
package docstring's architectural boundary: the underlying TradeCandidate
this produces is byte-for-byte identical to Phase 3's own detection).
"""
from __future__ import annotations

from engine.trade import setups
from engine.trade.candidate import SetupType

from .._shared import not_eligible_evaluation, wrap_candidate_into_evaluation
from ..evidence import EvidenceItem
from ..registry import get_definition


def evaluate_breakout_retest_long(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.BREAKOUT_RETEST_LONG.value)
    regime_ok = snapshot.market_state in ("UPTREND_CONFIRMED", "TRANSITIONAL_BULLISH")
    regime_item = EvidenceItem(
        code="REGIME", label="Uptrend or transitional-bullish regime", observed_value=snapshot.market_state,
        expected_value="UPTREND_CONFIRMED or TRANSITIONAL_BULLISH", passed=regime_ok, source="market_state",
    )
    breakout = snapshot.breakout
    direction_ok = breakout is not None and breakout.direction == "UP"
    direction_item = EvidenceItem(
        code="BREAKOUT_DIRECTION", label="An upward breakout has occurred",
        observed_value=(breakout.direction if breakout else "NONE"), expected_value="UP",
        passed=direction_ok, source="breakout",
    )
    quality_ok = breakout is not None and breakout.state.value in ("SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST")
    quality_item = EvidenceItem(
        code="BREAKOUT_QUALITY", label="Breakout classified as successful",
        observed_value=(breakout.state.value if breakout else "NONE"),
        expected_value="SUCCESSFUL_BREAKOUT or SUCCESSFUL_BREAKOUT_RETEST", passed=quality_ok, source="breakout",
    )
    items = (regime_item, direction_item, quality_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_breakout_retest_long(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)


def evaluate_breakout_retest_short(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.BREAKOUT_RETEST_SHORT.value)
    regime_ok = snapshot.market_state in ("DOWNTREND_CONFIRMED", "TRANSITIONAL_BEARISH")
    regime_item = EvidenceItem(
        code="REGIME", label="Downtrend or transitional-bearish regime", observed_value=snapshot.market_state,
        expected_value="DOWNTREND_CONFIRMED or TRANSITIONAL_BEARISH", passed=regime_ok, source="market_state",
    )
    breakout = snapshot.breakout
    direction_ok = breakout is not None and breakout.direction == "DOWN"
    direction_item = EvidenceItem(
        code="BREAKOUT_DIRECTION", label="A downward breakout has occurred",
        observed_value=(breakout.direction if breakout else "NONE"), expected_value="DOWN",
        passed=direction_ok, source="breakout",
    )
    quality_ok = breakout is not None and breakout.state.value in ("SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST")
    quality_item = EvidenceItem(
        code="BREAKOUT_QUALITY", label="Breakdown classified as successful",
        observed_value=(breakout.state.value if breakout else "NONE"),
        expected_value="SUCCESSFUL_BREAKOUT or SUCCESSFUL_BREAKOUT_RETEST", passed=quality_ok, source="breakout",
    )
    items = (regime_item, direction_item, quality_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_breakout_retest_short(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)
