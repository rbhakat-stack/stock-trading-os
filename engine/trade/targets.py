"""Target engine (§8).

Targets are derived ONLY from opposing S/R zones already computed by Phase 2
(engine.market_state.support_resistance) — the nearest zone edge(s) beyond
entry, in the trade's direction. If fewer than two qualifying zones exist,
this returns fewer than two targets rather than fabricating one; there is no
ATR-projected/measured-move fallback here (a deliberate Phase 3 scope choice
— see TRADING_OS_DESIGN.md discussion — to stay unambiguously on the safe
side of "never fabricate a target").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.market_state.support_resistance import EnrichedZone

MAX_TARGETS = 2


@dataclass(frozen=True)
class TargetResult:
    price: float
    reason: str
    distance: float
    reward_per_unit: float
    r_multiple: float
    evidence: dict = field(default_factory=dict)


def compute_targets(
    direction: str,
    entry: float,
    stop: float,
    resistance_zones: list[EnrichedZone] | None = None,
    support_zones: list[EnrichedZone] | None = None,
) -> list[TargetResult]:
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be 'LONG' or 'SHORT'")

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return []

    targets: list[TargetResult] = []

    if direction == "LONG":
        candidates = sorted(
            (z for z in (resistance_zones or []) if z.lower_boundary > entry),
            key=lambda z: z.lower_boundary,
        )
        for zone in candidates[:MAX_TARGETS]:
            price = zone.lower_boundary
            reward = price - entry
            if reward <= 0:
                continue
            targets.append(
                TargetResult(
                    price=round(price, 4),
                    reason="nearest resistance zone edge",
                    distance=round(reward, 4),
                    reward_per_unit=round(reward, 4),
                    r_multiple=round(reward / risk_per_unit, 3),
                    evidence={
                        "zone_upper_boundary": zone.upper_boundary,
                        "zone_strength_score": zone.strength_score,
                        "zone_touch_count": zone.touch_count,
                    },
                )
            )
    else:  # SHORT
        candidates = sorted(
            (z for z in (support_zones or []) if z.upper_boundary < entry),
            key=lambda z: -z.upper_boundary,
        )
        for zone in candidates[:MAX_TARGETS]:
            price = zone.upper_boundary
            reward = entry - price
            if reward <= 0:
                continue
            targets.append(
                TargetResult(
                    price=round(price, 4),
                    reason="nearest support zone edge",
                    distance=round(reward, 4),
                    reward_per_unit=round(reward, 4),
                    r_multiple=round(reward / risk_per_unit, 3),
                    evidence={
                        "zone_lower_boundary": zone.lower_boundary,
                        "zone_strength_score": zone.strength_score,
                        "zone_touch_count": zone.touch_count,
                    },
                )
            )

    return targets
