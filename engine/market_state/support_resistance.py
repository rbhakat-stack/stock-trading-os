"""Support/resistance zone builder + enrichment (§8, extending Phase 1 §13).

Zones are ranges, never a single magic price. `build_zones` (Phase 1) does a
simple greedy clustering of major swing points by ATR-normalized proximity.
`enrich_zones` (Phase 2) then scans the full bar history against each zone to
compute touch/rejection/break/retest counts and role-reversal history — the
"has this level flipped from resistance to support" tracking from §11.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from .types import SwingPoint, SwingSignificance, SwingType

DEFAULT_TOUCH_PROXIMITY_ATR = 0.15


def build_zones(swing_points: list[SwingPoint], atr_value: float, proximity_atr: float = 0.5) -> list[dict]:
    if atr_value <= 0:
        atr_value = 1.0
    majors = sorted(
        [p for p in swing_points if p.significance == SwingSignificance.MAJOR],
        key=lambda p: p.price,
    )

    clusters: list[dict] = []
    for p in majors:
        zone_type = "RESISTANCE" if p.swing_type == SwingType.HIGH else "SUPPORT"
        placed = False
        for cluster in clusters:
            if cluster["zone_type"] != zone_type:
                continue
            if abs(p.price - cluster["mid"]) <= proximity_atr * atr_value:
                cluster["points"].append(p)
                prices = [pt.price for pt in cluster["points"]]
                cluster["mid"] = sum(prices) / len(prices)
                placed = True
                break
        if not placed:
            clusters.append({"zone_type": zone_type, "mid": p.price, "points": [p]})

    zones = []
    for c in clusters:
        prices = [p.price for p in c["points"]]
        zones.append(
            {
                "zone_type": c["zone_type"],
                "upper_boundary": max(prices),
                "lower_boundary": min(prices),
                "touch_count": len(prices),
                "strength_score": round(min(1.0, 0.2 + 0.2 * len(prices)), 3),
                "source_bar_indices": [p.bar_index for p in c["points"]],
            }
        )
    return zones


@dataclass(frozen=True)
class RoleReversalEvent:
    ts: datetime
    from_type: str
    to_type: str


@dataclass(frozen=True)
class EnrichedZone:
    zone_type: str  # may differ from the input zone's type if a role reversal occurred
    upper_boundary: float
    lower_boundary: float
    strength_score: float
    touch_count: int
    rejection_count: int
    break_count: int
    retest_count: int
    last_touch_time: datetime | None
    role_reversal_history: list[RoleReversalEvent]
    source_bar_indices: list[int]
    evidence: dict = field(default_factory=dict)


def _enrich_one_zone(df: pd.DataFrame, zone: dict, atr_series: pd.Series, proximity_atr: float) -> EnrichedZone:
    upper = zone["upper_boundary"]
    lower = zone["lower_boundary"]

    touch_count = 0
    rejection_count = 0
    break_count = 0
    retest_count = 0
    last_touch_time = None
    role_reversal_history: list[RoleReversalEvent] = []
    current_type = zone["zone_type"]

    atr_fallback = atr_series.dropna().mean()
    atr_fallback = float(atr_fallback) if atr_fallback and atr_fallback > 0 else 1.0

    # A zone has exactly ONE meaningful break direction, determined by its
    # CURRENT type (which may itself have flipped after a confirmed role
    # reversal): a RESISTANCE zone is only broken by a close ABOVE it — a close
    # below is just ordinary distance, not a competing "break down." Mirror for
    # SUPPORT. Treating both sides as symmetric break candidates was an earlier
    # bug: it flagged an ordinary pullback away from a resistance zone as a
    # "break," which makes no directional sense.
    def _is_break_move(zone_type: str, close_val: float, buffer: float) -> bool:
        return (close_val > upper + buffer) if zone_type == "RESISTANCE" else (close_val < lower - buffer)

    def _is_retreat_move(zone_type: str, close_val: float, buffer: float) -> bool:
        # The move on the OPPOSITE side from this zone type's break direction —
        # only meaningful once already broken, as full reclamation of the level.
        return (close_val < lower - buffer) if zone_type == "RESISTANCE" else (close_val > upper + buffer)

    first_atr = atr_series.iloc[0]
    first_buffer = proximity_atr * (first_atr if not pd.isna(first_atr) and first_atr > 0 else atr_fallback)
    first_close = float(df["close"].iloc[0])
    # Establish whether the zone is already broken as of the very first bar
    # WITHOUT counting it as a break event — there's no prior intact state for
    # a series' opening bar to have broken from.
    broken = _is_break_move(current_type, first_close, first_buffer)

    for i in range(len(df)):
        atr_val = atr_series.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            atr_val = atr_fallback
        buffer = proximity_atr * atr_val

        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        close = float(df["close"].iloc[i])
        ts = df.index[i]

        touches_band = (high >= lower - buffer) and (low <= upper + buffer)
        counted_touch = False

        if not broken:
            if _is_break_move(current_type, close, buffer):
                break_count += 1
                broken = True
                counted_touch = True
            elif touches_band:
                rejection_count += 1
                counted_touch = True
            # A retreat move while unbroken is just ordinary movement on the
            # non-breakable side — not tracked as an event.
        else:
            if _is_retreat_move(current_type, close, buffer):
                # Price fully reclaimed the level — the break attempt failed;
                # reset to unbroken rather than counting a role reversal.
                broken = False
            elif not _is_break_move(current_type, close, buffer):
                # Neither still-beyond nor fully-reclaimed: price has come back
                # to the level without giving it up — a retest.
                retest_count += 1
                counted_touch = True
                new_type = "SUPPORT" if current_type == "RESISTANCE" else "RESISTANCE"
                if current_type != new_type:
                    role_reversal_history.append(RoleReversalEvent(ts=ts, from_type=current_type, to_type=new_type))
                    current_type = new_type
            # else: still beyond the level in the break direction (continuation) —
            # not itself a new event unless it also touches_band.

        if counted_touch or touches_band:
            touch_count += 1
            last_touch_time = ts

    return EnrichedZone(
        zone_type=current_type,
        upper_boundary=upper,
        lower_boundary=lower,
        strength_score=zone.get("strength_score", 0.0),
        touch_count=touch_count,
        rejection_count=rejection_count,
        break_count=break_count,
        retest_count=retest_count,
        last_touch_time=last_touch_time,
        role_reversal_history=role_reversal_history,
        source_bar_indices=zone.get("source_bar_indices", []),
        evidence={"proximity_atr": proximity_atr},
    )


def enrich_zones(
    df: pd.DataFrame,
    zones: list[dict],
    atr_series: pd.Series,
    proximity_atr: float = DEFAULT_TOUCH_PROXIMITY_ATR,
) -> list[EnrichedZone]:
    """Scans the full bar history against each zone from `build_zones` to compute
    touch/rejection/break/retest counts and role-reversal history."""
    return [_enrich_one_zone(df, zone, atr_series, proximity_atr) for zone in zones]


def _enrich_one_zone_fast(df: pd.DataFrame, zone: dict, atr_series: pd.Series, proximity_atr: float) -> EnrichedZone:
    """Phase 5.1P §Part B — implementation-equivalent optimization of
    `_enrich_one_zone`: PROFILING (not assumption) showed this function's
    per-bar `.iloc[]` scalar access accounted for ~94% of a full historical
    replay's total time — not `detect_swings`/`classify_trend` as originally
    suspected. Every access pattern, branch, and the exact order operations
    happen in is IDENTICAL to `_enrich_one_zone`; the only change is reading
    from plain numpy arrays (extracted once, up front) instead of repeatedly
    calling pandas' `Series.iloc[i]` (which carries substantial per-call
    overhead — isinstance checks, index/box reconstruction — verified via
    cProfile, see the Phase 5.1P report §H). This produces byte-identical
    output to `_enrich_one_zone`; see
    tests/test_phase51p_optimized_market_state.py for the exhaustive proof.
    `_enrich_one_zone` itself is UNTOUCHED and remains the golden reference.
    """
    upper = zone["upper_boundary"]
    lower = zone["lower_boundary"]

    touch_count = 0
    rejection_count = 0
    break_count = 0
    retest_count = 0
    last_touch_time = None
    role_reversal_history: list[RoleReversalEvent] = []
    current_type = zone["zone_type"]

    atr_fallback = atr_series.dropna().mean()
    atr_fallback = float(atr_fallback) if atr_fallback and atr_fallback > 0 else 1.0

    def _is_break_move(zone_type: str, close_val: float, buffer: float) -> bool:
        return (close_val > upper + buffer) if zone_type == "RESISTANCE" else (close_val < lower - buffer)

    def _is_retreat_move(zone_type: str, close_val: float, buffer: float) -> bool:
        return (close_val < lower - buffer) if zone_type == "RESISTANCE" else (close_val > upper + buffer)

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    atr_vals = atr_series.to_numpy()
    index = df.index
    n = len(df)

    first_atr = atr_vals[0]
    first_buffer = proximity_atr * (first_atr if not np.isnan(first_atr) and first_atr > 0 else atr_fallback)
    first_close = float(closes[0])
    broken = _is_break_move(current_type, first_close, first_buffer)

    for i in range(n):
        atr_val = atr_vals[i]
        if np.isnan(atr_val) or atr_val <= 0:
            atr_val = atr_fallback
        buffer = proximity_atr * atr_val

        high = float(highs[i])
        low = float(lows[i])
        close = float(closes[i])

        touches_band = (high >= lower - buffer) and (low <= upper + buffer)
        counted_touch = False

        if not broken:
            if _is_break_move(current_type, close, buffer):
                break_count += 1
                broken = True
                counted_touch = True
            elif touches_band:
                rejection_count += 1
                counted_touch = True
        else:
            if _is_retreat_move(current_type, close, buffer):
                broken = False
            elif not _is_break_move(current_type, close, buffer):
                retest_count += 1
                counted_touch = True
                new_type = "SUPPORT" if current_type == "RESISTANCE" else "RESISTANCE"
                if current_type != new_type:
                    role_reversal_history.append(
                        RoleReversalEvent(ts=index[i], from_type=current_type, to_type=new_type)
                    )
                    current_type = new_type

        if counted_touch or touches_band:
            touch_count += 1
            last_touch_time = index[i]

    return EnrichedZone(
        zone_type=current_type,
        upper_boundary=upper,
        lower_boundary=lower,
        strength_score=zone.get("strength_score", 0.0),
        touch_count=touch_count,
        rejection_count=rejection_count,
        break_count=break_count,
        retest_count=retest_count,
        last_touch_time=last_touch_time,
        role_reversal_history=role_reversal_history,
        source_bar_indices=zone.get("source_bar_indices", []),
        evidence={"proximity_atr": proximity_atr},
    )


def enrich_zones_fast(
    df: pd.DataFrame,
    zones: list[dict],
    atr_series: pd.Series,
    proximity_atr: float = DEFAULT_TOUCH_PROXIMITY_ATR,
) -> list[EnrichedZone]:
    """Phase 5.1P §Part B optimized counterpart to `enrich_zones` — see
    `_enrich_one_zone_fast`. Purely additive; `enrich_zones` is unchanged and
    remains what every existing caller uses by default."""
    return [_enrich_one_zone_fast(df, zone, atr_series, proximity_atr) for zone in zones]


def rank_zones_by_relevance(zones: list[EnrichedZone], current_price: float) -> list[EnrichedZone]:
    """Ranks (never filters or mutates) zones by display relevance — a DISPLAY
    concern, not a market-structure one. A long lookback naturally produces
    many distinct zones from `build_zones`' greedy ATR-proximity clustering
    (more major swing points spread across a wider price range over more time
    -> more clusters that don't merge); that's honest output, not a clustering
    bug, so this function doesn't reduce the underlying set — callers (the UI)
    decide how many of the ranked result to actually show, and the full,
    unranked list remains exactly what gets persisted/used for analysis.

    Composite of four components, each normalized to [0,1] against the given
    zone set so the ranking is meaningful regardless of scale:
      - strength_score (already 0..1, from build_zones)
      - touch_count + rejection_count, relative to the busiest zone in the set
      - recency of last_touch_time, relative to the set's own time span
      - proximity to current_price, relative to the widest distance in the set
    Weights: strength 0.35, interaction 0.25, proximity 0.25, recency 0.15.
    """
    if not zones:
        return []

    def zone_mid(z: EnrichedZone) -> float:
        return (z.upper_boundary + z.lower_boundary) / 2

    max_interactions = max((z.touch_count + z.rejection_count for z in zones), default=0) or 1

    touch_times = [z.last_touch_time for z in zones if z.last_touch_time is not None]
    min_touch = min(touch_times) if touch_times else None
    max_touch = max(touch_times) if touch_times else None
    touch_span_seconds = (max_touch - min_touch).total_seconds() if (min_touch and max_touch) else 0.0

    max_distance = max((abs(zone_mid(z) - current_price) for z in zones), default=0.0) or 1.0

    def relevance(z: EnrichedZone) -> float:
        strength = max(0.0, min(z.strength_score, 1.0))
        interaction = (z.touch_count + z.rejection_count) / max_interactions
        if z.last_touch_time is not None and touch_span_seconds > 0:
            recency = (z.last_touch_time - min_touch).total_seconds() / touch_span_seconds
        elif z.last_touch_time is not None:
            recency = 1.0  # every zone touched at the same single instant -> all equally recent
        else:
            recency = 0.0
        proximity = 1.0 - min(abs(zone_mid(z) - current_price) / max_distance, 1.0)
        return 0.35 * strength + 0.25 * interaction + 0.25 * proximity + 0.15 * recency

    return sorted(zones, key=relevance, reverse=True)
