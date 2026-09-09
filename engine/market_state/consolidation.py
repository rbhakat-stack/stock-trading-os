"""Consolidation / compression detection (§7).

IMPORTANT — this is a LOCAL/RECENT analysis, not a statement about the
symbol's overall trend: it operates ONLY on a rolling window of the most
recent `window_bars` bars (default 24), independent of `trend.classify_trend`'s
confirmed market state, which reflects the FULL swing history. It is entirely
valid and unsurprising for a symbol to be in a confirmed uptrend overall while
its most recent N bars are locally range-bound or compressing (a pause inside
a larger trend) — that is NOT a contradiction, and this module never touches
or overrides the FSM's trend state to make the two "agree." Callers/UI should
label this result "RECENT CONSOLIDATION" / "RECENT COMPRESSION," never bare
"CONSOLIDATION," to keep that scope explicit — see the `evidence` dict on
every result, which always reports exactly which window was analyzed
(lookback_bars, range_width, range_width_atr, majors_in_window) and a
directional-progress measure (net price move across the window, ATR-normalized)
so the local/recent basis for the classification is auditable, not implied.

CONSOLIDATION means price is range-bound within that window — repeated
highs/lows near the window's boundaries, no persistent HH/HL or LH/LL
structure. COMPRESSION additionally requires swing amplitudes to be visibly
narrowing across the window. Neither classification implies a breakout
direction — that's for the caller (or a human) to judge once/if a breakout
actually occurs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from .structure import detect_swing_legs
from .types import StructureLabel, SwingPoint, SwingSignificance

TOUCH_PROXIMITY_ATR = 0.25  # how close to range_high/range_low counts as a "touch"
FALSE_BREAK_ATR = 0.15  # how far beyond the range (without a held close) counts as a false break
COMPRESSION_SHRINK_RATIO = 0.7  # second-half avg swing amplitude / first-half <= this => compressing
MIN_BOUNDARY_TOUCHES = 2
PERSISTENT_STRUCTURE_RATIO = 0.75  # share of recent labels that must agree to call it "trending"


class ConsolidationState(str, Enum):
    NONE = "NONE"
    CONSOLIDATION = "CONSOLIDATION"
    COMPRESSION = "COMPRESSION"


@dataclass(frozen=True)
class ConsolidationEvaluation:
    state: str
    range_high: float | None
    range_low: float | None
    range_midpoint: float | None
    range_width: float | None
    range_width_atr: float | None
    duration_bars: int
    touch_count_high: int
    touch_count_low: int
    false_break_count: int
    evidence: dict = field(default_factory=dict)


def _has_persistent_direction(majors: list[SwingPoint]) -> bool:
    labels = [p.label for p in majors if p.label != StructureLabel.NONE]
    if len(labels) < 2:
        return False
    hh_hl = sum(1 for lbl in labels if lbl in (StructureLabel.HH, StructureLabel.HL))
    lh_ll = sum(1 for lbl in labels if lbl in (StructureLabel.LH, StructureLabel.LL))
    return hh_hl >= len(labels) * PERSISTENT_STRUCTURE_RATIO or lh_ll >= len(labels) * PERSISTENT_STRUCTURE_RATIO


def _is_compressing(points: list[SwingPoint]) -> bool:
    # Deliberately takes MAJOR *and* MINOR points, unlike the persistent-direction
    # check above: a genuinely compressing market shrinks its own swings down
    # below the MAJOR significance threshold as it narrows, so restricting this
    # to majors-only would blind the detector to exactly the swings that prove
    # compression is happening.
    if len(points) < 4:
        return False
    ordered = sorted(points, key=lambda p: p.bar_index)
    legs = detect_swing_legs(ordered)
    if len(legs) < 2:
        return False
    mid = len(legs) // 2
    first_half_amp = sum(leg.amplitude for leg in legs[:mid]) / max(mid, 1)
    second_half_amp = sum(leg.amplitude for leg in legs[mid:]) / max(len(legs) - mid, 1)
    if first_half_amp <= 0:
        return False
    return (second_half_amp / first_half_amp) <= COMPRESSION_SHRINK_RATIO


def evaluate_consolidation(
    df: pd.DataFrame,
    swing_points: list[SwingPoint],
    atr_series: pd.Series,
    window_bars: int = 24,
) -> ConsolidationEvaluation:
    window_bars = min(window_bars, len(df))
    window = df.iloc[-window_bars:]

    atr_val = atr_series.iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        fallback = atr_series.dropna().mean()
        atr_val = fallback if fallback and fallback > 0 else 1.0

    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    range_width = range_high - range_low
    range_width_atr = range_width / atr_val
    midpoint = (range_high + range_low) / 2

    touch_high = int((window["high"] >= range_high - TOUCH_PROXIMITY_ATR * atr_val).sum())
    touch_low = int((window["low"] <= range_low + TOUCH_PROXIMITY_ATR * atr_val).sum())
    false_break_count = int(
        (
            ((window["high"] > range_high + FALSE_BREAK_ATR * atr_val) & (window["close"] <= range_high))
            | ((window["low"] < range_low - FALSE_BREAK_ATR * atr_val) & (window["close"] >= range_low))
        ).sum()
    )

    window_start_idx = len(df) - window_bars
    points_in_window = [p for p in swing_points if p.bar_index >= window_start_idx]
    majors_in_window = [p for p in points_in_window if p.significance == SwingSignificance.MAJOR]

    # Directional-progress measure: net price movement across the window,
    # ATR-normalized — near zero means price went nowhere net despite moving
    # around inside the window (the classic consolidation signature); a large
    # value means the window itself made real net directional progress. This
    # is exposed as evidence for every outcome below, not just consolidation/
    # compression, so the local-window basis for any classification is
    # auditable rather than implied.
    directional_progress_atr = abs(float(window["close"].iloc[-1]) - float(window["close"].iloc[0])) / atr_val

    base_evidence = {
        "lookback_bars": window_bars,
        "range_width": round(range_width, 4),
        "range_width_atr": round(range_width_atr, 3),
        "majors_in_window": len(majors_in_window),
        "directional_progress_atr": round(directional_progress_atr, 3),
    }

    if _has_persistent_direction(majors_in_window):
        return ConsolidationEvaluation(
            state=ConsolidationState.NONE.value,
            range_high=None, range_low=None, range_midpoint=None, range_width=None, range_width_atr=None,
            duration_bars=window_bars, touch_count_high=touch_high, touch_count_low=touch_low,
            false_break_count=false_break_count,
            evidence={**base_evidence, "reason": "trending (persistent HH/HL or LH/LL) structure present in this window"},
        )

    # Compression (a narrowing wedge) is checked independently of exact boundary
    # touch count: a genuinely compressing series makes progressively TIGHTER
    # extremes by definition, so it won't repeatedly touch the same outer
    # boundary the way a flat range does — requiring that would make compression
    # and "repeated touches" mutually exclusive, which is wrong.
    is_compressing = _is_compressing(points_in_window)

    if is_compressing:
        state = ConsolidationState.COMPRESSION
    elif touch_high >= MIN_BOUNDARY_TOUCHES and touch_low >= MIN_BOUNDARY_TOUCHES:
        state = ConsolidationState.CONSOLIDATION
    else:
        return ConsolidationEvaluation(
            state=ConsolidationState.NONE.value,
            range_high=None, range_low=None, range_midpoint=None, range_width=None, range_width_atr=None,
            duration_bars=window_bars, touch_count_high=touch_high, touch_count_low=touch_low,
            false_break_count=false_break_count,
            evidence={
                **base_evidence,
                "reason": "not trending in this window, but insufficient boundary touches for a clean range and no compression signal",
            },
        )

    return ConsolidationEvaluation(
        state=state.value,
        range_high=round(range_high, 4),
        range_low=round(range_low, 4),
        range_midpoint=round(midpoint, 4),
        range_width=round(range_width, 4),
        range_width_atr=round(range_width_atr, 3),
        duration_bars=window_bars,
        touch_count_high=touch_high,
        touch_count_low=touch_low,
        false_break_count=false_break_count,
        evidence={**base_evidence, "is_compressing": is_compressing},
    )
