"""HH/HL/LH/LL structure labeling and upswing/downswing extraction (see §4-6).

Only MAJOR swing points participate in structure labeling — minor swings pass
through unlabeled (NONE). This mirrors the brief's explicit instruction not to
classify every candle high/low as a meaningful swing.
"""
from __future__ import annotations

from dataclasses import replace

from .types import Swing, SwingPoint, SwingSignificance, SwingType, StructureLabel


def label_structure(swing_points: list[SwingPoint]) -> list[SwingPoint]:
    ordered = sorted(swing_points, key=lambda p: p.bar_index)
    majors = [p for p in ordered if p.significance == SwingSignificance.MAJOR]

    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    # Keyed by (bar_index, swing_type), NOT bar_index alone. A single "outside"/
    # wide-range bar can legitimately be both a swing HIGH candidate and a swing
    # LOW candidate at once (detect_swings emits both as distinct points) — keying
    # by bar_index alone collapsed those two distinct points onto one dict entry,
    # silently discarding one and duplicating the other. That duplicate is what
    # produced a literal duplicate (symbol, timeframe, ts, swing_type) row at
    # persistence time — see tests/test_swings_and_structure.py for the repro.
    labeled_by_key: dict[tuple[int, SwingType], SwingPoint] = {}

    for p in majors:
        if p.swing_type == SwingType.HIGH:
            label = StructureLabel.NONE if last_high is None else (
                StructureLabel.HH if p.price > last_high.price else StructureLabel.LH
            )
            last_high = p
        else:
            label = StructureLabel.NONE if last_low is None else (
                StructureLabel.HL if p.price > last_low.price else StructureLabel.LL
            )
            last_low = p
        labeled_by_key[(p.bar_index, p.swing_type)] = replace(p, label=label)

    return [labeled_by_key.get((p.bar_index, p.swing_type), p) for p in ordered]


def detect_swing_legs(swing_points: list[SwingPoint]) -> list[Swing]:
    """Upswings/downswings — MOVEMENTS between consecutive swing points, distinct
    from the points themselves (see §4)."""
    ordered = sorted(swing_points, key=lambda p: p.bar_index)
    return [Swing(start=a, end=b) for a, b in zip(ordered, ordered[1:])]
