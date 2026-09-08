"""Basic support/resistance zone builder (Phase 1 scope — see §13).

Zones are ranges, never a single magic price. This is a simple greedy clustering
of major swing points by ATR-normalized proximity; retest/role-reversal history
and higher-timeframe confluence are Phase 2+ refinements (see roadmap §31).
"""
from __future__ import annotations

from .types import SwingPoint, SwingSignificance, SwingType


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
