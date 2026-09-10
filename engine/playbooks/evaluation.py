"""PlaybookEvaluation — the RUNTIME result of checking one PlaybookDefinition
against one MarketIntelligenceSnapshot (§15 of the Phase 4 report). Pure
data; producing one is the job of engine/playbooks/evaluators/*.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.trade.candidate import TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.targets import TargetResult

from .evidence import Disqualifier, EvidenceItem, SoftConcern


@dataclass(frozen=True)
class QualityBreakdown:
    """Componentized, explainable quality score (§10) — display-only. The
    authoritative score/band fed into the Phase 3 decision/Decision-History
    pipeline is still engine.trade.quality.QualityScoreResult (unchanged);
    this breakdown is built from the SAME underlying signals so it can never
    numerically disagree with that score's inputs, but it is not itself a
    new gate and nothing reads it for enforcement."""
    components: tuple[tuple[str, str, int, int], ...]  # (code, label, earned_points, max_points)
    total_score: int
    total_max: int
    band: str  # HIGH | MODERATE | LOW


@dataclass(frozen=True)
class RankingMetadata:
    """Normalized fields (§16) sufficient to compare candidates ACROSS
    symbols/playbooks without any playbook-specific parsing — e.g. "NVDA
    Trend Pullback Long" vs "SPY Breakout Retest Long" vs "TSLA Failed
    Breakout Reversal Short." This is what a future cross-symbol scanner
    would sort/filter on."""
    symbol: str
    timeframe: str
    playbook_id: str
    playbook_version: str
    family: str
    direction: str
    setup_status: str
    quality_score: int | None
    quality_band: str | None
    hard_disqualifier_count: int
    soft_concern_count: int
    entry_price: float | None
    stop_price: float | None
    target1: float | None
    rr1: float | None
    data_quality_status: str  # "OK" | "DATA_QUALITY_FAILURE"


@dataclass(frozen=True)
class PlaybookEvaluation:
    playbook_id: str
    playbook_version: str
    name: str
    direction: str
    family: str

    eligibility_status: str  # an EligibilityStatus value
    setup_status: str  # a SetupStatus value

    prerequisites_satisfied: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    prerequisites_missing: tuple[EvidenceItem, ...] = field(default_factory=tuple)

    trigger_status: bool = False
    trigger_conditions_satisfied: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    trigger_conditions_missing: tuple[EvidenceItem, ...] = field(default_factory=tuple)

    disqualifiers: tuple[Disqualifier, ...] = field(default_factory=tuple)

    reasons_for: tuple[str, ...] = field(default_factory=tuple)
    soft_concerns: tuple[SoftConcern, ...] = field(default_factory=tuple)

    quality_score: int | None = None
    quality_band: str | None = None
    quality_breakdown: QualityBreakdown | None = None

    # The underlying Phase 3 candidate this evaluation wraps — None when
    # NOT_ELIGIBLE/NOT_IMPLEMENTABLE_YET, since no candidate was ever formed.
    # This is what gets handed to engine.trade.planner.build_trade_plan
    # unchanged — see the package docstring's architectural boundary.
    candidate: TradeCandidate | None = None
    invalidation: InvalidationResult | None = None
    targets: tuple[TargetResult, ...] = field(default_factory=tuple)

    entry_price: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    entry_type: str | None = None
    entry_explanation: str = ""

    manual_review_notes: str = ""
    statistical_validation_status: str = "NOT_YET_AVAILABLE"

    ranking: RankingMetadata | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def stop_price(self) -> float | None:
        return self.invalidation.stop_price if self.invalidation else None

    @property
    def invalidation_reason(self) -> str | None:
        return self.invalidation.reason if self.invalidation else None

    @property
    def target1(self) -> float | None:
        return self.targets[0].price if self.targets else None

    @property
    def target1_source(self) -> str | None:
        return self.targets[0].reason if self.targets else None

    @property
    def target2(self) -> float | None:
        return self.targets[1].price if len(self.targets) > 1 else None

    @property
    def target2_source(self) -> str | None:
        return self.targets[1].reason if len(self.targets) > 1 else None

    @property
    def rr1(self) -> float | None:
        return self.targets[0].r_multiple if self.targets else None

    @property
    def hard_disqualifier_count(self) -> int:
        return len(self.disqualifiers)

    @property
    def soft_concern_count(self) -> int:
        return len(self.soft_concerns)
