"""The six Phase 3 setup archetypes (§4-5).

Deliberately limited scope — six high-quality, explicitly-defined archetypes,
not a general strategy framework. Every matcher consumes only fields already
present on a MarketIntelligenceSnapshot (market_state, trend_quality,
breakout, support_zones, resistance_zones, volume_level, volatility,
multi_timeframe_alignment) — none of them re-derive Phase 2 logic.

Each matcher returns None if the setup's basic regime/direction gate doesn't
apply at all, or a TradeCandidate with status FORMING (regime/structure is
right but the trigger hasn't happened yet) or TRIGGERED (the trigger has
occurred; the decision engine takes it from here).
"""
from __future__ import annotations

from engine.market_state.support_resistance import EnrichedZone

from .candidate import CandidateStatus, EntryMethod, SetupType, TradeCandidate

ENTRY_ZONE_BUFFER_ATR = 0.15
PULLBACK_PROXIMITY_ATR = 0.3
RECLAIM_PROXIMITY_ATR = 0.2


def _atr(snapshot) -> float:
    return snapshot.atr_value if snapshot.atr_value and snapshot.atr_value > 0 else 0.0


def _entry_zone(level: float, atr_value: float) -> tuple[float, float]:
    buffer = ENTRY_ZONE_BUFFER_ATR * atr_value
    return round(level - buffer, 4), round(level + buffer, 4)


def _nearest_zone_below(zones: list[EnrichedZone], price: float) -> EnrichedZone | None:
    candidates = [z for z in (zones or []) if z.lower_boundary <= price]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.upper_boundary)


def _nearest_zone_above(zones: list[EnrichedZone], price: float) -> EnrichedZone | None:
    candidates = [z for z in (zones or []) if z.upper_boundary >= price]
    if not candidates:
        return None
    return min(candidates, key=lambda z: z.lower_boundary)


def _soft_context_flags(snapshot) -> tuple[list[str], list[str]]:
    """Common soft-quality context checks shared across setups — volume,
    volatility, and multi-timeframe alignment. These are NOT hard gates (they
    don't return None); they're surfaced as reasons_for/against for the
    decision engine to weigh."""
    reasons_for: list[str] = []
    reasons_against: list[str] = []

    if snapshot.volume_level == "VERY_LOW":
        reasons_against.append("volume is VERY_LOW")
    elif snapshot.volume_level not in ("INSUFFICIENT_DATA", None):
        reasons_for.append(f"volume is {snapshot.volume_level}")

    vol_level = snapshot.volatility.level if snapshot.volatility else None
    if vol_level == "EXTREME":
        reasons_against.append("volatility is EXTREME")
    elif vol_level and vol_level != "INSUFFICIENT_DATA":
        reasons_for.append(f"volatility is {vol_level}")

    mtf_level = snapshot.multi_timeframe_alignment.level if snapshot.multi_timeframe_alignment else None
    if mtf_level == "CONFLICTED":
        reasons_against.append("multi-timeframe alignment is CONFLICTED")
    elif mtf_level == "HIGH_ALIGNMENT":
        reasons_for.append("multi-timeframe alignment is HIGH_ALIGNMENT")
    elif mtf_level == "MODERATE_ALIGNMENT":
        reasons_for.append("multi-timeframe alignment is MODERATE_ALIGNMENT")

    return reasons_for, reasons_against


# ===================== BREAKOUT_RETEST =====================


def match_breakout_retest_long(snapshot, current_price: float) -> TradeCandidate | None:
    if snapshot.market_state not in ("UPTREND_CONFIRMED", "TRANSITIONAL_BULLISH"):
        return None
    breakout = snapshot.breakout
    if breakout is None or breakout.direction != "UP":
        return None
    if breakout.state.value not in ("SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST"):
        return None

    atr_value = _atr(snapshot)
    level = breakout.level
    reasons_for = [f"market state is {snapshot.market_state}", f"breakout state is {breakout.state.value}"]
    reasons_against: list[str] = []
    conditions: list[str] = []

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    if breakout.state.value == "SUCCESSFUL_BREAKOUT_RETEST":
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append(f"price has already retested the broken level near {round(level, 4)}")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price returns to retest the broken level near {round(level, 4)} and holds as support")

    entry_low, entry_high = _entry_zone(level, atr_value)
    return TradeCandidate(
        setup_type=SetupType.BREAKOUT_RETEST_LONG.value, direction="LONG", status=status,
        structural_level=level, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"breakout_state": breakout.state.value, "breakout_follow_through": breakout.follow_through.value},
    )


def match_breakout_retest_short(snapshot, current_price: float) -> TradeCandidate | None:
    if snapshot.market_state not in ("DOWNTREND_CONFIRMED", "TRANSITIONAL_BEARISH"):
        return None
    breakout = snapshot.breakout
    if breakout is None or breakout.direction != "DOWN":
        return None
    if breakout.state.value not in ("SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST"):
        return None

    atr_value = _atr(snapshot)
    level = breakout.level
    reasons_for = [f"market state is {snapshot.market_state}", f"breakout state is {breakout.state.value}"]
    reasons_against: list[str] = []
    conditions: list[str] = []

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    if breakout.state.value == "SUCCESSFUL_BREAKOUT_RETEST":
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append(f"price has already retested the broken level near {round(level, 4)}")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price returns to retest the broken level near {round(level, 4)} and holds as resistance")

    entry_low, entry_high = _entry_zone(level, atr_value)
    return TradeCandidate(
        setup_type=SetupType.BREAKOUT_RETEST_SHORT.value, direction="SHORT", status=status,
        structural_level=level, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"breakout_state": breakout.state.value, "breakout_follow_through": breakout.follow_through.value},
    )


# ===================== TREND_PULLBACK =====================


def match_trend_pullback_long(snapshot, current_price: float) -> TradeCandidate | None:
    if snapshot.market_state != "UPTREND_CONFIRMED":
        return None
    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    if tq in ("FAILURE_RISK", "REVERSAL_DEVELOPING"):
        return None

    zone = _nearest_zone_below(snapshot.support_zones, current_price)
    if zone is None:
        return None

    atr_value = _atr(snapshot)
    level = zone.upper_boundary
    reasons_for = [f"market state is {snapshot.market_state}", "nearest support zone identified below price"]
    if tq:
        reasons_for.append(f"trend quality is {tq}")
    reasons_against: list[str] = []
    conditions: list[str] = []

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    proximity = (current_price - level) / atr_value if atr_value > 0 else float("inf")
    if current_price <= zone.upper_boundary + (0.05 * atr_value) and current_price >= zone.lower_boundary - (0.05 * atr_value):
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price has pulled back into the support zone")
    elif proximity <= PULLBACK_PROXIMITY_ATR:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price is within the pullback proximity of the support zone")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price pulls back into the support zone near {round(level, 4)}")

    entry_low, entry_high = zone.lower_boundary, zone.upper_boundary
    return TradeCandidate(
        setup_type=SetupType.TREND_PULLBACK_LONG.value, direction="LONG", status=status,
        structural_level=zone.lower_boundary, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"zone_strength_score": zone.strength_score, "zone_touch_count": zone.touch_count, "trend_quality": tq},
    )


def match_trend_pullback_short(snapshot, current_price: float) -> TradeCandidate | None:
    if snapshot.market_state != "DOWNTREND_CONFIRMED":
        return None
    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    if tq in ("FAILURE_RISK", "REVERSAL_DEVELOPING"):
        return None

    zone = _nearest_zone_above(snapshot.resistance_zones, current_price)
    if zone is None:
        return None

    atr_value = _atr(snapshot)
    level = zone.lower_boundary
    reasons_for = [f"market state is {snapshot.market_state}", "nearest resistance zone identified above price"]
    if tq:
        reasons_for.append(f"trend quality is {tq}")
    reasons_against: list[str] = []
    conditions: list[str] = []

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    proximity = (level - current_price) / atr_value if atr_value > 0 else float("inf")
    if current_price >= zone.lower_boundary - (0.05 * atr_value) and current_price <= zone.upper_boundary + (0.05 * atr_value):
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price has pulled back into the resistance zone")
    elif proximity <= PULLBACK_PROXIMITY_ATR:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append("price is within the pullback proximity of the resistance zone")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price pulls back into the resistance zone near {round(level, 4)}")

    entry_low, entry_high = zone.lower_boundary, zone.upper_boundary
    return TradeCandidate(
        setup_type=SetupType.TREND_PULLBACK_SHORT.value, direction="SHORT", status=status,
        structural_level=zone.upper_boundary, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.LIMIT.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"zone_strength_score": zone.strength_score, "zone_touch_count": zone.touch_count, "trend_quality": tq},
    )


# ===================== FAILED_BREAKOUT_REVERSAL =====================


def match_failed_breakout_reversal_long(snapshot, current_price: float) -> TradeCandidate | None:
    breakout = snapshot.breakout
    if breakout is None or breakout.direction != "DOWN":
        return None
    if breakout.state.value not in ("FAILED_BREAKOUT", "FAKEOUT", "BREAKOUT_THAT_LATER_FAILED"):
        return None

    atr_value = _atr(snapshot)
    level = breakout.level
    reasons_for = [f"breakdown attempt failed to hold ({breakout.state.value})"]
    reasons_against: list[str] = []
    conditions: list[str] = []

    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    if snapshot.market_state == "DOWNTREND_CONFIRMED" and tq in ("STRONG", "HEALTHY"):
        reasons_against.append(f"counter-trend against a {tq} confirmed downtrend — higher risk")

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    reclaimed = current_price > level
    if reclaimed:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append(f"price has reclaimed back above {round(level, 4)}")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price reclaims back above {round(level, 4)}")

    entry_low, entry_high = _entry_zone(level, atr_value)
    return TradeCandidate(
        setup_type=SetupType.FAILED_BREAKOUT_REVERSAL_LONG.value, direction="LONG", status=status,
        structural_level=level, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"breakout_state": breakout.state.value},
    )


def match_failed_breakout_reversal_short(snapshot, current_price: float) -> TradeCandidate | None:
    breakout = snapshot.breakout
    if breakout is None or breakout.direction != "UP":
        return None
    if breakout.state.value not in ("FAILED_BREAKOUT", "FAKEOUT", "BREAKOUT_THAT_LATER_FAILED"):
        return None

    atr_value = _atr(snapshot)
    level = breakout.level
    reasons_for = [f"breakout attempt failed to hold ({breakout.state.value})"]
    reasons_against: list[str] = []
    conditions: list[str] = []

    tq = snapshot.trend_quality.quality if snapshot.trend_quality else None
    if snapshot.market_state == "UPTREND_CONFIRMED" and tq in ("STRONG", "HEALTHY"):
        reasons_against.append(f"counter-trend against a {tq} confirmed uptrend — higher risk")

    soft_for, soft_against = _soft_context_flags(snapshot)
    reasons_for += soft_for
    reasons_against += soft_against

    reclaimed = current_price < level
    if reclaimed:
        status = CandidateStatus.TRIGGERED.value
        reasons_for.append(f"price has broken back below {round(level, 4)}")
    else:
        status = CandidateStatus.FORMING.value
        conditions.append(f"price breaks back below {round(level, 4)}")

    entry_low, entry_high = _entry_zone(level, atr_value)
    return TradeCandidate(
        setup_type=SetupType.FAILED_BREAKOUT_REVERSAL_SHORT.value, direction="SHORT", status=status,
        structural_level=level, entry_zone_low=entry_low, entry_zone_high=entry_high,
        entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value, reasons_for=reasons_for, reasons_against=reasons_against,
        conditions_to_wait_for=conditions,
        evidence={"breakout_state": breakout.state.value},
    )
