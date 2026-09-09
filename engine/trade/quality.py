"""Trade quality score (§19) — a RULE-QUALITY score, never a probability of
profit. Formal statistical validation doesn't exist yet (Phase 5); this score
must never be presented or interpreted as one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DISCLAIMER = "This is a rule-quality score, not probability of profit."

LOW_MAX = 40
MODERATE_MAX = 70

_TREND_QUALITY_SCORES = {
    "STRONG": 1.0, "HEALTHY": 0.8, "MODERATE": 0.6, "WEAKENING": 0.3,
    "FAILURE_RISK": 0.1, "REVERSAL_DEVELOPING": 0.0,
}
_MTF_SCORES = {
    "HIGH_ALIGNMENT": 1.0, "MODERATE_ALIGNMENT": 0.6, "NO_DIRECTIONAL_BIAS": 0.5,
    "INSUFFICIENT_DATA": 0.5, "CONFLICTED": 0.0,
}
_VOLUME_SCORES = {
    "EXTREME": 0.6, "HIGH": 0.9, "ELEVATED": 1.0, "NORMAL": 0.7,
    "LOW": 0.4, "VERY_LOW": 0.1, "INSUFFICIENT_DATA": 0.5,
}
_VOLATILITY_SCORES = {
    "VERY_LOW": 0.4, "LOW": 0.6, "NORMAL": 1.0, "ELEVATED": 0.8,
    "HIGH": 0.5, "EXTREME": 0.1, "INSUFFICIENT_DATA": 0.5,
}

_WEIGHTS = {
    "structure": 0.20, "multi_timeframe": 0.15, "volume": 0.15,
    "volatility": 0.15, "setup_confidence": 0.15, "reward_risk": 0.20,
}


@dataclass(frozen=True)
class QualityScoreResult:
    score: int  # 0-100
    band: str  # LOW | MODERATE | HIGH
    components: dict = field(default_factory=dict)
    disclaimer: str = DISCLAIMER


def compute_quality_score(
    trend_quality: str | None,
    multi_timeframe_level: str | None,
    volume_level: str | None,
    volatility_level: str | None,
    rr1: float | None,
    min_rr: float,
    setup_confidence: float = 0.6,
) -> QualityScoreResult:
    """`setup_confidence` (0-1) lets the caller weigh in setup-specific signal
    strength (e.g. a BOS/retest's own strength classification) without this
    module needing to know about setup internals."""
    components = {
        "structure": _TREND_QUALITY_SCORES.get(trend_quality, 0.5),
        "multi_timeframe": _MTF_SCORES.get(multi_timeframe_level, 0.5),
        "volume": _VOLUME_SCORES.get(volume_level, 0.5),
        "volatility": _VOLATILITY_SCORES.get(volatility_level, 0.5),
        "setup_confidence": max(0.0, min(setup_confidence, 1.0)),
        "reward_risk": max(0.0, min((rr1 / min_rr) / 2.0, 1.0)) if rr1 and min_rr > 0 else 0.0,
    }
    composite = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)
    score = round(composite * 100)

    if score < LOW_MAX:
        band = "LOW"
    elif score < MODERATE_MAX:
        band = "MODERATE"
    else:
        band = "HIGH"

    return QualityScoreResult(score=score, band=band, components={k: round(v, 3) for k, v in components.items()})
