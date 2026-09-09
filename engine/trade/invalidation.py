"""Structural invalidation / stop derivation (§6).

Every stop is derived from a structural level (a swing point, an S/R zone
boundary, a retest zone) plus an OPTIONAL small ATR buffer — the buffer is
never a substitute for structure, and a stop is never placed at an arbitrary
fixed cents/dollars offset with no structural basis.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_BUFFER_ATR = 0.1


@dataclass(frozen=True)
class InvalidationResult:
    stop_price: float
    structural_level: float
    atr_buffer: float
    reason: str
    evidence: dict = field(default_factory=dict)


def compute_long_invalidation(structural_level: float, atr_value: float, buffer_atr: float = DEFAULT_BUFFER_ATR) -> InvalidationResult:
    """Stop sits BELOW the structural support level, by a small ATR buffer —
    structure invalidates the long thesis; the buffer just avoids placing the
    stop exactly on the level where routine noise would tag it."""
    buffer = max(0.0, buffer_atr) * max(atr_value, 0.0)
    stop = structural_level - buffer
    return InvalidationResult(
        stop_price=round(stop, 4),
        structural_level=round(structural_level, 4),
        atr_buffer=round(buffer, 4),
        reason=f"below structural support at {round(structural_level, 4)} (ATR buffer {buffer_atr}x)",
        evidence={"buffer_atr_multiple": buffer_atr, "atr_value": round(atr_value, 4)},
    )


def compute_short_invalidation(structural_level: float, atr_value: float, buffer_atr: float = DEFAULT_BUFFER_ATR) -> InvalidationResult:
    """Mirror of compute_long_invalidation: stop sits ABOVE the structural
    resistance level."""
    buffer = max(0.0, buffer_atr) * max(atr_value, 0.0)
    stop = structural_level + buffer
    return InvalidationResult(
        stop_price=round(stop, 4),
        structural_level=round(structural_level, 4),
        atr_buffer=round(buffer, 4),
        reason=f"above structural resistance at {round(structural_level, 4)} (ATR buffer {buffer_atr}x)",
        evidence={"buffer_atr_multiple": buffer_atr, "atr_value": round(atr_value, 4)},
    )
