"""BOS/CHOCH strength scoring and post-CHOCH outcome tracking (§4-5).

Does NOT redetect BOS/CHOCH — `trend.classify_trend()` already determines WHEN
a bullish/bearish BOS or CHOCH occurs (its "wait for pullback before
confirming" logic is the tested source of truth from Phase 1). This module
scores the STRENGTH of an already-detected BOS using bar-level evidence, and
tracks what happens after a CHOCH: PENDING -> REVERSAL_CONTINUING ->
REVERSAL_CONFIRMED, or INVALIDATED if price reclaimed the original trend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd

from .types import MarketStateEvent

# Composite-score thresholds — documented, not buried magic numbers.
STRONG_THRESHOLD = 0.66
MODERATE_THRESHOLD = 0.40


class BosStrength(str, Enum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


class ChochOutcome(str, Enum):
    PENDING = "PENDING"  # CHOCH just occurred; nothing has happened since
    REVERSAL_CONTINUING = "REVERSAL_CONTINUING"  # movement since CHOCH, not yet resolved
    REVERSAL_CONFIRMED = "REVERSAL_CONFIRMED"  # trend FSM actually flipped to the new confirmed trend
    INVALIDATED = "INVALIDATED"  # price reclaimed and returned to the original trend


@dataclass(frozen=True)
class BosEvaluation:
    ts: datetime
    direction: str  # "BULLISH" | "BEARISH"
    broken_level: float
    break_distance: float
    break_distance_atr: float
    body_close_break: bool
    candle_body_ratio: float
    volume: float | None
    rvol: float | None
    follow_through_bars: int
    strength: BosStrength
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChochEvaluation:
    ts: datetime
    direction: str  # "BEARISH_CHOCH" | "BULLISH_CHOCH"
    broken_level: float | None
    outcome: ChochOutcome
    evidence: dict = field(default_factory=dict)


def evaluate_bos(
    df: pd.DataFrame,
    event: MarketStateEvent,
    atr_series: pd.Series,
    rvol_series: pd.Series | None = None,
    max_follow_through_bars: int = 10,
) -> BosEvaluation | None:
    """`event` must be a BOS event from classify_trend() output. Returns None if
    `event` isn't a BOS (e.g. it's a CHOCH or structure-update event)."""
    kind = event.evidence.get("event")
    if kind not in ("BULLISH_BOS", "BEARISH_BOS"):
        return None

    direction = "BULLISH" if kind == "BULLISH_BOS" else "BEARISH"
    level = float(event.evidence["broken_level"])

    try:
        bar_idx = df.index.get_loc(event.ts)
    except KeyError:
        return None

    row = df.iloc[bar_idx]
    atr_val = atr_series.iloc[bar_idx]
    if pd.isna(atr_val) or atr_val <= 0:
        fallback = atr_series.dropna().mean()
        atr_val = fallback if fallback and fallback > 0 else 1.0

    close = float(row["close"])
    break_distance = abs(close - level)
    break_distance_atr = break_distance / atr_val

    # trend.classify_trend() only triggers a BOS on a CLOSE beyond the level, so this
    # is always True for FSM-sourced events — kept explicit/defensive so this function
    # stays correct if ever fed a wick-only candidate from a different source.
    body_close_break = (close > level) if direction == "BULLISH" else (close < level)

    candle_range = float(row["high"] - row["low"])
    candle_body = abs(float(row["close"] - row["open"]))
    candle_body_ratio = (candle_body / candle_range) if candle_range > 0 else 0.0

    follow_through_bars = 0
    for i in range(bar_idx + 1, min(bar_idx + 1 + max_follow_through_bars, len(df))):
        c = float(df["close"].iloc[i])
        holds = (c > level) if direction == "BULLISH" else (c < level)
        if not holds:
            break
        follow_through_bars += 1

    rvol = None
    if rvol_series is not None and bar_idx < len(rvol_series):
        v = rvol_series.iloc[bar_idx]
        rvol = None if pd.isna(v) else float(v)

    parts = [
        min(break_distance_atr, 2.0) / 2.0,
        1.0 if body_close_break else 0.0,
        min(candle_body_ratio, 1.0),
        min(follow_through_bars / max_follow_through_bars, 1.0) if max_follow_through_bars else 0.0,
    ]
    if rvol is not None:
        parts.append(min(rvol, 2.5) / 2.5)
    score = sum(parts) / len(parts)

    if not body_close_break:
        strength = BosStrength.WEAK
    elif score >= STRONG_THRESHOLD:
        strength = BosStrength.STRONG
    elif score >= MODERATE_THRESHOLD:
        strength = BosStrength.MODERATE
    else:
        strength = BosStrength.WEAK

    return BosEvaluation(
        ts=event.ts,
        direction=direction,
        broken_level=level,
        break_distance=round(break_distance, 4),
        break_distance_atr=round(break_distance_atr, 3),
        body_close_break=body_close_break,
        candle_body_ratio=round(candle_body_ratio, 3),
        volume=float(row["volume"]) if "volume" in row else None,
        rvol=rvol,
        follow_through_bars=follow_through_bars,
        strength=strength,
        evidence={
            "score": round(score, 3),
            "strong_threshold": STRONG_THRESHOLD,
            "moderate_threshold": MODERATE_THRESHOLD,
            "components": {
                "break_distance_atr": round(break_distance_atr, 3),
                "body_close_break": body_close_break,
                "candle_body_ratio": round(candle_body_ratio, 3),
                "follow_through_bars": follow_through_bars,
                "rvol": rvol,
            },
        },
    )


def evaluate_latest_choch(events: list[MarketStateEvent]) -> ChochEvaluation | None:
    """Finds the most recent CHOCH in the event stream and classifies what has
    happened since, per §5's "track what happens after CHOCH" requirement.
    Never itself claims REVERSAL_CONFIRMED — that only happens once the FSM
    (trend.classify_trend) actually emits the corresponding confirmation event.
    """
    choch_indices = [i for i, e in enumerate(events) if e.evidence.get("event") in ("BEARISH_CHOCH", "BULLISH_CHOCH")]
    if not choch_indices:
        return None

    idx = choch_indices[-1]
    latest = events[idx]
    subsequent = events[idx + 1 :]

    outcome = ChochOutcome.PENDING
    for e in subsequent:
        kind = e.evidence.get("event")
        if kind in ("DOWNTREND_CONFIRMED_FROM_CHOCH", "UPTREND_CONFIRMED_FROM_CHOCH"):
            outcome = ChochOutcome.REVERSAL_CONFIRMED
            break
        if kind == "CHOCH_INVALIDATED":
            outcome = ChochOutcome.INVALIDATED
            break
    if outcome == ChochOutcome.PENDING and subsequent:
        outcome = ChochOutcome.REVERSAL_CONTINUING

    return ChochEvaluation(
        ts=latest.ts,
        direction=latest.evidence.get("event"),
        broken_level=latest.evidence.get("broken_level"),
        outcome=outcome,
        evidence={
            "note": latest.evidence.get("note"),
            "subsequent_event_count": len(subsequent),
            "subsequent_events": [e.evidence.get("event") for e in subsequent],
        },
    )


def choch_banner_message(choch: ChochEvaluation) -> tuple[str, str]:
    """Single source of truth for how a CHOCH's outcome is communicated —
    returns (message, severity) where severity is "warning" or "info".

    Used by BOTH the snapshot's warnings list (market_intelligence.build_snapshot)
    and the Market Reader's top banner / panel display, so they can never
    disagree. This exists because of a real bug: the top banner used to always
    say "REVERSAL NOT YET CONFIRMED" regardless of outcome — hardcoded at the
    point the CHOCH was first detected — while a separately-written panel
    correctly re-checked the outcome every render. Two independent call sites
    computing the same fact is exactly how they drifted; one shared function
    that both call is the actual fix, not just making the two agree today.
    """
    if choch.outcome in (ChochOutcome.PENDING, ChochOutcome.REVERSAL_CONTINUING):
        return (
            f"CHOCH DETECTED — STRUCTURE WARNING — REVERSAL NOT YET CONFIRMED ({choch.direction})",
            "warning",
        )
    if choch.outcome == ChochOutcome.REVERSAL_CONFIRMED:
        return (f"CHOCH FOLLOW-THROUGH — REVERSAL CONFIRMED ({choch.direction})", "info")
    # INVALIDATED
    return (f"Prior CHOCH invalidated — original trend resumed ({choch.direction})", "info")
