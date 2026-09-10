"""Forward-looking data contracts (Phase 5.0 §8, §9, §10) — SHAPES only.

Nothing in this module computes a backtest, an outcome, or a statistic.
These dataclasses exist so that the future Phase 5.1/5.2/5.3 implementation
and the future Phase 6 recommendation engine can be built against a STABLE
contract from day one, without Phase 6 ever needing to understand how
historical replay works internally (§9's explicit goal).

§8 — PLAYBOOK VERSION SEMANTICS (important correction from Phase 5.0's own
brief): a backtest run explicitly PINS one or more playbook_id/version
pairs. A historical market date NEVER silently selects which playbook
version applies — `BacktestRunSpec.playbook_id`/`playbook_version` are
required fields with no "infer from date" fallback anywhere in this module
or elsewhere in the codebase.

§10 — POOLED / CROSS-SYMBOL STATISTICS: `StatisticalValidationResult.symbol`
is deliberately OPTIONAL (`None` = evidence pooled across the backtested
symbol universe; a concrete value = symbol-specific evidence as one
additional segmentation). Symbol-specific evidence must never be the ONLY
representable shape, since a single symbol's historical sample size may be
too small to be meaningful on its own — see the Phase 5 design report §10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ValidationStatus(str, Enum):
    """§18-G's approved minimum taxonomy — thresholds that decide between
    these are a VERSIONED POLICY (`validation_policy_version` below), never
    a hardcoded, unversioned magic number. POSITIVE_EDGE is explicitly NOT
    the same thing as "automatically recommend" — that gate belongs to a
    future Phase 6 policy, not to this taxonomy."""
    NOT_TESTED = "NOT_TESTED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NO_EDGE = "NO_EDGE"
    POSITIVE_EDGE = "POSITIVE_EDGE"


class RunDataMode(str, Enum):
    """Mirrors engine.backtest.bar_source.RunMode — repeated here as the
    field a persisted/reproducible run record carries, so a stored run's
    intent (real evidence vs. exercising the engine on synthetic data) is
    never ambiguous after the fact."""
    PRODUCTION = "PRODUCTION"
    TEST_SYNTHETIC = "TEST_SYNTHETIC"


@dataclass(frozen=True)
class BacktestRunSpec:
    """§8 — what one backtest run explicitly pins. NOT implemented/executed
    by anything yet (Phase 5.1); this is the reproducibility-relevant SHAPE
    a run request/record must have once replay exists. Mirrors the
    Phase 5 design report's §24 reproducibility field list.
    """
    playbook_id: str
    playbook_version: str                 # REQUIRED — never inferred from date_range
    symbol_universe: tuple[str, ...]       # may be a single symbol or many; see §10
    timeframe: str
    date_range_start: datetime
    date_range_end: datetime
    data_mode: RunDataMode
    execution_assumptions: dict = field(default_factory=dict)
    transaction_cost_assumptions: dict = field(default_factory=dict)
    parameter_configuration: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.playbook_id or not self.playbook_version:
            raise ValueError("BacktestRunSpec requires an explicit playbook_id and playbook_version")
        if not self.symbol_universe:
            raise ValueError("BacktestRunSpec requires at least one symbol")
        if self.date_range_end <= self.date_range_start:
            raise ValueError("date_range_end must be after date_range_start")


@dataclass(frozen=True)
class StatisticalValidationResult:
    """§9 — the stable, candidate-facing contract a future Phase 6 will
    query: "give me the valid statistical evidence for this current
    PlaybookEvaluation." NOT computed by anything in Phase 5.0 — this is
    the return SHAPE only. Every field the task's brief lists is present.

    `symbol` is optional — see the module docstring's §10 note. `run_id` is
    a plain str here (not a DB foreign key type) so this module stays
    persistence-agnostic, consistent with engine/ never importing Supabase.
    """
    playbook_id: str
    playbook_version: str
    direction: str                         # "LONG" | "SHORT" — never suppressed, see §27 of the design report
    timeframe: str
    market_regime: str | None              # a MarketState value, or None if this evidence is regime-pooled
    volatility_regime: str | None          # a VolatilityLevel value, or None if volatility-pooled

    sample_size: int
    expectancy_R: float | None
    expectancy_R_ci_low: float | None
    expectancy_R_ci_high: float | None
    win_rate: float | None
    average_win_R: float | None
    average_loss_R: float | None

    validation_status: str                 # a ValidationStatus value
    validation_policy_version: str

    data_period_start: datetime
    data_period_end: datetime
    run_id: str

    symbol: str | None = None              # None = pooled across the backtested universe; see §10
