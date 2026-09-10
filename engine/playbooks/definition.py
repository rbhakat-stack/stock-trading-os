"""PlaybookDefinition — the first-class, versioned domain model a setup is
defined by (§2 of the Phase 4 report). This is STATIC, code-defined,
version-controlled metadata — the "textbook description" of a playbook,
answering every one of the report's WHAT/WHY questions in plain English.
It is deliberately separate from PlaybookEvaluation (engine/playbooks/
evaluation.py), which is the RUNTIME result of checking one specific market
snapshot against a definition.

Persistence (§23): only `enabled` and `priority` are ever persisted
(supabase/migrations/0007_phase4_playbooks.sql) — the rule logic itself
stays code-defined/version-controlled here, never a generic JSON-driven
rules engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .taxonomy import PlaybookFamily, PlaybookId


@dataclass(frozen=True)
class QualityComponentConfig:
    """One component this playbook's quality score is built from (§10) —
    `max_points` values across a playbook's components must sum to 100."""
    code: str
    label: str
    max_points: int


@dataclass(frozen=True)
class PlaybookDefinition:
    playbook_id: str  # a PlaybookId value
    version: str  # e.g. "1.0" — see §5; bump, never silently mutate
    name: str
    family: str  # a PlaybookFamily value
    direction: str  # "LONG" | "SHORT"

    short_description: str
    plain_english_description: str
    when_it_works: str
    what_can_go_wrong: str

    supported_timeframes: tuple[str, ...]
    supported_regimes: tuple[str, ...]  # plain-English regime descriptions, e.g. "UPTREND_CONFIRMED"

    prerequisites: tuple[str, ...]  # plain-English; runtime check -> EvidenceItem list
    trigger_rules: tuple[str, ...]
    confirmation_rules: tuple[str, ...]
    disqualifiers: tuple[str, ...]
    entry_rules: tuple[str, ...]
    stop_rules: tuple[str, ...]
    target_rules: tuple[str, ...]
    management_rules: tuple[str, ...]

    quality_components: tuple[QualityComponentConfig, ...]

    required_evidence: tuple[str, ...]
    optional_evidence: tuple[str, ...]
    contra_evidence: tuple[str, ...]

    entry_type: str  # e.g. "RETEST_HOLD", "BREAKOUT_CLOSE" — see §11

    manual_review_notes: str = "MANUAL REVIEW REQUIRED — this system never places or recommends placing an order."
    statistical_validation_status: str = "NOT_YET_AVAILABLE"
    enabled: bool = True
    implementable: bool = True  # False = NOT_IMPLEMENTABLE_YET (§3) — no evaluator exists
    not_implementable_reason: str | None = None
    priority: int = 100  # explicit tie-break precedence (§17) — LOWER sorts first
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.implementable and self.not_implementable_reason is not None:
            raise ValueError(f"{self.playbook_id}: not_implementable_reason set on an implementable playbook")
        if not self.implementable and self.not_implementable_reason is None:
            raise ValueError(f"{self.playbook_id}: not_implementable_reason required when implementable=False")
        total_points = sum(c.max_points for c in self.quality_components)
        if self.implementable and total_points != 100:
            raise ValueError(f"{self.playbook_id}: quality_components must sum to 100 max points, got {total_points}")
