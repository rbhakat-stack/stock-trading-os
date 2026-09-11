"""Phase 5.3 — pure statistical evidence engine.

Aggregates Phase 5.2 `HistoricalTradeOutcome` records into `PlaybookStatistics`
— deterministic, reproducible, conservative. Answers "does this playbook have
evidence?" (research evidence, NOT a live-trading approval — see this
module's own final report for that boundary).

ARCHITECTURAL INVARIANTS:
  - Denominator/population rules come ONLY from `engine.backtest.
    statistics_policy` (Phase 5.2V) — never re-invented here (§1).
  - `cost_policy` (Phase 5.2V `TransactionCostPolicy`) is a LABEL/pass-
    through in Phase 5.3 v1: it stamps `cost_policy_id`/`cost_policy_version`
    onto every result and documents which cost assumption `net_R` already
    reflects (every current `HistoricalTradeOutcome.net_R` was computed
    under ZERO_COST_RESEARCH at Phase 5.2 simulation time — this module
    never recomputes or re-derives net_R itself).
  - Pure Python + numpy only: no network, no database, no Streamlit, no
    account state, no Alpaca calls (§26).
  - This is MEASUREMENT, not optimization — no threshold here is tuned
    against real-data results (§11).
"""
from __future__ import annotations

import hashlib
import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from .contracts import ValidationStatus
from .cost_policy import TransactionCostPolicy
from .evidence_policy import EvidencePolicy
from .outcomes import HistoricalTradeOutcome
from .statistics_policy import (
    StatisticalPopulationPolicy, is_fill_rate_eligible, is_filled, is_realized_trade, is_valid_occurrence,
)

class DuplicateOccurrenceError(ValueError):
    """§24 — no occurrence_id may appear twice in one statistical run. Fails
    closed rather than silently double-counting."""


class HeterogeneousOutcomeSetError(ValueError):
    """A statistics call must receive outcomes for exactly ONE (playbook_id,
    playbook_version, direction, timeframe, execution_policy_version) —
    mixing them would silently blend incompatible evidence. Filtering to a
    single segment is the ORCHESTRATOR's job (`select_outcomes_for_segment`
    below), never something this pure function guesses at."""


class InvalidRealizedOutcomeError(ValueError):
    """§30 — 'NaN/invalid numeric values fail closed appropriately'. A
    REALIZED trade (entry_status==FILLED, a genuine exit) with a missing or
    non-finite gross_R/net_R can never be silently included in — or
    silently excluded from — a mean/win-rate/bootstrap computation; this
    module refuses to guess which, and fails closed instead."""


# =============================================================================
# §25 — the result contract (nested dataclasses; "cleaner" per the task)
# =============================================================================


@dataclass(frozen=True)
class CountBreakdown:
    occurrence_count: int
    fill_rate_eligible_count: int
    filled_count: int
    realized_trade_count: int
    target_count: int
    stop_count: int
    expired_eod_count: int
    timeout_count: int
    expired_unfilled_count: int
    invalidated_before_fill_count: int
    end_of_data_count: int
    invalid_geometry_count: int
    other_excluded_count: int
    fill_rate: float | None
    realized_completion_rate: float | None  # realized_trade_count / filled_count


@dataclass(frozen=True)
class CoreStatistics:
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: float | None
    loss_rate: float | None
    breakeven_rate: float | None
    average_R: float | None
    median_R: float | None
    standard_deviation_R: float | None
    average_win_R: float | None
    median_win_R: float | None
    average_loss_R: float | None
    median_loss_R: float | None
    largest_win_R: float | None
    largest_loss_R: float | None
    gross_expectancy_R: float | None
    net_expectancy_R: float | None
    profit_factor: float | str | None
    average_MFE_R: float | None
    median_MFE_R: float | None
    average_MAE_R: float | None
    median_MAE_R: float | None
    average_holding_bars: float | None
    median_holding_bars: float | None
    average_holding_minutes: float | None
    median_holding_minutes: float | None


@dataclass(frozen=True)
class DrawdownResult:
    """STRATEGY OUTCOME DRAWDOWN IN R — a drawdown in the cumulative sum of
    realized net_R values, in deterministic chronological trade order. This
    is NOT portfolio dollar drawdown (no position sizing/account state is
    involved anywhere in Phase 5)."""
    max_drawdown_R: float | None
    drawdown_peak_timestamp: datetime | None
    drawdown_trough_timestamp: datetime | None


@dataclass(frozen=True)
class BootstrapResult:
    bootstrap_lower_R: float | None
    bootstrap_upper_R: float | None
    confidence_level: float
    bootstrap_iterations: int
    bootstrap_seed: int


@dataclass(frozen=True)
class SymbolConcentration:
    unique_symbol_count: int
    occurrence_count_by_symbol: dict[str, int]
    realized_trade_count_by_symbol: dict[str, int]
    largest_symbol_share_of_realized_trades: float | None
    top_3_symbol_share: float | None


@dataclass(frozen=True)
class TemporalConcentration:
    earliest_trade_date: str | None
    latest_trade_date: str | None
    trade_count_by_year: dict[int, int]
    largest_period_share: float | None


@dataclass(frozen=True)
class SplitMetrics:
    """One half (development OR validation) of the chronological split (§16)."""
    realized_trade_count: int
    expectancy_R: float | None
    bootstrap_lower_R: float | None
    bootstrap_upper_R: float | None
    evidence_class: str


@dataclass(frozen=True)
class DevelopmentValidationSplit:
    development: SplitMetrics
    validation: SplitMetrics
    development_fraction: float


@dataclass(frozen=True)
class UniverseMetadata:
    """§21 — survivorship-bias disclosure. `survivorship_bias_status`
    defaults to "UNKNOWN" — a caller must EXPLICITLY assert
    "SURVIVORSHIP_BIAS_PRESENT" (e.g. a present-day curated liquid-stock
    list applied to historical data) or "NOT_APPLICABLE" (e.g. a
    fixed-membership index snapshot); this module never infers it."""
    universe_definition: str | None = None
    universe_method: str | None = None
    universe_as_of: str | None = None
    survivorship_bias_status: str = "UNKNOWN"


@dataclass(frozen=True)
class QualityBandDiagnostics:
    """§20 — DESCRIPTIVE / EXPLORATORY ONLY. Never used to change Phase 4
    quality cutoffs or to gate evidence_class."""
    count_by_band: dict[str, int]
    average_R_by_band: dict[str, float]
    median_R_by_band: dict[str, float]


@dataclass(frozen=True)
class ReproducibilityMetadata:
    statistics_run_id: str
    created_at: datetime
    data_start: datetime | None
    data_end: datetime | None
    input_outcome_count: int
    input_fingerprint: str


@dataclass(frozen=True)
class PlaybookStatistics:
    playbook_id: str
    playbook_version: str
    family: str
    direction: str
    timeframe: str
    segment_type: str  # "POOLED" | "MARKET_REGIME" | "VOLATILITY_REGIME"
    segment_value: str | None

    execution_policy_version: str
    population_policy_version: str
    cost_policy_id: str
    cost_policy_version: str
    evidence_policy_id: str
    evidence_policy_version: str

    counts: CountBreakdown
    core: CoreStatistics
    drawdown: DrawdownResult
    bootstrap: BootstrapResult
    evidence_class: str

    symbol_concentration: SymbolConcentration
    temporal_concentration: TemporalConcentration
    development_validation: DevelopmentValidationSplit | None

    universe: UniverseMetadata
    reproducibility: ReproducibilityMetadata
    quality_band_diagnostics: QualityBandDiagnostics | None = None


# =============================================================================
# internal pure helpers
# =============================================================================


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return float(np.std(values, ddof=1))


def _classify_realized(net_R: float, epsilon: float) -> str:
    if net_R > epsilon:
        return "WIN"
    if net_R < -epsilon:
        return "LOSS"
    return "BREAKEVEN"


def _profit_factor(net_Rs: list[float]) -> float | str | None:
    """§6 — a clear, machine-readable representation, never a raw inf.
    - no realized trades, or all exactly breakeven: None (undefined ratio).
    - wins exist, zero losses: "INFINITE_NO_LOSSES" (mathematically infinite).
    - zero wins, losses (and/or breakevens) exist: 0.0 (a real, meaningful zero).
    - otherwise: sum(positive)/abs(sum(negative))."""
    if not net_Rs:
        return None
    positive_sum = sum(r for r in net_Rs if r > 0)
    negative_sum = sum(r for r in net_Rs if r < 0)
    if positive_sum == 0 and negative_sum == 0:
        return None
    if negative_sum == 0:
        return "INFINITE_NO_LOSSES"
    if positive_sum == 0:
        return 0.0
    return positive_sum / abs(negative_sum)


def _sort_key(o: HistoricalTradeOutcome):
    return (o.exit_timestamp, o.trigger_timestamp, o.symbol, o.occurrence_id)


def _deterministic_chronological(realized: list[HistoricalTradeOutcome]) -> list[HistoricalTradeOutcome]:
    """§7 — deterministic chronological order: exit_timestamp, then
    trigger_timestamp, then symbol, then occurrence_id. Never reordered by
    performance."""
    return sorted(realized, key=_sort_key)


def _max_drawdown(realized_sorted: list[HistoricalTradeOutcome]) -> DrawdownResult:
    if not realized_sorted:
        return DrawdownResult(max_drawdown_R=None, drawdown_peak_timestamp=None, drawdown_trough_timestamp=None)
    cumulative = 0.0
    peak = 0.0
    peak_ts = realized_sorted[0].exit_timestamp
    max_dd = 0.0
    dd_peak_ts = peak_ts
    dd_trough_ts = peak_ts
    for o in realized_sorted:
        cumulative += o.net_R
        if cumulative > peak:
            peak = cumulative
            peak_ts = o.exit_timestamp
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown
            dd_peak_ts = peak_ts
            dd_trough_ts = o.exit_timestamp
    return DrawdownResult(max_drawdown_R=max_dd, drawdown_peak_timestamp=dd_peak_ts, drawdown_trough_timestamp=dd_trough_ts)


def bootstrap_confidence_interval(
    values: list[float], confidence_level: float, iterations: int, seed: int,
) -> tuple[float | None, float | None]:
    """§8 — deterministic seeded bootstrap of the MEAN. No normality
    assumption. Vectorized (one `rng.integers` call for the whole resample
    matrix) so it stays fast at 100k+ input records. Same inputs + seed +
    iterations => bit-identical bounds, always."""
    if not values:
        return None, None
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(iterations, n))
    means = arr[idx].mean(axis=1)
    alpha = 1.0 - confidence_level
    lower = float(np.percentile(means, 100 * alpha / 2))
    upper = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lower, upper


def _classify_evidence(realized_count: int, bootstrap_lower: float | None, min_trades: int) -> str:
    """§9 — exactly the four ValidationStatus classes. Mean expectancy > 0
    alone is NEVER sufficient — POSITIVE_EDGE requires the bootstrap LOWER
    bound itself to be > 0."""
    if realized_count == 0:
        return ValidationStatus.NOT_TESTED.value
    if realized_count < min_trades:
        return ValidationStatus.INSUFFICIENT_SAMPLE.value
    if bootstrap_lower is not None and bootstrap_lower > 0:
        return ValidationStatus.POSITIVE_EDGE.value
    return ValidationStatus.NO_EDGE.value


def _split_metrics(realized_sorted: list[HistoricalTradeOutcome], evidence_policy: EvidencePolicy) -> SplitMetrics:
    net_Rs = [o.net_R for o in realized_sorted]
    lower, upper = bootstrap_confidence_interval(
        net_Rs, evidence_policy.confidence_level, evidence_policy.bootstrap_iterations, evidence_policy.bootstrap_seed,
    )
    return SplitMetrics(
        realized_trade_count=len(realized_sorted), expectancy_R=_mean(net_Rs),
        bootstrap_lower_R=lower, bootstrap_upper_R=upper,
        evidence_class=_classify_evidence(len(realized_sorted), lower, evidence_policy.minimum_realized_trades),
    )


def _development_validation_split(
    realized_sorted: list[HistoricalTradeOutcome], evidence_policy: EvidencePolicy,
) -> DevelopmentValidationSplit:
    """§16 — earliest `development_fraction` chronologically = DEVELOPMENT,
    the rest = VALIDATION. Never shuffled; deterministic tie-break via the
    same `_sort_key` used everywhere else in this module."""
    n = len(realized_sorted)
    split_idx = int(n * evidence_policy.development_fraction)
    development = realized_sorted[:split_idx]
    validation = realized_sorted[split_idx:]
    return DevelopmentValidationSplit(
        development=_split_metrics(development, evidence_policy),
        validation=_split_metrics(validation, evidence_policy),
        development_fraction=evidence_policy.development_fraction,
    )


_FINGERPRINT_FIELDS = (
    "occurrence_id", "symbol", "timeframe", "playbook_id", "playbook_version", "direction",
    "trigger_timestamp", "entry_status", "entry_timestamp", "entry_price", "exit_status", "exit_timestamp",
    "exit_price", "gross_R", "net_R",
)


def compute_input_fingerprint(outcomes: list[HistoricalTradeOutcome]) -> str:
    """§28 — deterministic sha256 over exactly `_FINGERPRINT_FIELDS`, for
    outcomes ordered by `occurrence_id` (order-independent of the caller's
    own list order — two callers passing the same set in different order
    get the identical fingerprint). No object memory addresses, no
    dict/set iteration-order noise."""
    ordered = sorted(outcomes, key=lambda o: o.occurrence_id)
    parts = []
    for o in ordered:
        row = "|".join(str(getattr(o, f)) for f in _FINGERPRINT_FIELDS)
        parts.append(row)
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def select_outcomes_for_segment(
    outcomes: list[HistoricalTradeOutcome], playbook_id: str, playbook_version: str, direction: str,
    timeframe: str, execution_policy_version: str, segment_type: str = "POOLED", segment_value: str | None = None,
) -> list[HistoricalTradeOutcome]:
    """Orchestration helper: filters a raw outcome pool down to exactly one
    (playbook_id, playbook_version, direction, timeframe,
    execution_policy_version) [+ optional regime segment] group —
    `compute_playbook_statistics` itself never filters; it only validates
    homogeneity (§ HeterogeneousOutcomeSetError)."""
    matches = [
        o for o in outcomes
        if o.playbook_id == playbook_id and o.playbook_version == playbook_version and o.direction == direction
        and o.timeframe == timeframe and o.execution_policy_version == execution_policy_version
    ]
    if segment_type == "MARKET_REGIME":
        matches = [o for o in matches if o.market_regime_at_trigger == segment_value]
    elif segment_type == "VOLATILITY_REGIME":
        matches = [o for o in matches if o.volatility_regime_at_trigger == segment_value]
    elif segment_type != "POOLED":
        raise ValueError(f"unknown segment_type: {segment_type!r}")
    return matches


# =============================================================================
# §26 — the pure statistics engine entry point
# =============================================================================


def compute_playbook_statistics(
    outcomes: list[HistoricalTradeOutcome],
    playbook_id: str,
    playbook_version: str,
    family: str,
    direction: str,
    timeframe: str,
    execution_policy_version: str,
    population_policy: StatisticalPopulationPolicy,
    evidence_policy: EvidencePolicy,
    cost_policy: TransactionCostPolicy,
    segment_type: str = "POOLED",
    segment_value: str | None = None,
    universe_metadata: UniverseMetadata | None = None,
    run_id: str | None = None,
    created_at: datetime | None = None,
    compute_development_validation: bool = True,
    compute_quality_band_diagnostics: bool = False,
) -> PlaybookStatistics:
    """Pure, deterministic: no network, no database, no Streamlit, no
    account state, no Alpaca calls. `outcomes` must ALREADY be the exact
    set for one (playbook_id, playbook_version, direction, timeframe,
    execution_policy_version) [+ segment] — use `select_outcomes_for_segment`
    to build it; this function only validates homogeneity, never filters.
    An empty `outcomes` list is valid input (=> NOT_TESTED evidence).
    """
    if population_policy.version != "v1":
        raise ValueError(
            f"compute_playbook_statistics implements StatisticalPopulationPolicy v1 semantics only; "
            f"got version {population_policy.version!r}"
        )

    seen_ids: set[str] = set()
    for o in outcomes:
        if o.occurrence_id in seen_ids:
            raise DuplicateOccurrenceError(f"duplicate occurrence_id in statistics input: {o.occurrence_id!r}")
        seen_ids.add(o.occurrence_id)
        if (
            o.playbook_id != playbook_id or o.playbook_version != playbook_version or o.direction != direction
            or o.timeframe != timeframe or o.execution_policy_version != execution_policy_version
        ):
            raise HeterogeneousOutcomeSetError(
                f"outcome {o.occurrence_id!r} does not match the declared segment "
                f"({playbook_id}/{playbook_version}/{direction}/{timeframe}/{execution_policy_version}) — "
                "filter with select_outcomes_for_segment before calling compute_playbook_statistics"
            )
        if is_realized_trade(o):
            if o.gross_R is None or o.net_R is None or not math.isfinite(o.gross_R) or not math.isfinite(o.net_R):
                raise InvalidRealizedOutcomeError(
                    f"realized outcome {o.occurrence_id!r} has a missing or non-finite gross_R/net_R "
                    f"(gross_R={o.gross_R!r}, net_R={o.net_R!r}) — refusing to silently include or exclude it"
                )

    valid_occurrences = [o for o in outcomes if is_valid_occurrence(o)]
    fill_eligible = [o for o in outcomes if is_fill_rate_eligible(o)]
    filled = [o for o in outcomes if is_filled(o)]
    realized = [o for o in outcomes if is_realized_trade(o)]

    exit_counts = Counter(o.exit_status for o in filled)
    target_count = exit_counts.get("TARGET1", 0)
    stop_count = exit_counts.get("STOP", 0)
    expired_eod_count = exit_counts.get("EXPIRED_EOD", 0)
    timeout_count = exit_counts.get("TIMEOUT", 0)
    end_of_data_count = exit_counts.get("END_OF_DATA", 0)

    entry_counts = Counter(o.entry_status for o in outcomes)
    expired_unfilled_count = entry_counts.get("EXPIRED_UNFILLED", 0)
    invalidated_before_fill_count = entry_counts.get("INVALIDATED_BEFORE_FILL", 0)
    invalid_geometry_count = entry_counts.get("INVALID_STOP_GEOMETRY", 0)

    accounted_filled_exits = target_count + stop_count + expired_eod_count + timeout_count + end_of_data_count
    other_excluded_count = max(0, len(filled) - accounted_filled_exits)

    fill_rate = (len(filled) / len(fill_eligible)) if fill_eligible else None
    realized_completion_rate = (len(realized) / len(filled)) if filled else None

    counts = CountBreakdown(
        occurrence_count=len(valid_occurrences), fill_rate_eligible_count=len(fill_eligible),
        filled_count=len(filled), realized_trade_count=len(realized), target_count=target_count,
        stop_count=stop_count, expired_eod_count=expired_eod_count, timeout_count=timeout_count,
        expired_unfilled_count=expired_unfilled_count, invalidated_before_fill_count=invalidated_before_fill_count,
        end_of_data_count=end_of_data_count, invalid_geometry_count=invalid_geometry_count,
        other_excluded_count=other_excluded_count, fill_rate=fill_rate, realized_completion_rate=realized_completion_rate,
    )

    realized_sorted = _deterministic_chronological(realized)
    net_Rs = [o.net_R for o in realized_sorted]
    gross_Rs = [o.gross_R for o in realized_sorted]
    classes = [_classify_realized(r, evidence_policy.epsilon_R) for r in net_Rs]
    wins = [r for r, c in zip(net_Rs, classes) if c == "WIN"]
    losses = [r for r, c in zip(net_Rs, classes) if c == "LOSS"]
    breakevens = [r for r, c in zip(net_Rs, classes) if c == "BREAKEVEN"]
    mfes = [o.mfe_R for o in realized_sorted if o.mfe_R is not None]
    maes = [o.mae_R for o in realized_sorted if o.mae_R is not None]
    holding_bars = [o.holding_period_bars for o in realized_sorted if o.holding_period_bars is not None]
    holding_minutes = [o.holding_period_minutes for o in realized_sorted if o.holding_period_minutes is not None]

    n_realized = len(realized_sorted)
    core = CoreStatistics(
        win_count=len(wins), loss_count=len(losses), breakeven_count=len(breakevens),
        win_rate=(len(wins) / n_realized) if n_realized else None,
        loss_rate=(len(losses) / n_realized) if n_realized else None,
        breakeven_rate=(len(breakevens) / n_realized) if n_realized else None,
        average_R=_mean(net_Rs), median_R=_median(net_Rs), standard_deviation_R=_stdev(net_Rs),
        average_win_R=_mean(wins), median_win_R=_median(wins),
        average_loss_R=_mean(losses), median_loss_R=_median(losses),
        largest_win_R=max(wins) if wins else None, largest_loss_R=min(losses) if losses else None,
        gross_expectancy_R=_mean(gross_Rs), net_expectancy_R=_mean(net_Rs),
        profit_factor=_profit_factor(net_Rs),
        average_MFE_R=_mean(mfes), median_MFE_R=_median(mfes),
        average_MAE_R=_mean(maes), median_MAE_R=_median(maes),
        average_holding_bars=_mean(holding_bars), median_holding_bars=_median(holding_bars),
        average_holding_minutes=_mean(holding_minutes), median_holding_minutes=_median(holding_minutes),
    )

    drawdown = _max_drawdown(realized_sorted)
    bootstrap_lower, bootstrap_upper = bootstrap_confidence_interval(
        net_Rs, evidence_policy.confidence_level, evidence_policy.bootstrap_iterations, evidence_policy.bootstrap_seed,
    )
    bootstrap = BootstrapResult(
        bootstrap_lower_R=bootstrap_lower, bootstrap_upper_R=bootstrap_upper,
        confidence_level=evidence_policy.confidence_level, bootstrap_iterations=evidence_policy.bootstrap_iterations,
        bootstrap_seed=evidence_policy.bootstrap_seed,
    )
    evidence_class = _classify_evidence(n_realized, bootstrap_lower, evidence_policy.minimum_realized_trades)

    occ_by_symbol = Counter(o.symbol for o in valid_occurrences)
    realized_by_symbol = Counter(o.symbol for o in realized_sorted)
    largest_share = (max(realized_by_symbol.values()) / n_realized) if n_realized and realized_by_symbol else None
    top3_share = (
        sum(sorted(realized_by_symbol.values(), reverse=True)[:3]) / n_realized
        if n_realized and realized_by_symbol else None
    )
    symbol_concentration = SymbolConcentration(
        unique_symbol_count=len(occ_by_symbol), occurrence_count_by_symbol=dict(occ_by_symbol),
        realized_trade_count_by_symbol=dict(realized_by_symbol),
        largest_symbol_share_of_realized_trades=largest_share, top_3_symbol_share=top3_share,
    )

    trigger_dates = [o.trigger_timestamp for o in valid_occurrences]
    year_counts = Counter(o.trigger_timestamp.year for o in realized_sorted)
    largest_period_share = (max(year_counts.values()) / n_realized) if n_realized and year_counts else None
    temporal_concentration = TemporalConcentration(
        earliest_trade_date=min(trigger_dates).date().isoformat() if trigger_dates else None,
        latest_trade_date=max(trigger_dates).date().isoformat() if trigger_dates else None,
        trade_count_by_year=dict(year_counts), largest_period_share=largest_period_share,
    )

    dev_val = _development_validation_split(realized_sorted, evidence_policy) if compute_development_validation else None

    quality_diag = None
    if compute_quality_band_diagnostics:
        by_band: dict[str, list[float]] = {}
        for o in realized_sorted:
            band = o.quality_score_at_trigger
            if band is None:
                continue
            by_band.setdefault(str(band), []).append(o.net_R)
        quality_diag = QualityBandDiagnostics(
            count_by_band={b: len(v) for b, v in by_band.items()},
            average_R_by_band={b: _mean(v) for b, v in by_band.items()},
            median_R_by_band={b: _median(v) for b, v in by_band.items()},
        )

    repro = ReproducibilityMetadata(
        statistics_run_id=run_id or str(uuid.uuid4()),
        created_at=created_at or datetime.now(timezone.utc),
        data_start=min((o.trigger_timestamp for o in valid_occurrences), default=None),
        data_end=max((o.trigger_timestamp for o in valid_occurrences), default=None),
        input_outcome_count=len(outcomes),
        input_fingerprint=compute_input_fingerprint(outcomes),
    )

    return PlaybookStatistics(
        playbook_id=playbook_id, playbook_version=playbook_version, family=family, direction=direction,
        timeframe=timeframe, segment_type=segment_type, segment_value=segment_value,
        execution_policy_version=execution_policy_version, population_policy_version=population_policy.version,
        cost_policy_id=cost_policy.cost_policy_id, cost_policy_version=cost_policy.cost_policy_version,
        evidence_policy_id=evidence_policy.evidence_policy_id, evidence_policy_version=evidence_policy.evidence_policy_version,
        counts=counts, core=core, drawdown=drawdown, bootstrap=bootstrap, evidence_class=evidence_class,
        symbol_concentration=symbol_concentration, temporal_concentration=temporal_concentration,
        development_validation=dev_val, universe=universe_metadata or UniverseMetadata(),
        reproducibility=repro, quality_band_diagnostics=quality_diag,
    )


# =============================================================================
# §12 — segmented orchestration (POOLED + MARKET_REGIME + VOLATILITY_REGIME)
# =============================================================================


def compute_segmented_statistics(
    outcomes: list[HistoricalTradeOutcome],
    playbook_id: str,
    playbook_version: str,
    family: str,
    direction: str,
    timeframe: str,
    execution_policy_version: str,
    population_policy: StatisticalPopulationPolicy,
    evidence_policy: EvidencePolicy,
    cost_policy: TransactionCostPolicy,
    universe_metadata: UniverseMetadata | None = None,
    run_id: str | None = None,
    created_at: datetime | None = None,
) -> list[PlaybookStatistics]:
    """§12 — POOLED baseline + one result per observed market_regime_at_
    trigger + one per observed volatility_regime_at_trigger. Deliberately
    NOT a deep combinatorial cross-product (§12: no regime x volatility x
    symbol x month x quality-band fragmentation)."""
    base = select_outcomes_for_segment(
        outcomes, playbook_id, playbook_version, direction, timeframe, execution_policy_version,
    )
    results = [
        compute_playbook_statistics(
            base, playbook_id, playbook_version, family, direction, timeframe, execution_policy_version,
            population_policy, evidence_policy, cost_policy, segment_type="POOLED", segment_value=None,
            universe_metadata=universe_metadata, run_id=run_id, created_at=created_at,
        )
    ]

    market_regimes = sorted({o.market_regime_at_trigger for o in base if o.market_regime_at_trigger is not None})
    for regime in market_regimes:
        segment = [o for o in base if o.market_regime_at_trigger == regime]
        results.append(compute_playbook_statistics(
            segment, playbook_id, playbook_version, family, direction, timeframe, execution_policy_version,
            population_policy, evidence_policy, cost_policy, segment_type="MARKET_REGIME", segment_value=regime,
            universe_metadata=universe_metadata, run_id=run_id, created_at=created_at,
        ))

    vol_regimes = sorted({o.volatility_regime_at_trigger for o in base if o.volatility_regime_at_trigger is not None})
    for regime in vol_regimes:
        segment = [o for o in base if o.volatility_regime_at_trigger == regime]
        results.append(compute_playbook_statistics(
            segment, playbook_id, playbook_version, family, direction, timeframe, execution_policy_version,
            population_policy, evidence_policy, cost_policy, segment_type="VOLATILITY_REGIME", segment_value=regime,
            universe_metadata=universe_metadata, run_id=run_id, created_at=created_at,
        ))

    return results
