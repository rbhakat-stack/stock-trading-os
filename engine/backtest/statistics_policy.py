"""Phase 5.2V §8 — StatisticalPopulationPolicy: a versioned, machine-readable
contract defining which HistoricalTradeOutcome rows belong in which
statistical population, for Phase 5.3 to consume. This module computes
NOTHING — no win rate, no expectancy, no confidence interval. It only
classifies rows into named populations. See engine/backtest/outcomes.py for
the HistoricalTradeOutcome contract this classifies.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .outcomes import HistoricalTradeOutcome


class OutcomePopulation(str, Enum):
    OCCURRENCE = "OCCURRENCE"
    FILL_RATE_ELIGIBLE = "FILL_RATE_ELIGIBLE"
    REALIZED_TRADE = "REALIZED_TRADE"


# §8-C — the ONLY exit_status values that mean the execution policy actually,
# genuinely closed the trade. §9: EXPIRED_EOD and TIMEOUT belong here — the
# execution policy deliberately closes the trade at a real, executable price
# (session close / holding-limit close), unlike END_OF_DATA (§M below).
_REALIZED_EXIT_STATUSES = frozenset({"TARGET1", "STOP", "EXPIRED_EOD", "TIMEOUT"})

# §8-D — entry_status values that mean the occurrence could not even be
# meaningfully attempted (a data/geometry problem, not a trading outcome).
_INVALID_ENTRY_STATUSES = frozenset({"INVALID_STOP_GEOMETRY"})


@dataclass(frozen=True)
class StatisticalPopulationPolicy:
    version: str
    description: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("StatisticalPopulationPolicy requires an explicit version")


DEFAULT_STATISTICAL_POPULATION_POLICY = StatisticalPopulationPolicy(
    version="v1",
    description=(
        "A. OCCURRENCE: every valid historical TRIGGERED occurrence (entry_status != INVALID_STOP_GEOMETRY). "
        "B. FILL_RATE_ELIGIBLE: the same population as OCCURRENCE — invalid-geometry occurrences never enter "
        "the fill-rate denominator; FILLED vs EXPIRED_UNFILLED vs INVALIDATED_BEFORE_FILL are counted "
        "separately within it, never merged into one bucket. "
        "C. REALIZED_TRADE: FILLED trades whose exit_status is TARGET1, STOP, EXPIRED_EOD, or TIMEOUT — the "
        "execution policy genuinely, deliberately closed these at an executable price. "
        "D. EXCLUDED from realized performance statistics (counted/surfaced separately, NEVER hidden or "
        "deleted): END_OF_DATA (a mark-to-last-close observation, not a modeled exit), INVALID_STOP_GEOMETRY, "
        "EXPIRED_UNFILLED, INVALIDATED_BEFORE_FILL, and any other data-quality-invalid outcome."
    ),
)


def is_valid_occurrence(outcome: HistoricalTradeOutcome) -> bool:
    """§8-A."""
    return outcome.entry_status not in _INVALID_ENTRY_STATUSES


def is_fill_rate_eligible(outcome: HistoricalTradeOutcome) -> bool:
    """§8-B — same underlying population as `is_valid_occurrence`; a
    separately-named function so Phase 5.3 code reads its intent directly."""
    return is_valid_occurrence(outcome)


def is_filled(outcome: HistoricalTradeOutcome) -> bool:
    return outcome.entry_status == "FILLED"


def is_realized_trade(outcome: HistoricalTradeOutcome) -> bool:
    """§8-C/§9 — FILLED AND the execution policy reached a genuine,
    executable exit. END_OF_DATA is a FILLED trade too, but is deliberately
    NOT realized (§M) — it's where the available data ran out, not where the
    modeled strategy closed the position."""
    return outcome.entry_status == "FILLED" and outcome.exit_status in _REALIZED_EXIT_STATUSES


def is_excluded_from_realized_statistics(outcome: HistoricalTradeOutcome) -> bool:
    """§8-D — the positive-assertion complement of `is_realized_trade`, so a
    caller can explicitly tag "this row belongs in the excluded/surfaced
    bucket" without just negating the inclusion check."""
    return not is_realized_trade(outcome)


def classify_populations(outcome: HistoricalTradeOutcome) -> tuple[str, ...]:
    """Every named population (§8) `outcome` belongs to. Never drops or
    hides a row — an outcome excluded from REALIZED_TRADE still appears
    here (e.g. in OCCURRENCE/FILL_RATE_ELIGIBLE) unless it's genuinely
    invalid, matching §8-D's explicit 'must still be counted and surfaced
    separately, never deleted or hidden' requirement."""
    populations = []
    if is_valid_occurrence(outcome):
        populations.append(OutcomePopulation.OCCURRENCE.value)
    if is_fill_rate_eligible(outcome):
        populations.append(OutcomePopulation.FILL_RATE_ELIGIBLE.value)
    if is_realized_trade(outcome):
        populations.append(OutcomePopulation.REALIZED_TRADE.value)
    return tuple(populations)
