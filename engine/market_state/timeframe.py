"""Multi-timeframe alignment (§19).

Operates on already-computed per-timeframe market states — fetching bars and
running the pipeline per timeframe is the orchestrator's job
(market_intelligence.py), not this module's. Keeping this pure makes the
alignment RULE independently testable without any data-fetching involved.

A lower timeframe never silently overrides a higher-timeframe conflict: any
mix of bullish and bearish states anywhere in the set reports CONFLICTED, full
stop — it is never averaged away or vote-decided by the lower timeframes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .types import MarketState

_BULLISH_STATES = {MarketState.UPTREND_CONFIRMED, MarketState.TRANSITIONAL_BULLISH}
_BEARISH_STATES = {MarketState.DOWNTREND_CONFIRMED, MarketState.TRANSITIONAL_BEARISH}
# A warning state (CHOCH occurred) is a live conflict signal against its OWN
# established trend, but the trend hasn't actually reversed yet (§5) — so for
# cross-timeframe alignment purposes it's treated as neutral, not directional
# evidence either way.
_NEUTRAL_STATES = {
    MarketState.RANGE, MarketState.UPTREND_WARNING, MarketState.DOWNTREND_WARNING,
    MarketState.TRANSITIONAL_BULLISH, MarketState.TRANSITIONAL_BEARISH,
}


class AlignmentLevel(str, Enum):
    HIGH_ALIGNMENT = "HIGH_ALIGNMENT"
    MODERATE_ALIGNMENT = "MODERATE_ALIGNMENT"
    CONFLICTED = "CONFLICTED"
    NO_DIRECTIONAL_BIAS = "NO_DIRECTIONAL_BIAS"  # every known timeframe is RANGE/neutral
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def _bias(state: MarketState | None) -> str:
    if state is None:
        return "UNKNOWN"
    if state in _BULLISH_STATES:
        return "BULLISH"
    if state in _BEARISH_STATES:
        return "BEARISH"
    return "NEUTRAL"


@dataclass(frozen=True)
class MultiTimeframeAlignment:
    level: str  # an AlignmentLevel value
    timeframe_states: dict
    evidence: dict = field(default_factory=dict)


def compute_multi_timeframe_alignment(states: dict[str, MarketState | None]) -> MultiTimeframeAlignment:
    """`states` maps a timeframe label (e.g. "5min") to its MarketState, or None
    if that timeframe's data was insufficient to compute one."""
    biases = {tf: _bias(state) for tf, state in states.items()}
    known = {tf: b for tf, b in biases.items() if b != "UNKNOWN"}
    rendered_states = {tf: (s.value if s else None) for tf, s in states.items()}

    if len(known) < 2:
        return MultiTimeframeAlignment(
            level=AlignmentLevel.INSUFFICIENT_DATA.value,
            timeframe_states=rendered_states,
            evidence={"reason": "fewer than 2 timeframes with a usable state", "biases": biases},
        )

    directional = {tf: b for tf, b in known.items() if b in ("BULLISH", "BEARISH")}
    bullish_count = sum(1 for b in directional.values() if b == "BULLISH")
    bearish_count = sum(1 for b in directional.values() if b == "BEARISH")

    if bullish_count > 0 and bearish_count > 0:
        level = AlignmentLevel.CONFLICTED
    elif not directional:
        level = AlignmentLevel.NO_DIRECTIONAL_BIAS
    elif len(directional) == len(known):
        level = AlignmentLevel.HIGH_ALIGNMENT
    else:
        level = AlignmentLevel.MODERATE_ALIGNMENT

    return MultiTimeframeAlignment(
        level=level.value,
        timeframe_states=rendered_states,
        evidence={"biases": biases, "bullish_count": bullish_count, "bearish_count": bearish_count},
    )
