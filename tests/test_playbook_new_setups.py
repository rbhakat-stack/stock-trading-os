"""Opening Range Breakout + Range Mean Reversion — the two genuinely NEW
Phase 4 playbooks (§32 acceptance checklist A-L applied to each)."""
from datetime import datetime

from engine.market_state.breakouts import BreakoutEvaluation, BreakoutState, FollowThroughState, RetestState
from engine.market_state.consolidation import ConsolidationEvaluation
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.opening_range import OpeningRangeEvaluation
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.playbooks.evaluators.opening_range_breakout import (
    evaluate_opening_range_breakout_long, evaluate_opening_range_breakout_short,
)
from engine.playbooks.evaluators.range_mean_reversion import (
    evaluate_range_mean_reversion_long, evaluate_range_mean_reversion_short,
)

MIN_RR = 1.5


def _snapshot(**overrides):
    defaults = dict(
        symbol="SPY", timeframe="5min", as_of=datetime(2024, 1, 15, 10, 0), data_source="synthetic",
        market_state="RANGE", trend_quality=TrendQualityEvaluation(quality="NOT_APPLICABLE", score=None, evidence={}),
        latest_bos=None, latest_choch=None, support_zones=[], resistance_zones=[],
        consolidation=None, breakout=None, volume_level="NORMAL", rvol=None, atr_value=1.0,
        volatility=VolatilityClassification(level="NORMAL", percentile=50.0, transition="NEUTRAL", evidence={}),
        opening_range=None, multi_timeframe_alignment=None, time_of_day="OPENING",
        warnings=[], data_quality_ok=True, swing_points=[], events=[], evidence={},
    )
    defaults.update(overrides)
    return MarketIntelligenceSnapshot(**defaults)


def _or_breakout(direction, level, state):
    return BreakoutEvaluation(
        ts=datetime(2024, 1, 15, 10, 5), direction=direction, level=level, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=3, mfe_atr=0.6,
        retest=RetestState.NO_RETEST, follow_through=FollowThroughState.MODERATE, state=BreakoutState(state), evidence={},
    )


def _opening_range(orh=102.0, orl=100.0, status="ABOVE_ORH", breakout=None):
    return OpeningRangeEvaluation(
        session_date="2024-01-15", window_minutes=30, orh=orh, orl=orl, midpoint=(orh + orl) / 2, width=orh - orl,
        width_atr=(orh - orl), opening_volume=100_000, status=status, breakout=breakout, evidence={},
    )


# ===================== Opening Range Breakout =====================


def test_orb_long_not_eligible_no_opening_range_data():
    snapshot = _snapshot(opening_range=None)
    result = evaluate_opening_range_breakout_long(snapshot, 101.0, MIN_RR)
    assert result.setup_status == "NOT_ELIGIBLE"
    assert result.candidate is None


def test_orb_long_forming_still_inside_range():
    orr = _opening_range(status="INSIDE_OPENING_RANGE", breakout=None)
    snapshot = _snapshot(opening_range=orr)
    result = evaluate_opening_range_breakout_long(snapshot, 101.0, MIN_RR)
    assert result.setup_status == "FORMING"
    assert result.quality_score is None  # never fabricated for FORMING


def test_orb_long_triggered_on_successful_breakout():
    breakout = _or_breakout("UP", 102.0, "SUCCESSFUL_BREAKOUT")
    orr = _opening_range(orh=102.0, orl=100.0, status="SUCCESSFUL_BREAKOUT", breakout=breakout)
    snapshot = _snapshot(opening_range=orr, resistance_zones=[])
    result = evaluate_opening_range_breakout_long(snapshot, 102.3, MIN_RR)
    assert result.setup_status == "TRIGGERED"
    assert result.candidate.direction == "LONG"
    assert result.stop_price < result.entry_price  # LONG: stop below entry
    assert result.invalidation_reason


def test_orb_long_invalidated_disqualified_when_breakout_already_failed():
    breakout = _or_breakout("UP", 102.0, "FAKEOUT")
    orr = _opening_range(orh=102.0, orl=100.0, status="FAKEOUT", breakout=breakout)
    snapshot = _snapshot(opening_range=orr)
    result = evaluate_opening_range_breakout_long(snapshot, 101.5, MIN_RR)
    assert result.setup_status == "NOT_ELIGIBLE"


def test_orb_short_triggered_mirrors_long():
    breakout = _or_breakout("DOWN", 100.0, "SUCCESSFUL_BREAKOUT")
    orr = _opening_range(orh=102.0, orl=100.0, status="SUCCESSFUL_BREAKOUT", breakout=breakout)
    snapshot = _snapshot(opening_range=orr, support_zones=[])
    result = evaluate_opening_range_breakout_short(snapshot, 99.7, MIN_RR)
    assert result.setup_status == "TRIGGERED"
    assert result.candidate.direction == "SHORT"
    assert result.stop_price > result.entry_price  # SHORT: stop above entry


def test_orb_wrong_direction_breakout_is_not_eligible_for_opposite_playbook():
    breakout = _or_breakout("UP", 102.0, "SUCCESSFUL_BREAKOUT")
    orr = _opening_range(orh=102.0, orl=100.0, status="SUCCESSFUL_BREAKOUT", breakout=breakout)
    snapshot = _snapshot(opening_range=orr)
    result = evaluate_opening_range_breakout_short(snapshot, 102.3, MIN_RR)
    assert result.setup_status == "NOT_ELIGIBLE"


def test_orb_unsupported_timeframe_is_caught_by_engine_not_evaluator():
    # The evaluator itself doesn't gate timeframe (engine.evaluate_all_playbooks does) —
    # confirm the definition's supported_timeframes excludes 1hour/1day.
    from engine.playbooks.registry import get_definition
    d = get_definition("OPENING_RANGE_BREAKOUT_LONG")
    assert "1hour" not in d.supported_timeframes
    assert "1day" not in d.supported_timeframes
    assert "5min" in d.supported_timeframes


# ===================== Range Mean Reversion =====================


def test_range_mean_reversion_long_not_eligible_when_trending():
    cons = ConsolidationEvaluation(
        state="NONE", range_high=None, range_low=None, range_midpoint=None, range_width=None, range_width_atr=None,
        duration_bars=24, touch_count_high=0, touch_count_low=0, false_break_count=0, evidence={},
    )
    snapshot = _snapshot(consolidation=cons)
    result = evaluate_range_mean_reversion_long(snapshot, 100.0, MIN_RR)
    assert result.setup_status == "NOT_ELIGIBLE"


def test_range_mean_reversion_long_forming_when_not_near_low():
    cons = ConsolidationEvaluation(
        state="CONSOLIDATION", range_high=110.0, range_low=100.0, range_midpoint=105.0, range_width=10.0,
        range_width_atr=10.0, duration_bars=24, touch_count_high=2, touch_count_low=2, false_break_count=0, evidence={},
    )
    snapshot = _snapshot(consolidation=cons, atr_value=1.0)
    result = evaluate_range_mean_reversion_long(snapshot, 105.0, MIN_RR)  # mid-range, not near the low
    assert result.setup_status == "FORMING"
    assert result.quality_score is None


def test_range_mean_reversion_long_triggered_near_range_low():
    cons = ConsolidationEvaluation(
        state="CONSOLIDATION", range_high=110.0, range_low=100.0, range_midpoint=105.0, range_width=10.0,
        range_width_atr=10.0, duration_bars=24, touch_count_high=2, touch_count_low=2, false_break_count=0, evidence={},
    )
    snapshot = _snapshot(consolidation=cons, atr_value=1.0)
    result = evaluate_range_mean_reversion_long(snapshot, 100.1, MIN_RR)  # right at the range low
    assert result.setup_status == "TRIGGERED"
    assert result.stop_price < result.entry_price
    assert result.target1 is not None
    assert result.target1 > result.entry_price  # target toward midpoint/high, above entry for a long


def test_range_mean_reversion_short_triggered_near_range_high_mirrors_long():
    cons = ConsolidationEvaluation(
        state="CONSOLIDATION", range_high=110.0, range_low=100.0, range_midpoint=105.0, range_width=10.0,
        range_width_atr=10.0, duration_bars=24, touch_count_high=2, touch_count_low=2, false_break_count=0, evidence={},
    )
    snapshot = _snapshot(consolidation=cons, atr_value=1.0)
    result = evaluate_range_mean_reversion_short(snapshot, 109.9, MIN_RR)
    assert result.setup_status == "TRIGGERED"
    assert result.stop_price > result.entry_price
    assert result.target1 is not None
    assert result.target1 < result.entry_price


def test_range_mean_reversion_missing_consolidation_fails_closed():
    snapshot = _snapshot(consolidation=None)
    long_result = evaluate_range_mean_reversion_long(snapshot, 100.0, MIN_RR)
    short_result = evaluate_range_mean_reversion_short(snapshot, 100.0, MIN_RR)
    assert long_result.setup_status == "NOT_ELIGIBLE"
    assert short_result.setup_status == "NOT_ELIGIBLE"
    assert long_result.candidate is None and short_result.candidate is None
