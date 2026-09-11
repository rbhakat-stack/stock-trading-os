"""Phase 5.3 — EvidencePolicy: a versioned, machine-readable policy
controlling how PlaybookStatistics classifies evidence. Every threshold
here is an explicit, versioned RESEARCH DEFAULT — never empirically
optimized, never hardcoded inline elsewhere in the statistics engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidencePolicy:
    evidence_policy_id: str
    evidence_policy_version: str
    minimum_realized_trades: int
    confidence_level: float
    bootstrap_iterations: int
    bootstrap_seed: int
    development_fraction: float
    epsilon_R: float

    def __post_init__(self) -> None:
        if not self.evidence_policy_id or not self.evidence_policy_version:
            raise ValueError("EvidencePolicy requires an explicit evidence_policy_id and evidence_policy_version")
        if self.minimum_realized_trades <= 0:
            raise ValueError("minimum_realized_trades must be positive")
        if not (0.0 < self.confidence_level < 1.0):
            raise ValueError("confidence_level must be strictly between 0 and 1")
        if self.bootstrap_iterations <= 0:
            raise ValueError("bootstrap_iterations must be positive")
        if not (0.0 < self.development_fraction < 1.0):
            raise ValueError("development_fraction must be strictly between 0 and 1")
        if self.epsilon_R <= 0:
            raise ValueError("epsilon_R must be positive")


def default_evidence_policy() -> EvidencePolicy:
    """Phase 5.3 v1 research defaults (§10) — NOT empirically optimized
    truths. `minimum_realized_trades=30` and `confidence_level=0.90` are
    exactly the task's own suggested research defaults."""
    return EvidencePolicy(
        evidence_policy_id="EVIDENCE_POLICY",
        evidence_policy_version="v1",
        minimum_realized_trades=30,
        confidence_level=0.90,
        bootstrap_iterations=2000,
        bootstrap_seed=42,
        development_fraction=0.70,
        epsilon_R=1e-9,
    )
