"""Canonical trading-domain vocabulary (see TRADING_OS_DESIGN.md §7).

These enums and dataclasses are the single source of truth for market-structure
terminology across the engine, the repository layer, and the UI — ambiguous
free-text labels ("upward trend") are never used elsewhere in the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingSignificance(str, Enum):
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class StructureLabel(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    NONE = "NONE"


class MarketState(str, Enum):
    RANGE = "RANGE"
    TRANSITIONAL_BULLISH = "TRANSITIONAL_BULLISH"
    TRANSITIONAL_BEARISH = "TRANSITIONAL_BEARISH"
    UPTREND_CONFIRMED = "UPTREND_CONFIRMED"
    DOWNTREND_CONFIRMED = "DOWNTREND_CONFIRMED"
    UPTREND_WARNING = "UPTREND_WARNING"
    DOWNTREND_WARNING = "DOWNTREND_WARNING"


@dataclass(frozen=True)
class SwingPoint:
    ts: datetime
    price: float
    swing_type: SwingType
    significance: SwingSignificance
    score: float
    bar_index: int
    label: StructureLabel = StructureLabel.NONE
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Swing:
    """A single upswing or downswing movement between two swing points (a MOVEMENT,
    distinct from the swing points themselves, which are POINTS — see §4)."""

    start: SwingPoint
    end: SwingPoint

    @property
    def direction(self) -> str:
        return "UP" if self.end.price > self.start.price else "DOWN"

    @property
    def amplitude(self) -> float:
        return abs(self.end.price - self.start.price)


@dataclass(frozen=True)
class MarketStateEvent:
    ts: datetime
    state: MarketState
    structure_label: StructureLabel
    evidence: dict = field(default_factory=dict)
