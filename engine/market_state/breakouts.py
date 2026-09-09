"""Breakout/breakdown classification, retest, and follow-through (§9-12).

One general-purpose classifier (`classify_breakout`), reused for BOS-driven
swing-level breaks, S/R-zone breaks, and opening-range breaks — rather than
three separate implementations that could drift apart from each other's
definitions of "successful" vs "failed" vs "fakeout."

Decision tree (thresholds are named constants below, not buried magic numbers):

  no forward bars yet                -> BREAKOUT_ATTEMPT / BREAKDOWN_ATTEMPT
  never reaches "sustained acceptance" (>= ACCEPTANCE_BARS closes beyond the
  level AND >= ACCEPTANCE_ATR of favorable excursion):
      returns inside within FAKEOUT_MAX_BARS bars     -> FAKEOUT
      returns inside after that                        -> FAILED_BREAKOUT
      hasn't returned inside yet, penetration is tiny   -> WEAK_BREAKOUT
      hasn't returned inside yet, penetration is real   -> BREAKOUT_ATTEMPT / BREAKDOWN_ATTEMPT
  reaches sustained acceptance:
      later closes meaningfully back through the level  -> BREAKOUT_THAT_LATER_FAILED
      retests the level and holds (bounces back)         -> SUCCESSFUL_BREAKOUT_RETEST
      no retest, or retest not yet resolved              -> SUCCESSFUL_BREAKOUT

"Meaningfully through the opposite side" (used to tell a real retest failure
apart from a one-bar wick-like dip back toward the level) requires closing
past the level by more than a small ATR buffer — see MEANINGFUL_BREACH_ATR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd

ACCEPTANCE_BARS = 5
ACCEPTANCE_ATR = 1.0
FAKEOUT_MAX_BARS = 2
WEAK_PENETRATION_ATR = 0.3
RETEST_PROXIMITY_ATR = 0.35
MEANINGFUL_BREACH_ATR = 0.15
DEFAULT_LOOKFORWARD_BARS = 30
RETEST_EVALUATION_BARS = 10  # how far past the initial return we look for retest behavior


class BreakoutState(str, Enum):
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    BREAKDOWN_ATTEMPT = "BREAKDOWN_ATTEMPT"
    WEAK_BREAKOUT = "WEAK_BREAKOUT"
    SUCCESSFUL_BREAKOUT = "SUCCESSFUL_BREAKOUT"
    SUCCESSFUL_BREAKOUT_RETEST = "SUCCESSFUL_BREAKOUT_RETEST"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    FAKEOUT = "FAKEOUT"
    BREAKOUT_THAT_LATER_FAILED = "BREAKOUT_THAT_LATER_FAILED"


class RetestState(str, Enum):
    NO_RETEST = "NO_RETEST"
    WEAK_RETEST = "WEAK_RETEST"
    SUCCESSFUL_RETEST = "SUCCESSFUL_RETEST"
    FAILED_RETEST = "FAILED_RETEST"


class FollowThroughState(str, Enum):
    NONE = "NONE"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


@dataclass(frozen=True)
class BreakoutEvaluation:
    ts: datetime
    direction: str  # "UP" | "DOWN"
    level: float
    break_bar_index: int
    break_distance_atr: float
    body_close_break: bool
    bars_beyond_level: int
    mfe_atr: float
    retest: RetestState
    follow_through: FollowThroughState
    state: BreakoutState
    evidence: dict = field(default_factory=dict)


def _atr_at(atr_series: pd.Series, idx: int) -> float:
    val = atr_series.iloc[idx]
    if pd.isna(val) or val <= 0:
        fallback = atr_series.dropna().mean()
        return float(fallback) if fallback and fallback > 0 else 1.0
    return float(val)


def _classify_follow_through(bars_beyond_level: int, mfe_atr: float) -> FollowThroughState:
    if bars_beyond_level == 0:
        return FollowThroughState.NONE
    if bars_beyond_level < 2 or mfe_atr < 0.5:
        return FollowThroughState.WEAK
    if bars_beyond_level < ACCEPTANCE_BARS or mfe_atr < ACCEPTANCE_ATR:
        return FollowThroughState.MODERATE
    return FollowThroughState.STRONG


def classify_breakout(
    df: pd.DataFrame,
    level: float,
    direction: str,
    break_bar_index: int,
    atr_series: pd.Series,
    lookforward_bars: int = DEFAULT_LOOKFORWARD_BARS,
) -> BreakoutEvaluation:
    if direction not in ("UP", "DOWN"):
        raise ValueError("direction must be 'UP' or 'DOWN'")

    atr_val = _atr_at(atr_series, break_bar_index)
    break_close = float(df["close"].iloc[break_bar_index])
    break_distance = abs(break_close - level)
    break_distance_atr = break_distance / atr_val
    body_close_break = (break_close > level) if direction == "UP" else (break_close < level)

    end_idx = min(break_bar_index + 1 + lookforward_bars, len(df))
    window = df.iloc[break_bar_index + 1 : end_idx]
    closes = window["close"].tolist()
    highs = window["high"].tolist()
    lows = window["low"].tolist()

    def beyond(c: float) -> bool:
        return c > level if direction == "UP" else c < level

    def meaningfully_through_opposite(c: float) -> bool:
        buffer = MEANINGFUL_BREACH_ATR * atr_val
        return (c < level - buffer) if direction == "UP" else (c > level + buffer)

    # Phase 1: initial persistence run right after the break.
    bars_beyond_level = 0
    for c in closes:
        if beyond(c):
            bars_beyond_level += 1
        else:
            break

    furthest = break_close
    excursion_bars = max(bars_beyond_level, 1)
    for h, l in zip(highs[:excursion_bars], lows[:excursion_bars]):
        furthest = max(furthest, h) if direction == "UP" else min(furthest, l)
    mfe_atr = abs(furthest - level) / atr_val

    acceptance_reached = bars_beyond_level >= ACCEPTANCE_BARS and mfe_atr >= ACCEPTANCE_ATR
    returned_inside = bars_beyond_level < len(closes)

    # Phase 2: only if price actually returned inside — what happens afterward.
    retest = RetestState.NO_RETEST
    lost_after_acceptance = False
    if returned_inside:
        remaining = closes[bars_beyond_level : bars_beyond_level + RETEST_EVALUATION_BARS]
        near_level = any(abs(c - level) <= RETEST_PROXIMITY_ATR * atr_val for c in remaining)
        broke_back_meaningfully = any(meaningfully_through_opposite(c) for c in remaining)
        resumed_after_return = any(beyond(c) for c in remaining)

        if acceptance_reached:
            if broke_back_meaningfully:
                lost_after_acceptance = True
                retest = RetestState.FAILED_RETEST if near_level else RetestState.NO_RETEST
            elif near_level and resumed_after_return:
                retest = RetestState.SUCCESSFUL_RETEST
            elif near_level:
                retest = RetestState.WEAK_RETEST

    attempt_state = BreakoutState.BREAKOUT_ATTEMPT if direction == "UP" else BreakoutState.BREAKDOWN_ATTEMPT

    if not acceptance_reached:
        if returned_inside:
            state = BreakoutState.FAKEOUT if bars_beyond_level <= FAKEOUT_MAX_BARS else BreakoutState.FAILED_BREAKOUT
        elif break_distance_atr < WEAK_PENETRATION_ATR:
            state = BreakoutState.WEAK_BREAKOUT
        else:
            state = attempt_state
    else:
        if lost_after_acceptance:
            state = BreakoutState.BREAKOUT_THAT_LATER_FAILED
        elif retest == RetestState.SUCCESSFUL_RETEST:
            state = BreakoutState.SUCCESSFUL_BREAKOUT_RETEST
        else:
            state = BreakoutState.SUCCESSFUL_BREAKOUT

    return BreakoutEvaluation(
        ts=df.index[break_bar_index],
        direction=direction,
        level=level,
        break_bar_index=break_bar_index,
        break_distance_atr=round(break_distance_atr, 3),
        body_close_break=body_close_break,
        bars_beyond_level=bars_beyond_level,
        mfe_atr=round(mfe_atr, 3),
        retest=retest,
        follow_through=_classify_follow_through(bars_beyond_level, mfe_atr),
        state=state,
        evidence={
            "acceptance_bars_threshold": ACCEPTANCE_BARS,
            "acceptance_atr_threshold": ACCEPTANCE_ATR,
            "fakeout_max_bars": FAKEOUT_MAX_BARS,
            "weak_penetration_atr": WEAK_PENETRATION_ATR,
            "retest_proximity_atr": RETEST_PROXIMITY_ATR,
            "meaningful_breach_atr": MEANINGFUL_BREACH_ATR,
            "returned_inside": returned_inside,
            "acceptance_reached": acceptance_reached,
            "forward_bars_evaluated": len(closes),
        },
    )
