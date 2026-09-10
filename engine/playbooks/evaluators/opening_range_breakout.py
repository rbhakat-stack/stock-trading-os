"""OPENING_RANGE_BREAKOUT_LONG / _SHORT — a NEW Phase 4 playbook (no Phase 3
equivalent existed). Built entirely on engine.market_state.opening_range's
existing, already-robust OpeningRangeEvaluation (ORH/ORL/breakout state) —
reuses engine.market_state.breakouts.classify_breakout's output exactly as
computed by compute_opening_range, never re-derives breakout logic.

Note: this module does NOT go through engine.trade.setups (there is no
Phase 3 equivalent to preserve), so it builds its own TradeCandidate — but
uses the exact same TradeCandidate shape, and the same
compute_long_invalidation/compute_short_invalidation/compute_targets/
compute_quality_score pure functions every other playbook uses, via
_shared.wrap_candidate_into_evaluation.
"""
from __future__ import annotations

from engine.trade.candidate import CandidateStatus, EntryMethod, TradeCandidate

from .._shared import not_eligible_evaluation, wrap_candidate_into_evaluation
from ..evidence import EvidenceItem
from ..registry import get_definition
from ..taxonomy import PlaybookId


def _forming_still_inside_range(definition, snapshot, current_price: float, min_rr: float, orr, direction: str, satisfied):
    """Price hasn't broken out of the opening range yet — a genuine FORMING
    state (waiting for the break), not NOT_ELIGIBLE (which would imply this
    playbook could never apply to today's session at all)."""
    level = orr.orh if direction == "LONG" else orr.orl
    other_level = orr.orl if direction == "LONG" else orr.orh
    candidate = TradeCandidate(
        setup_type=definition.playbook_id, direction=direction, status=CandidateStatus.FORMING.value,
        structural_level=other_level, entry_zone_low=None, entry_zone_high=None,
        entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value,
        reasons_for=[f"today's opening range is established (ORH={orr.orh}, ORL={orr.orl})"], reasons_against=[],
        conditions_to_wait_for=[f"price breaks {'above' if direction == 'LONG' else 'below'} {level}"],
        evidence={"orh": orr.orh, "orl": orr.orl},
    )
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)

_FAILED_STATES = ("FAILED_BREAKOUT", "FAKEOUT", "BREAKOUT_THAT_LATER_FAILED")
_HELD_STATES = ("BREAKOUT_ATTEMPT", "WEAK_BREAKOUT", "SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST")
ENTRY_BUFFER_ATR = 0.1


def evaluate_opening_range_breakout_long(snapshot, current_price: float, min_rr: float):
    definition = get_definition(PlaybookId.OPENING_RANGE_BREAKOUT_LONG.value)
    orr = snapshot.opening_range
    orr_ok = orr is not None and orr.orh is not None and orr.orl is not None
    orr_item = EvidenceItem(
        code="OPENING_RANGE_COMPUTED", label="Opening range (ORH/ORL) computed for today's session",
        observed_value=(f"ORH={orr.orh} ORL={orr.orl}" if orr_ok else "NONE"),
        expected_value="a valid opening range", passed=orr_ok, source="opening_range",
    )
    if not orr_ok:
        return not_eligible_evaluation(definition, snapshot, (), (orr_item,))

    breakout = orr.breakout
    direction_ok = breakout is not None and breakout.direction == "UP"
    not_failed = breakout is not None and breakout.state.value not in _FAILED_STATES
    direction_item = EvidenceItem(
        code="BREAK_DIRECTION_UP", label="Price broke above the opening range high",
        observed_value=(breakout.direction if breakout else orr.status), expected_value="UP",
        passed=direction_ok, source="opening_range.breakout",
    )
    satisfied = tuple(i for i in (orr_item, direction_item) if i.passed)
    missing = tuple(i for i in (orr_item, direction_item) if not i.passed)

    if not direction_ok:
        if orr.status == "INSIDE_OPENING_RANGE":
            return _forming_still_inside_range(definition, snapshot, current_price, min_rr, orr, "LONG", (orr_item,))
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    if not not_failed:
        # A genuine ORH break occurred but it already failed — a real,
        # already-known outcome, not a missing prerequisite. NOT_ELIGIBLE
        # (this exact opening range's breakout attempt is over) rather than
        # a fabricated FORMING/TRIGGERED status.
        failed_item = EvidenceItem(
            code="BREAKOUT_NOT_FAILED", label="The breakout has not already failed",
            observed_value=breakout.state.value, expected_value="not FAILED_BREAKOUT/FAKEOUT/BREAKOUT_THAT_LATER_FAILED",
            passed=False, source="opening_range.breakout",
        )
        return not_eligible_evaluation(definition, snapshot, satisfied, (failed_item,))

    status = CandidateStatus.TRIGGERED.value
    reasons_for = [f"price broke above the opening range high ({orr.orh})", f"breakout state is {breakout.state.value}"]
    entry_low = round(orr.orh, 4)
    entry_high = round(orr.orh + ENTRY_BUFFER_ATR * (snapshot.atr_value or 0.0), 4)
    candidate = TradeCandidate(
        setup_type=PlaybookId.OPENING_RANGE_BREAKOUT_LONG.value, direction="LONG", status=status,
        structural_level=orr.orl, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value, reasons_for=reasons_for, reasons_against=[],
        conditions_to_wait_for=[], evidence={"orh": orr.orh, "orl": orr.orl, "breakout_state": breakout.state.value},
    )
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)


def evaluate_opening_range_breakout_short(snapshot, current_price: float, min_rr: float):
    definition = get_definition(PlaybookId.OPENING_RANGE_BREAKOUT_SHORT.value)
    orr = snapshot.opening_range
    orr_ok = orr is not None and orr.orh is not None and orr.orl is not None
    orr_item = EvidenceItem(
        code="OPENING_RANGE_COMPUTED", label="Opening range (ORH/ORL) computed for today's session",
        observed_value=(f"ORH={orr.orh} ORL={orr.orl}" if orr_ok else "NONE"),
        expected_value="a valid opening range", passed=orr_ok, source="opening_range",
    )
    if not orr_ok:
        return not_eligible_evaluation(definition, snapshot, (), (orr_item,))

    breakout = orr.breakout
    direction_ok = breakout is not None and breakout.direction == "DOWN"
    not_failed = breakout is not None and breakout.state.value not in _FAILED_STATES
    direction_item = EvidenceItem(
        code="BREAK_DIRECTION_DOWN", label="Price broke below the opening range low",
        observed_value=(breakout.direction if breakout else orr.status), expected_value="DOWN",
        passed=direction_ok, source="opening_range.breakout",
    )
    satisfied = tuple(i for i in (orr_item, direction_item) if i.passed)
    missing = tuple(i for i in (orr_item, direction_item) if not i.passed)

    if not direction_ok:
        if orr.status == "INSIDE_OPENING_RANGE":
            return _forming_still_inside_range(definition, snapshot, current_price, min_rr, orr, "SHORT", (orr_item,))
        return not_eligible_evaluation(definition, snapshot, satisfied, missing)
    if not not_failed:
        failed_item = EvidenceItem(
            code="BREAKDOWN_NOT_FAILED", label="The breakdown has not already failed",
            observed_value=breakout.state.value, expected_value="not FAILED_BREAKOUT/FAKEOUT/BREAKOUT_THAT_LATER_FAILED",
            passed=False, source="opening_range.breakout",
        )
        return not_eligible_evaluation(definition, snapshot, satisfied, (failed_item,))

    status = CandidateStatus.TRIGGERED.value
    reasons_for = [f"price broke below the opening range low ({orr.orl})", f"breakdown state is {breakout.state.value}"]
    entry_high = round(orr.orl, 4)
    entry_low = round(orr.orl - ENTRY_BUFFER_ATR * (snapshot.atr_value or 0.0), 4)
    candidate = TradeCandidate(
        setup_type=PlaybookId.OPENING_RANGE_BREAKOUT_SHORT.value, direction="SHORT", status=status,
        structural_level=orr.orh, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value, reasons_for=reasons_for, reasons_against=[],
        conditions_to_wait_for=[], evidence={"orh": orr.orh, "orl": orr.orl, "breakout_state": breakout.state.value},
    )
    return wrap_candidate_into_evaluation(definition, snapshot, current_price, candidate, satisfied, min_rr)
