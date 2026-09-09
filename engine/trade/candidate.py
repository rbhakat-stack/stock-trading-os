"""Trade candidate domain objects (§3-4).

`detect_candidates` is the entry point: it runs all six setup matchers
(engine.trade.setups) against a MarketIntelligenceSnapshot and returns
whichever ones are at least partially relevant (FORMING or TRIGGERED) —
setups whose basic regime/direction gate doesn't apply at all (e.g. a bullish
setup while the market is in a confirmed downtrend) are not returned, to keep
output free of noise. Nothing here re-derives Phase 2 logic — every field
consumed comes directly from the snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CandidateStatus(str, Enum):
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class SetupType(str, Enum):
    BREAKOUT_RETEST_LONG = "BREAKOUT_RETEST_LONG"
    BREAKOUT_RETEST_SHORT = "BREAKOUT_RETEST_SHORT"
    TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"
    TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"
    FAILED_BREAKOUT_REVERSAL_LONG = "FAILED_BREAKOUT_REVERSAL_LONG"
    FAILED_BREAKOUT_REVERSAL_SHORT = "FAILED_BREAKOUT_REVERSAL_SHORT"


class EntryMethod(str, Enum):
    LIMIT = "LIMIT"
    STOP_TRIGGER = "STOP_TRIGGER"
    MARKET_ON_CONFIRMATION = "MARKET_ON_CONFIRMATION"
    NONE = "NONE"


@dataclass(frozen=True)
class TradeCandidate:
    setup_type: str  # a SetupType value
    direction: str  # "LONG" | "SHORT"
    status: str  # a CandidateStatus value
    structural_level: float  # the level invalidation/entry is anchored to
    entry_zone_low: float | None
    entry_zone_high: float | None
    entry_method: str  # an EntryMethod value
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    conditions_to_wait_for: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def detect_candidates(snapshot, current_price: float) -> list[TradeCandidate]:
    from . import setups

    matchers = (
        setups.match_breakout_retest_long,
        setups.match_breakout_retest_short,
        setups.match_trend_pullback_long,
        setups.match_trend_pullback_short,
        setups.match_failed_breakout_reversal_long,
        setups.match_failed_breakout_reversal_short,
    )
    candidates = []
    for matcher in matchers:
        result = matcher(snapshot, current_price)
        if result is not None:
            candidates.append(result)
    return candidates
