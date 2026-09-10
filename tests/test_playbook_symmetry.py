"""§33 of the Phase 4 report — explicit long/short mirror tests for every
paired playbook family. Never assumes copy/paste correctness."""
from datetime import datetime

from engine.market_state.breakouts import BreakoutEvaluation, BreakoutState, FollowThroughState, RetestState
from engine.market_state.consolidation import ConsolidationEvaluation
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.opening_range import OpeningRangeEvaluation
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.playbooks.evaluators.breakout_retest import evaluate_breakout_retest_long, evaluate_breakout_retest_short
from engine.playbooks.evaluators.failed_breakout_reversal import (
    evaluate_failed_breakout_reversal_long, evaluate_failed_breakout_reversal_short,
)
from engine.playbooks.evaluators.opening_range_breakout import (
    evaluate_opening_range_breakout_long, evaluate_opening_range_breakout_short,
)
from engine.playbooks.evaluators.range_mean_reversion import (
    evaluate_range_mean_reversion_long, evaluate_range_mean_reversion_short,
)
from engine.playbooks.evaluators.trend_pullback import evaluate_trend_pullback_long, evaluate_trend_pullback_short

MIN_RR = 1.5


def _zone(zone_type, upper, lower, strength=0.6, touch=3):
    return EnrichedZone(
        zone_type=zone_type, upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=1, break_count=0, retest_count=0,
        last_touch_time=None, role_reversal_history=[], source_bar_indices=[],
    )


def _snapshot(**overrides):
    defaults = dict(
        symbol="SPY", timeframe="5min", as_of=datetime(2024, 1, 15, 10, 0), data_source="synthetic",
        market_state="UPTREND_CONFIRMED", trend_quality=TrendQualityEvaluation(quality="HEALTHY", score=0.75, evidence={}),
        latest_bos=None, latest_choch=None, support_zones=[], resistance_zones=[],
        consolidation=None, breakout=None, volume_level="NORMAL", rvol=None, atr_value=1.0,
        volatility=VolatilityClassification(level="NORMAL", percentile=50.0, transition="NEUTRAL", evidence={}),
        opening_range=None, multi_timeframe_alignment=None, time_of_day="LATE_MORNING",
        warnings=[], data_quality_ok=True, swing_points=[], events=[], evidence={},
    )
    defaults.update(overrides)
    return MarketIntelligenceSnapshot(**defaults)


def test_trend_pullback_stop_geometry_mirrors():
    long_snap = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    short_snap = _snapshot(market_state="DOWNTREND_CONFIRMED", resistance_zones=[_zone("RESISTANCE", 102.0, 101.0)])
    long_result = evaluate_trend_pullback_long(long_snap, 98.5, MIN_RR)
    short_result = evaluate_trend_pullback_short(short_snap, 101.5, MIN_RR)
    assert long_result.setup_status == short_result.setup_status == "TRIGGERED"
    assert long_result.stop_price < long_result.entry_price  # long: stop BELOW structure
    assert short_result.stop_price > short_result.entry_price  # short: stop ABOVE structure


def test_breakout_retest_stop_geometry_mirrors():
    up = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="UP", level=100.0, break_bar_index=10,
        break_distance_atr=1.2, body_close_break=True, bars_beyond_level=6, mfe_atr=1.3, retest=RetestState.SUCCESSFUL_RETEST,
        follow_through=FollowThroughState.STRONG, state=BreakoutState.SUCCESSFUL_BREAKOUT_RETEST, evidence={})
    down = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="DOWN", level=100.0, break_bar_index=10,
        break_distance_atr=1.2, body_close_break=True, bars_beyond_level=6, mfe_atr=1.3, retest=RetestState.SUCCESSFUL_RETEST,
        follow_through=FollowThroughState.STRONG, state=BreakoutState.SUCCESSFUL_BREAKOUT_RETEST, evidence={})
    long_snap = _snapshot(market_state="UPTREND_CONFIRMED", breakout=up, resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    short_snap = _snapshot(market_state="DOWNTREND_CONFIRMED", breakout=down, support_zones=[_zone("SUPPORT", 91.0, 90.0)])
    long_result = evaluate_breakout_retest_long(long_snap, 100.2, MIN_RR)
    short_result = evaluate_breakout_retest_short(short_snap, 99.8, MIN_RR)
    assert long_result.setup_status == short_result.setup_status == "TRIGGERED"
    assert long_result.stop_price < long_result.entry_price
    assert short_result.stop_price > short_result.entry_price


def test_failed_breakout_reversal_stop_geometry_mirrors():
    down_fakeout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="DOWN", level=100.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=1, mfe_atr=0.3, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.WEAK, state=BreakoutState.FAKEOUT, evidence={})
    up_fakeout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="UP", level=100.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=1, mfe_atr=0.3, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.WEAK, state=BreakoutState.FAKEOUT, evidence={})
    long_snap = _snapshot(market_state="RANGE", trend_quality=None, breakout=down_fakeout, resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    short_snap = _snapshot(market_state="RANGE", trend_quality=None, breakout=up_fakeout, support_zones=[_zone("SUPPORT", 91.0, 90.0)])
    long_result = evaluate_failed_breakout_reversal_long(long_snap, 100.5, MIN_RR)
    short_result = evaluate_failed_breakout_reversal_short(short_snap, 99.5, MIN_RR)
    assert long_result.setup_status == short_result.setup_status == "TRIGGERED"
    assert long_result.stop_price < long_result.entry_price
    assert short_result.stop_price > short_result.entry_price


def test_opening_range_breakout_stop_geometry_mirrors():
    up = BreakoutEvaluation(ts=datetime(2024, 1, 15, 10, 5), direction="UP", level=102.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=3, mfe_atr=0.6, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.MODERATE, state=BreakoutState.SUCCESSFUL_BREAKOUT, evidence={})
    down = BreakoutEvaluation(ts=datetime(2024, 1, 15, 10, 5), direction="DOWN", level=100.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=3, mfe_atr=0.6, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.MODERATE, state=BreakoutState.SUCCESSFUL_BREAKOUT, evidence={})
    orr_up = OpeningRangeEvaluation(session_date="2024-01-15", window_minutes=30, orh=102.0, orl=100.0, midpoint=101.0,
        width=2.0, width_atr=2.0, opening_volume=100_000, status="SUCCESSFUL_BREAKOUT", breakout=up, evidence={})
    orr_down = OpeningRangeEvaluation(session_date="2024-01-15", window_minutes=30, orh=102.0, orl=100.0, midpoint=101.0,
        width=2.0, width_atr=2.0, opening_volume=100_000, status="SUCCESSFUL_BREAKOUT", breakout=down, evidence={})
    long_result = evaluate_opening_range_breakout_long(_snapshot(opening_range=orr_up), 102.3, MIN_RR)
    short_result = evaluate_opening_range_breakout_short(_snapshot(opening_range=orr_down), 99.7, MIN_RR)
    assert long_result.setup_status == short_result.setup_status == "TRIGGERED"
    assert long_result.stop_price < long_result.entry_price
    assert short_result.stop_price > short_result.entry_price


def test_range_mean_reversion_stop_geometry_mirrors():
    cons = ConsolidationEvaluation(state="CONSOLIDATION", range_high=110.0, range_low=100.0, range_midpoint=105.0,
        range_width=10.0, range_width_atr=10.0, duration_bars=24, touch_count_high=2, touch_count_low=2,
        false_break_count=0, evidence={})
    long_result = evaluate_range_mean_reversion_long(_snapshot(consolidation=cons), 100.1, MIN_RR)
    short_result = evaluate_range_mean_reversion_short(_snapshot(consolidation=cons), 109.9, MIN_RR)
    assert long_result.setup_status == short_result.setup_status == "TRIGGERED"
    assert long_result.stop_price < long_result.entry_price
    assert short_result.stop_price > short_result.entry_price
    assert long_result.target1 > long_result.entry_price
    assert short_result.target1 < short_result.entry_price
