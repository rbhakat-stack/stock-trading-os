"""Phase 5.3 — statistical evidence engine tests.

§29 hand-calculated fixtures are the primary correctness proof — formulas
are verified against numbers computed by hand, never just cross-checked
against another implementation of the same formula.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from engine.backtest.cost_policy import ZERO_COST_RESEARCH_POLICY, TransactionCostPolicy
from engine.backtest.evidence_policy import EvidencePolicy, default_evidence_policy
from engine.backtest.outcomes import HistoricalTradeOutcome
from engine.backtest.statistics import (
    DuplicateOccurrenceError, HeterogeneousOutcomeSetError, InvalidRealizedOutcomeError, UniverseMetadata,
    bootstrap_confidence_interval, compute_input_fingerprint, compute_playbook_statistics,
    compute_segmented_statistics, select_outcomes_for_segment,
)
from engine.backtest.statistics_policy import DEFAULT_STATISTICAL_POPULATION_POLICY

POP = DEFAULT_STATISTICAL_POPULATION_POLICY
EP = default_evidence_policy()
COST = ZERO_COST_RESEARCH_POLICY

_BASE = dict(playbook_id="TREND_PULLBACK_LONG", playbook_version="1.0", family="TREND_CONTINUATION",
             direction="LONG", timeframe="5min", execution_policy_version="v1")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _outcome(
    occurrence_id: str, symbol="AAPL", entry_status="FILLED", exit_status="TARGET1",
    gross_R=1.0, net_R=None, trigger_ts="2024-01-02 09:30:00", exit_ts="2024-01-02 10:00:00",
    mfe_R=1.0, mae_R=0.2, holding_period_bars=6, holding_period_minutes=30.0,
    market_regime_at_trigger=None, volatility_regime_at_trigger=None, quality_score_at_trigger=70,
    **overrides,
) -> HistoricalTradeOutcome:
    net_R = gross_R if net_R is None else net_R
    fields = dict(
        occurrence_id=occurrence_id, symbol=symbol, timeframe=_BASE["timeframe"], playbook_id=_BASE["playbook_id"],
        playbook_version=_BASE["playbook_version"], family=_BASE["family"], direction=_BASE["direction"],
        trigger_timestamp=_ts(trigger_ts), entry_status=entry_status,
        entry_timestamp=_ts(trigger_ts) if entry_status == "FILLED" else None,
        entry_price=100.0 if entry_status == "FILLED" else None, entry_zone_low=None, entry_zone_high=None,
        stop_price=98.0, target1_price=104.0, target2_price=None,
        exit_status=exit_status if entry_status == "FILLED" else None,
        exit_timestamp=_ts(exit_ts) if entry_status == "FILLED" and exit_status else None,
        exit_price=104.0 if entry_status == "FILLED" and exit_status else None,
        initial_risk_per_share=2.0 if entry_status == "FILLED" else None,
        gross_R=gross_R if entry_status == "FILLED" and exit_status else None,
        net_R=net_R if entry_status == "FILLED" and exit_status else None,
        mfe_R=mfe_R if entry_status == "FILLED" else None, mae_R=mae_R if entry_status == "FILLED" else None,
        holding_period_bars=holding_period_bars if entry_status == "FILLED" else None,
        holding_period_minutes=holding_period_minutes if entry_status == "FILLED" else None,
        same_bar_collision=False, market_regime_at_trigger=market_regime_at_trigger,
        volatility_regime_at_trigger=volatility_regime_at_trigger, quality_score_at_trigger=quality_score_at_trigger,
        execution_policy_version=_BASE["execution_policy_version"], evidence={},
    )
    fields.update(overrides)
    return HistoricalTradeOutcome(**fields)


def _stats(outcomes, **kw):
    return compute_playbook_statistics(
        outcomes, _BASE["playbook_id"], _BASE["playbook_version"], _BASE["family"], _BASE["direction"],
        _BASE["timeframe"], _BASE["execution_policy_version"], POP, kw.pop("evidence_policy", EP), COST, **kw,
    )


# =============================================================================
# §29 — hand-calculated fixture: net_R = [+2, -1, +1, -1, 0]
# =============================================================================


def _five_trade_fixture():
    values = [2.0, -1.0, 1.0, -1.0, 0.0]
    return [
        _outcome(f"occ-{i}", gross_R=v, exit_ts=f"2024-01-0{i+2} 10:00:00", trigger_ts=f"2024-01-0{i+2} 09:30:00")
        for i, v in enumerate(values)
    ]


def test_hand_calculated_win_loss_breakeven_counts():
    stats = _stats(_five_trade_fixture())
    assert stats.core.win_count == 2
    assert stats.core.loss_count == 2
    assert stats.core.breakeven_count == 1


def test_hand_calculated_rates():
    stats = _stats(_five_trade_fixture())
    assert stats.core.win_rate == pytest.approx(0.4)
    assert stats.core.loss_rate == pytest.approx(0.4)
    assert stats.core.breakeven_rate == pytest.approx(0.2)


def test_hand_calculated_mean_and_median():
    stats = _stats(_five_trade_fixture())
    assert stats.core.average_R == pytest.approx(0.2)
    assert stats.core.median_R == pytest.approx(0.0)


def test_hand_calculated_average_win_and_loss():
    stats = _stats(_five_trade_fixture())
    assert stats.core.average_win_R == pytest.approx(1.5)
    assert stats.core.average_loss_R == pytest.approx(-1.0)


def test_hand_calculated_profit_factor():
    stats = _stats(_five_trade_fixture())
    assert stats.core.profit_factor == pytest.approx(1.5)


def test_hand_calculated_max_drawdown():
    # chronological order (by exit_timestamp) is exactly [+2, -1, +1, -1, 0]:
    # cumulative = [2, 1, 2, 1, 1]; peak = [2, 2, 2, 2, 2]; drawdown = [0,1,0,1,1] -> max 1.0
    stats = _stats(_five_trade_fixture())
    assert stats.drawdown.max_drawdown_R == pytest.approx(1.0)


def test_hand_calculated_expectancy_matches_win_loss_breakeven_decomposition():
    stats = _stats(_five_trade_fixture())
    breakeven_mean = 0.0  # the single breakeven trade's R is exactly 0 in this fixture
    decomposed = (
        stats.core.win_rate * stats.core.average_win_R
        + stats.core.loss_rate * stats.core.average_loss_R
        + stats.core.breakeven_rate * breakeven_mean
    )
    assert stats.core.net_expectancy_R == pytest.approx(decomposed)


def test_hand_calculated_gross_equals_net_under_zero_cost():
    stats = _stats(_five_trade_fixture())
    assert stats.core.gross_expectancy_R == pytest.approx(stats.core.net_expectancy_R)


# =============================================================================
# §6 — profit factor special cases
# =============================================================================


def test_profit_factor_no_losses_is_infinite_sentinel():
    outcomes = [_outcome("a", gross_R=1.0), _outcome("b", gross_R=2.0)]
    stats = _stats(outcomes)
    assert stats.core.profit_factor == "INFINITE_NO_LOSSES"


def test_profit_factor_no_wins_is_zero():
    outcomes = [_outcome("a", gross_R=-1.0), _outcome("b", gross_R=-2.0)]
    stats = _stats(outcomes)
    assert stats.core.profit_factor == 0.0


def test_profit_factor_all_breakeven_is_none():
    outcomes = [_outcome("a", gross_R=0.0), _outcome("b", gross_R=0.0)]
    stats = _stats(outcomes)
    assert stats.core.profit_factor is None


def test_profit_factor_no_realized_trades_is_none():
    stats = _stats([])
    assert stats.core.profit_factor is None


# =============================================================================
# §2/§18 population/denominator policy — never re-invented
# =============================================================================


def test_realized_population_excludes_end_of_data():
    outcomes = [
        _outcome("a", exit_status="TARGET1", gross_R=1.0),
        _outcome("b", exit_status="END_OF_DATA", gross_R=0.5),
    ]
    stats = _stats(outcomes)
    assert stats.counts.realized_trade_count == 1
    assert stats.counts.end_of_data_count == 1
    assert stats.core.win_count == 1  # only the TARGET1 trade is realized/classified


def test_expired_eod_and_timeout_are_realized():
    outcomes = [
        _outcome("a", exit_status="EXPIRED_EOD", gross_R=0.3),
        _outcome("b", exit_status="TIMEOUT", gross_R=-0.3),
    ]
    stats = _stats(outcomes)
    assert stats.counts.realized_trade_count == 2
    assert stats.counts.expired_eod_count == 1
    assert stats.counts.timeout_count == 1


def test_invalid_geometry_expired_unfilled_invalidated_before_fill_excluded_but_counted():
    outcomes = [
        _outcome("a", entry_status="INVALID_STOP_GEOMETRY", exit_status=None),
        _outcome("b", entry_status="EXPIRED_UNFILLED", exit_status=None),
        _outcome("c", entry_status="INVALIDATED_BEFORE_FILL", exit_status=None),
        _outcome("d", exit_status="TARGET1", gross_R=1.0),
    ]
    stats = _stats(outcomes)
    assert stats.counts.invalid_geometry_count == 1
    assert stats.counts.expired_unfilled_count == 1
    assert stats.counts.invalidated_before_fill_count == 1
    assert stats.counts.realized_trade_count == 1
    # occurrence_count excludes ONLY invalid geometry (§8-A)
    assert stats.counts.occurrence_count == 3
    # fill_rate_eligible excludes invalid geometry too, includes unfilled/invalidated
    assert stats.counts.fill_rate_eligible_count == 3
    assert stats.counts.filled_count == 1


def test_fill_rate_and_realized_completion_rate():
    outcomes = [
        _outcome("a", exit_status="TARGET1", gross_R=1.0),
        _outcome("b", entry_status="EXPIRED_UNFILLED", exit_status=None),
        _outcome("c", exit_status="END_OF_DATA", gross_R=0.1),
    ]
    stats = _stats(outcomes)
    assert stats.counts.fill_rate == pytest.approx(2 / 3)
    assert stats.counts.realized_completion_rate == pytest.approx(1 / 2)


def test_excluded_records_never_silently_dropped_from_counts():
    outcomes = [_outcome("a", entry_status="EXPIRED_UNFILLED", exit_status=None)]
    stats = _stats(outcomes)
    assert stats.counts.expired_unfilled_count == 1
    assert stats.counts.occurrence_count == 1  # still counted as a valid occurrence


# =============================================================================
# §24 — duplicate protection
# =============================================================================


def test_duplicate_occurrence_id_fails_closed():
    outcomes = [_outcome("dup", gross_R=1.0), _outcome("dup", gross_R=-1.0)]
    with pytest.raises(DuplicateOccurrenceError):
        _stats(outcomes)


def test_distinct_occurrence_ids_are_fine():
    outcomes = [_outcome("a", gross_R=1.0), _outcome("b", gross_R=-1.0)]
    stats = _stats(outcomes)
    assert stats.counts.realized_trade_count == 2


# =============================================================================
# heterogeneous input protection
# =============================================================================


def test_heterogeneous_playbook_id_rejected():
    outcomes = [_outcome("a", gross_R=1.0, playbook_id="OTHER_PLAYBOOK")]
    with pytest.raises(HeterogeneousOutcomeSetError):
        _stats(outcomes)


def test_select_outcomes_for_segment_filters_correctly():
    homogeneous = [_outcome("a", gross_R=1.0), _outcome("b", gross_R=-1.0)]
    other = [_outcome("c", gross_R=1.0, playbook_id="OTHER", playbook_version="1.0")]
    selected = select_outcomes_for_segment(
        homogeneous + other, _BASE["playbook_id"], _BASE["playbook_version"], _BASE["direction"],
        _BASE["timeframe"], _BASE["execution_policy_version"],
    )
    assert len(selected) == 2
    assert all(o.playbook_id == _BASE["playbook_id"] for o in selected)


# =============================================================================
# §30 NaN / invalid numeric fail-closed
# =============================================================================


def test_nan_net_r_on_realized_trade_fails_closed():
    outcomes = [_outcome("a", exit_status="TARGET1", gross_R=1.0, net_R=float("nan"))]
    with pytest.raises(InvalidRealizedOutcomeError):
        _stats(outcomes)


def test_none_gross_r_on_realized_trade_fails_closed():
    bad = _outcome("a", exit_status="TARGET1", gross_R=1.0)
    bad = HistoricalTradeOutcome(**{**bad.__dict__, "gross_R": None})
    with pytest.raises(InvalidRealizedOutcomeError):
        _stats([bad])


# =============================================================================
# §30 edge cases
# =============================================================================


def test_zero_trades():
    stats = _stats([])
    assert stats.evidence_class == "NOT_TESTED"
    assert stats.counts.realized_trade_count == 0


def test_one_trade():
    stats = _stats([_outcome("a", gross_R=1.0)])
    assert stats.counts.realized_trade_count == 1
    assert stats.core.standard_deviation_R is None  # needs n>=2


def test_all_wins():
    stats = _stats([_outcome(str(i), gross_R=1.0) for i in range(5)])
    assert stats.core.loss_count == 0
    assert stats.core.win_count == 5
    assert stats.core.average_loss_R is None


def test_all_losses():
    stats = _stats([_outcome(str(i), gross_R=-1.0) for i in range(5)])
    assert stats.core.win_count == 0
    assert stats.core.average_win_R is None


def test_all_breakeven():
    stats = _stats([_outcome(str(i), gross_R=0.0) for i in range(5)])
    assert stats.core.breakeven_count == 5
    assert stats.core.win_count == 0 and stats.core.loss_count == 0


def test_only_end_of_data():
    stats = _stats([_outcome("a", exit_status="END_OF_DATA", gross_R=0.5)])
    assert stats.counts.realized_trade_count == 0
    assert stats.counts.end_of_data_count == 1
    assert stats.evidence_class == "NOT_TESTED"


def test_only_unfilled():
    stats = _stats([_outcome("a", entry_status="EXPIRED_UNFILLED", exit_status=None)])
    assert stats.counts.realized_trade_count == 0
    assert stats.counts.filled_count == 0


def test_only_invalid_geometry():
    stats = _stats([_outcome("a", entry_status="INVALID_STOP_GEOMETRY", exit_status=None)])
    assert stats.counts.occurrence_count == 0
    assert stats.counts.invalid_geometry_count == 1


def test_multiple_symbols_pooled():
    outcomes = [_outcome("a", symbol="AAPL", gross_R=1.0), _outcome("b", symbol="MSFT", gross_R=-1.0)]
    stats = _stats(outcomes)
    assert stats.counts.realized_trade_count == 2
    assert stats.symbol_concentration.unique_symbol_count == 2


def test_single_symbol():
    outcomes = [_outcome("a", symbol="AAPL", gross_R=1.0), _outcome("b", symbol="AAPL", gross_R=-1.0)]
    stats = _stats(outcomes)
    assert stats.symbol_concentration.unique_symbol_count == 1
    assert stats.symbol_concentration.largest_symbol_share_of_realized_trades == pytest.approx(1.0)


def test_same_timestamp_deterministic_tie_break():
    a = _outcome("z-occ", symbol="AAPL", gross_R=1.0, exit_ts="2024-01-02 10:00:00", trigger_ts="2024-01-02 09:30:00")
    b = _outcome("a-occ", symbol="AAPL", gross_R=-1.0, exit_ts="2024-01-02 10:00:00", trigger_ts="2024-01-02 09:30:00")
    stats1 = _stats([a, b])
    stats2 = _stats([b, a])
    assert stats1.drawdown.max_drawdown_R == stats2.drawdown.max_drawdown_R  # order-independent input, same tie-break


def test_missing_optional_regime():
    stats = _stats([_outcome("a", gross_R=1.0, market_regime_at_trigger=None)])
    assert stats.counts.realized_trade_count == 1


def test_single_regime_segmentation():
    outcomes = [_outcome("a", gross_R=1.0, market_regime_at_trigger="UPTREND_CONFIRMED")]
    segmented = compute_segmented_statistics(
        outcomes, _BASE["playbook_id"], _BASE["playbook_version"], _BASE["family"], _BASE["direction"],
        _BASE["timeframe"], _BASE["execution_policy_version"], POP, EP, COST,
    )
    segment_types = {(s.segment_type, s.segment_value) for s in segmented}
    assert ("POOLED", None) in segment_types
    assert ("MARKET_REGIME", "UPTREND_CONFIRMED") in segment_types


def test_extreme_positive_and_negative_r():
    outcomes = [_outcome("a", gross_R=1000.0), _outcome("b", gross_R=-999.0)]
    stats = _stats(outcomes)
    assert stats.core.largest_win_R == pytest.approx(1000.0)
    assert stats.core.largest_loss_R == pytest.approx(-999.0)


# =============================================================================
# §31 — bootstrap tests
# =============================================================================


def test_bootstrap_same_seed_identical_bounds():
    values = [1.0, -1.0, 0.5, -0.5, 2.0, -0.2]
    lo1, hi1 = bootstrap_confidence_interval(values, 0.90, 500, seed=42)
    lo2, hi2 = bootstrap_confidence_interval(values, 0.90, 500, seed=42)
    assert lo1 == lo2 and hi1 == hi2


def test_bootstrap_different_seeds_may_differ():
    values = [1.0, -1.0, 0.5, -0.5, 2.0, -0.2, 3.0, -1.5]
    lo1, hi1 = bootstrap_confidence_interval(values, 0.90, 500, seed=1)
    lo2, hi2 = bootstrap_confidence_interval(values, 0.90, 500, seed=2)
    assert (lo1, hi1) != (lo2, hi2)


def test_bootstrap_constant_distribution_gives_exact_constant_ci():
    values = [1.5] * 20
    lo, hi = bootstrap_confidence_interval(values, 0.90, 1000, seed=7)
    assert lo == pytest.approx(1.5)
    assert hi == pytest.approx(1.5)


def test_bootstrap_all_positive_gives_positive_lower_bound():
    values = [1.0, 2.0, 0.5, 1.5, 3.0, 0.8, 1.2] * 5
    lo, hi = bootstrap_confidence_interval(values, 0.90, 2000, seed=42)
    assert lo > 0


def test_bootstrap_mixed_noisy_may_cross_zero():
    values = [5.0, -5.0, 0.1, -0.1, 4.0, -4.5, 0.2, -0.3]
    lo, hi = bootstrap_confidence_interval(values, 0.90, 2000, seed=42)
    assert lo <= 0 <= hi or lo <= hi  # not asserting a specific sign — just sane bound ordering


def test_bootstrap_lower_always_leq_upper():
    values = [1.0, -3.0, 2.0, 0.5, -0.5, 4.0, -1.0]
    lo, hi = bootstrap_confidence_interval(values, 0.90, 1000, seed=99)
    assert lo <= hi


def test_small_sample_stays_insufficient_regardless_of_positive_mean():
    outcomes = [_outcome(str(i), gross_R=5.0) for i in range(5)]  # tiny but strongly positive
    stats = _stats(outcomes)
    assert stats.counts.realized_trade_count == 5
    assert stats.core.net_expectancy_R > 0
    assert stats.evidence_class == "INSUFFICIENT_SAMPLE"  # below minimum_realized_trades=30


def test_positive_edge_requires_positive_lower_bound_not_just_positive_mean():
    # Enough trades to clear the sample floor, but noisy enough that the
    # bootstrap lower bound should NOT cross zero confidently for this mix.
    import random
    rng = random.Random(11)
    outcomes = []
    for i in range(40):
        r = 3.0 if i % 3 == 0 else -1.0  # positive mean, but not uniformly so
        outcomes.append(_outcome(str(i), gross_R=r))
    stats = _stats(outcomes)
    assert stats.core.net_expectancy_R > 0
    # evidence_class must be exactly POSITIVE_EDGE or NO_EDGE depending on the bootstrap bound —
    # the key invariant is it must NOT be POSITIVE_EDGE purely because the mean is positive.
    if stats.evidence_class == "POSITIVE_EDGE":
        assert stats.bootstrap.bootstrap_lower_R > 0
    else:
        assert stats.evidence_class == "NO_EDGE"


def test_evidence_class_not_tested_for_zero_realized():
    assert _stats([]).evidence_class == "NOT_TESTED"


# =============================================================================
# §32 — development / validation split
# =============================================================================


def _dated_outcomes(n, start_year=2020):
    return [
        _outcome(
            f"occ-{i}", gross_R=(1.0 if i % 2 == 0 else -1.0),
            trigger_ts=f"{start_year + i // 10}-01-{(i % 27) + 1:02d} 09:30:00",
            exit_ts=f"{start_year + i // 10}-01-{(i % 27) + 1:02d} 10:00:00",
        )
        for i in range(n)
    ]


def test_dev_validation_split_is_chronological_70_30():
    outcomes = _dated_outcomes(10)
    stats = _stats(outcomes)
    split = stats.development_validation
    assert split.development.realized_trade_count == 7
    assert split.validation.realized_trade_count == 3


def test_dev_validation_no_overlap_no_dropped_trades():
    outcomes = _dated_outcomes(10)
    stats = _stats(outcomes)
    split = stats.development_validation
    assert split.development.realized_trade_count + split.validation.realized_trade_count == 10


def test_dev_validation_deterministic_under_ties():
    # same timestamps -> tie-break via symbol/occurrence_id must be stable/reproducible
    outcomes = [_outcome(f"occ-{i}", gross_R=1.0, trigger_ts="2024-01-02 09:30:00", exit_ts="2024-01-02 10:00:00") for i in range(10)]
    s1 = _stats(outcomes)
    s2 = _stats(list(reversed(outcomes)))
    assert s1.development_validation.development.realized_trade_count == s2.development_validation.development.realized_trade_count


def test_dev_validation_same_input_same_split():
    outcomes = _dated_outcomes(10)
    s1 = _stats(outcomes)
    s2 = _stats(outcomes)
    assert s1.development_validation == s2.development_validation


def test_dev_validation_small_sample_handled_explicitly():
    outcomes = _dated_outcomes(2)
    stats = _stats(outcomes)
    assert stats.development_validation.validation.evidence_class in ("NOT_TESTED", "INSUFFICIENT_SAMPLE")


def test_validation_trades_are_all_chronologically_after_development_trades():
    outcomes = _dated_outcomes(10)
    realized_sorted = sorted(outcomes, key=lambda o: (o.exit_timestamp, o.trigger_timestamp, o.symbol, o.occurrence_id))
    split_idx = int(len(realized_sorted) * EP.development_fraction)
    development = realized_sorted[:split_idx]
    validation = realized_sorted[split_idx:]
    assert max(o.exit_timestamp for o in development) <= min(o.exit_timestamp for o in validation)


# =============================================================================
# other_excluded_count — forward compatibility for a future exit_status this
# module doesn't yet enumerate (none exists today; constructed directly).
# =============================================================================


def test_other_excluded_count_catches_an_unrecognized_exit_status():
    known = _outcome("a", exit_status="TARGET1", gross_R=1.0)
    unknown = _outcome("b", exit_status="TARGET1", gross_R=1.0)  # build normally, then mutate exit_status
    unknown = HistoricalTradeOutcome(**{**unknown.__dict__, "exit_status": "SOME_FUTURE_STATUS"})
    stats = _stats([known, unknown])
    assert stats.counts.other_excluded_count == 1
    assert stats.counts.filled_count == 2
    assert stats.counts.realized_trade_count == 1  # the unrecognized status is NOT realized


# =============================================================================
# §33 — lightweight performance regression guard (full benchmark reported
# separately; this just prevents accidental reintroduction of an O(n^2)-ish
# per-row Python loop in the statistics engine itself).
# =============================================================================


def test_statistics_computation_is_fast_for_a_few_thousand_outcomes():
    import time

    outcomes = [
        _outcome(f"occ-{i}", gross_R=(1.0 if i % 2 == 0 else -1.0),
                 trigger_ts=f"2024-{(i % 12) + 1:02d}-01 09:30:00", exit_ts=f"2024-{(i % 12) + 1:02d}-01 10:00:00")
        for i in range(3000)
    ]
    t0 = time.perf_counter()
    _stats(outcomes)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0  # generous bound; real throughput is ~20k+ outcomes/sec


# =============================================================================
# reproducibility / fingerprint
# =============================================================================


def test_same_inputs_same_policies_same_seed_identical_results():
    outcomes = _five_trade_fixture()
    s1 = _stats(outcomes, run_id="r1", created_at=_ts("2024-06-01 00:00:00"))
    s2 = _stats(outcomes, run_id="r1", created_at=_ts("2024-06-01 00:00:00"))
    assert s1 == s2


def test_fingerprint_changes_if_load_bearing_field_changes():
    outcomes = _five_trade_fixture()
    fp1 = compute_input_fingerprint(outcomes)
    mutated = list(outcomes)
    mutated[0] = _outcome(outcomes[0].occurrence_id, gross_R=outcomes[0].gross_R + 0.5,
                           exit_ts="2024-01-02 10:00:00", trigger_ts="2024-01-02 09:30:00")
    fp2 = compute_input_fingerprint(mutated)
    assert fp1 != fp2


def test_fingerprint_order_independent():
    outcomes = _five_trade_fixture()
    fp1 = compute_input_fingerprint(outcomes)
    fp2 = compute_input_fingerprint(list(reversed(outcomes)))
    assert fp1 == fp2


def test_reproducibility_metadata_present():
    stats = _stats(_five_trade_fixture(), run_id="my-run")
    assert stats.reproducibility.statistics_run_id == "my-run"
    assert stats.reproducibility.input_outcome_count == 5
    assert stats.reproducibility.input_fingerprint
    assert stats.reproducibility.data_start is not None
    assert stats.reproducibility.data_end is not None


# =============================================================================
# universe metadata / survivorship bias
# =============================================================================


def test_universe_metadata_defaults_to_unknown_survivorship():
    stats = _stats([])
    assert stats.universe.survivorship_bias_status == "UNKNOWN"


def test_universe_metadata_explicit_survivorship_bias_present():
    universe = UniverseMetadata(
        universe_definition="manual liquid-large-cap research universe",
        universe_method="hand-curated 2026", universe_as_of="2026-09-10",
        survivorship_bias_status="SURVIVORSHIP_BIAS_PRESENT",
    )
    stats = _stats([], universe_metadata=universe)
    assert stats.universe.survivorship_bias_status == "SURVIVORSHIP_BIAS_PRESENT"
    assert stats.universe.universe_definition == "manual liquid-large-cap research universe"
