"""FAILED_BREAKOUT_REVERSAL_LONG / _SHORT — wraps engine.trade.setups'
existing match_failed_breakout_reversal_long/short matchers unchanged.
"""
from __future__ import annotations

from engine.trade import setups
from engine.trade.candidate import SetupType

from .._shared import not_eligible_evaluation, wrap_candidate_into_evaluation
from ..evidence import EvidenceItem
from ..registry import get_definition


def evaluate_failed_breakout_reversal_long(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.FAILED_BREAKOUT_REVERSAL_LONG.value)
    breakout = snapshot.breakout
    direction_ok = breakout is not None and breakout.direction == "DOWN"
    direction_item = EvidenceItem(
        code="BREAKDOWN_DIRECTION", label="A downward breakdown attempt exists",
        observed_value=(breakout.direction if breakout else "NONE"), expected_value="DOWN",
        passed=direction_ok, source="breakout",
    )
    failed_ok = breakout is not None and breakout.state.value in ("FAILED_BREAKOUT", "FAKEOUT", "BREAKOUT_THAT_LATER_FAILED")
    failed_item = EvidenceItem(
        code="BREAKDOWN_FAILED", label="The breakdown failed to hold",
        observed_value=(breakout.state.value if breakout else "NONE"),
        expected_value="FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED", passed=failed_ok, source="breakout",
    )
    items = (direction_item, failed_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_failed_breakout_reversal_long(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)


def evaluate_failed_breakout_reversal_short(snapshot, current_price: float, min_rr: float):
    definition = get_definition(SetupType.FAILED_BREAKOUT_REVERSAL_SHORT.value)
    breakout = snapshot.breakout
    direction_ok = breakout is not None and breakout.direction == "UP"
    direction_item = EvidenceItem(
        code="BREAKOUT_DIRECTION", label="An upward breakout attempt exists",
        observed_value=(breakout.direction if breakout else "NONE"), expected_value="UP",
        passed=direction_ok, source="breakout",
    )
    failed_ok = breakout is not None and breakout.state.value in ("FAILED_BREAKOUT", "FAKEOUT", "BREAKOUT_THAT_LATER_FAILED")
    failed_item = EvidenceItem(
        code="BREAKOUT_FAILED", label="The breakout failed to hold",
        observed_value=(breakout.state.value if breakout else "NONE"),
        expected_value="FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED", passed=failed_ok, source="breakout",
    )
    items = (direction_item, failed_item)
    satisfied = tuple(i for i in items if i.passed)
    missing = tuple(i for i in items if not i.passed)

    candidate = setups.match_failed_breakout_reversal_short(snapshot, current_price)
    if candidate is None:
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)
