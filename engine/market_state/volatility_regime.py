"""Volatility classification and expansion/contraction detection (§15-16).

Classifies current ATR by percentile rank against its own trailing history —
never against a fixed absolute threshold, since "normal" volatility differs
by symbol and price level. Never predicts direction from contraction alone;
this module only reports the regime, not what comes next.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

MIN_BARS_FOR_VOLATILITY_REGIME = 20
EXPANSION_CONTRACTION_WINDOW = 10
EXPANSION_CONTRACTION_CHANGE_THRESHOLD = 0.15  # +/-15% ATR change across the window's two halves

# Percentile-rank upper bounds for each bucket (e.g. <=10th percentile -> VERY_LOW).
VERY_LOW_PCTL = 10
LOW_PCTL = 30
NORMAL_PCTL = 70
ELEVATED_PCTL = 90
HIGH_PCTL = 97
# > HIGH_PCTL -> EXTREME


class VolatilityLevel(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class VolatilityTransition(str, Enum):
    EXPANSION = "EXPANSION"
    CONTRACTION = "CONTRACTION"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class VolatilityClassification:
    level: str  # a VolatilityLevel value, or "INSUFFICIENT_DATA"
    percentile: float | None
    transition: str  # a VolatilityTransition value
    evidence: dict = field(default_factory=dict)


def detect_expansion_contraction(
    atr_series: pd.Series, bar_index: int, window: int = EXPANSION_CONTRACTION_WINDOW
) -> str:
    start = max(0, bar_index - window + 1)
    segment = atr_series.iloc[start : bar_index + 1].dropna()
    if len(segment) < window:
        return VolatilityTransition.NEUTRAL.value

    first_half = segment.iloc[: len(segment) // 2].mean()
    second_half = segment.iloc[len(segment) // 2 :].mean()
    if not first_half or first_half <= 0:
        return VolatilityTransition.NEUTRAL.value

    change = (second_half - first_half) / first_half
    if change >= EXPANSION_CONTRACTION_CHANGE_THRESHOLD:
        return VolatilityTransition.EXPANSION.value
    if change <= -EXPANSION_CONTRACTION_CHANGE_THRESHOLD:
        return VolatilityTransition.CONTRACTION.value
    return VolatilityTransition.NEUTRAL.value


def classify_volatility(
    atr_series: pd.Series, bar_index: int, history_window: int = 60
) -> VolatilityClassification:
    current = atr_series.iloc[bar_index]
    if pd.isna(current):
        return VolatilityClassification(
            level="INSUFFICIENT_DATA", percentile=None, transition=VolatilityTransition.NEUTRAL.value,
            evidence={"reason": "ATR not yet available at this bar"},
        )

    history_start = max(0, bar_index - history_window + 1)
    history = atr_series.iloc[history_start : bar_index + 1].dropna()
    if len(history) < MIN_BARS_FOR_VOLATILITY_REGIME:
        return VolatilityClassification(
            level="INSUFFICIENT_DATA", percentile=None, transition=VolatilityTransition.NEUTRAL.value,
            evidence={"reason": f"fewer than {MIN_BARS_FOR_VOLATILITY_REGIME} ATR values in trailing history"},
        )

    # Mean-rank percentile (ties split credit) rather than a plain "<=" rank —
    # with a plain "<=" rank, a perfectly flat trailing history would rank the
    # current (identical) value at the 100th percentile and misreport EXTREME.
    less = int((history < current).sum())
    equal = int((history == current).sum())
    percentile = (less + 0.5 * equal) / len(history) * 100

    if percentile <= VERY_LOW_PCTL:
        level = VolatilityLevel.VERY_LOW.value
    elif percentile <= LOW_PCTL:
        level = VolatilityLevel.LOW.value
    elif percentile <= NORMAL_PCTL:
        level = VolatilityLevel.NORMAL.value
    elif percentile <= ELEVATED_PCTL:
        level = VolatilityLevel.ELEVATED.value
    elif percentile <= HIGH_PCTL:
        level = VolatilityLevel.HIGH.value
    else:
        level = VolatilityLevel.EXTREME.value

    return VolatilityClassification(
        level=level,
        percentile=round(percentile, 1),
        transition=detect_expansion_contraction(atr_series, bar_index),
        evidence={"history_bars": len(history), "atr_value": round(float(current), 4)},
    )
