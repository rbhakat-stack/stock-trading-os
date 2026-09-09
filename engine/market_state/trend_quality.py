"""Trend-quality scoring (§6).

Operates on the most recent confirmed-trend impulse/pullback leg pair, built
from the same major swing points and legs Phase 1 already computes
(`structure.detect_swing_legs`) — no separate swing detection here.

A trend currently in a WARNING state (CHOCH occurred) is always
REVERSAL_DEVELOPING regardless of leg shape — that classification comes
directly from market state, not the composite score. A market that isn't in a
confirmed trend at all has no trend quality to grade (NOT_APPLICABLE).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .structure import detect_swing_legs
from .types import MarketState, MarketStateEvent, SwingPoint, SwingSignificance

STRONG_THRESHOLD = 0.75
HEALTHY_THRESHOLD = 0.60
MODERATE_THRESHOLD = 0.45
WEAKENING_THRESHOLD = 0.30


class TrendQuality(str, Enum):
    STRONG = "STRONG"
    HEALTHY = "HEALTHY"
    MODERATE = "MODERATE"
    WEAKENING = "WEAKENING"
    FAILURE_RISK = "FAILURE_RISK"
    REVERSAL_DEVELOPING = "REVERSAL_DEVELOPING"


@dataclass(frozen=True)
class TrendQualityEvaluation:
    quality: str  # a TrendQuality value, "NOT_APPLICABLE", or "INSUFFICIENT_DATA"
    score: float | None
    evidence: dict = field(default_factory=dict)


def classify_trend_quality(
    swing_points: list[SwingPoint],
    latest_state: MarketState,
    recent_events: list[MarketStateEvent] | None = None,
) -> TrendQualityEvaluation:
    if latest_state in (MarketState.UPTREND_WARNING, MarketState.DOWNTREND_WARNING):
        return TrendQualityEvaluation(
            quality=TrendQuality.REVERSAL_DEVELOPING.value,
            score=None,
            evidence={"reason": "CHOCH has occurred; structure warning in effect"},
        )
    if latest_state not in (MarketState.UPTREND_CONFIRMED, MarketState.DOWNTREND_CONFIRMED):
        return TrendQualityEvaluation(
            quality="NOT_APPLICABLE", score=None, evidence={"reason": f"no confirmed trend (state={latest_state.value})"}
        )

    majors = sorted([p for p in swing_points if p.significance == SwingSignificance.MAJOR], key=lambda p: p.bar_index)
    if len(majors) < 4:
        return TrendQualityEvaluation(
            quality="INSUFFICIENT_DATA", score=None, evidence={"reason": "fewer than 4 major swing points available"}
        )

    legs = detect_swing_legs(majors)
    is_uptrend = latest_state == MarketState.UPTREND_CONFIRMED
    impulse_dir = "UP" if is_uptrend else "DOWN"
    pullback_dir = "DOWN" if is_uptrend else "UP"

    pullback_idx = None
    for i in range(len(legs) - 1, 0, -1):
        if legs[i].direction == pullback_dir and legs[i - 1].direction == impulse_dir:
            pullback_idx = i
            break
    if pullback_idx is None:
        return TrendQualityEvaluation(
            quality="INSUFFICIENT_DATA", score=None, evidence={"reason": "no completed impulse/pullback pair in recent legs"}
        )

    pullback1 = legs[pullback_idx]
    impulse1 = legs[pullback_idx - 1]
    if not impulse1.amplitude or not impulse1.end.bar_index > impulse1.start.bar_index:
        return TrendQualityEvaluation(quality="INSUFFICIENT_DATA", score=None, evidence={"reason": "degenerate impulse leg"})

    retracement_ratio = pullback1.amplitude / impulse1.amplitude
    duration_impulse = impulse1.end.bar_index - impulse1.start.bar_index
    duration_pullback = max(pullback1.end.bar_index - pullback1.start.bar_index, 0)
    duration_ratio = duration_pullback / duration_impulse

    growth_ratio = None
    if pullback_idx >= 3 and legs[pullback_idx - 3].direction == impulse_dir and legs[pullback_idx - 3].amplitude:
        impulse2 = legs[pullback_idx - 3]
        growth_ratio = impulse1.amplitude / impulse2.amplitude

    violation_count = sum(
        1 for e in (recent_events or []) if e.evidence.get("event") in ("BEARISH_CHOCH", "BULLISH_CHOCH")
    )
    violation_score = 1.0 if violation_count == 0 else max(0.0, 1.0 - 0.3 * violation_count)

    retracement_score = max(0.0, 1 - min(retracement_ratio, 1.5) / 1.5)
    duration_score = max(0.0, 1 - min(duration_ratio, 2.0) / 2.0)
    growth_score = min(growth_ratio, 2.0) / 2.0 if growth_ratio is not None else 0.5

    parts = [retracement_score, duration_score, growth_score, violation_score]
    score = sum(parts) / len(parts)

    if score >= STRONG_THRESHOLD:
        quality = TrendQuality.STRONG
    elif score >= HEALTHY_THRESHOLD:
        quality = TrendQuality.HEALTHY
    elif score >= MODERATE_THRESHOLD:
        quality = TrendQuality.MODERATE
    elif score >= WEAKENING_THRESHOLD:
        quality = TrendQuality.WEAKENING
    else:
        quality = TrendQuality.FAILURE_RISK

    return TrendQualityEvaluation(
        quality=quality.value,
        score=round(score, 3),
        evidence={
            "retracement_ratio": round(retracement_ratio, 3),
            "duration_ratio": round(duration_ratio, 3),
            "growth_ratio": round(growth_ratio, 3) if growth_ratio is not None else None,
            "violation_count": violation_count,
            "component_scores": {
                "retracement": round(retracement_score, 3),
                "duration": round(duration_score, 3),
                "growth": round(growth_score, 3),
                "violation": round(violation_score, 3),
            },
            "thresholds": {
                "strong": STRONG_THRESHOLD,
                "healthy": HEALTHY_THRESHOLD,
                "moderate": MODERATE_THRESHOLD,
                "weakening": WEAKENING_THRESHOLD,
            },
        },
    )
