from datetime import datetime

from engine.market_state.breakouts import BreakoutEvaluation, BreakoutState, FollowThroughState, RetestState
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.trade import setups
from engine.trade.candidate import CandidateStatus


def _zone(zone_type, upper, lower, strength=0.6, touch=3):
    return EnrichedZone(
        zone_type=zone_type, upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=1, break_count=0, retest_count=0,
        last_touch_time=None, role_reversal_history=[], source_bar_indices=[],
    )


def _breakout(direction, level, state, retest=RetestState.NO_RETEST, follow_through=FollowThroughState.MODERATE):
    return BreakoutEvaluation(
        ts=datetime(2024, 1, 15, 10, 0), direction=direction, level=level, break_bar_index=10,
        break_distance_atr=0.5, body_close_break=True, bars_beyond_level=6, mfe_atr=1.2,
        retest=retest, follow_through=follow_through, state=state, evidence={},
    )


def _trend_quality(quality, score=0.7):
    return TrendQualityEvaluation(quality=quality, score=score, evidence={})


def _volatility(level="NORMAL"):
    return VolatilityClassification(level=level, percentile=50.0, transition="NEUTRAL", evidence={})


def _snapshot(**overrides):
    defaults = dict(
        symbol="SPY", timeframe="5min", as_of=datetime(2024, 1, 15, 10, 0), data_source="synthetic",
        market_state="RANGE", trend_quality=None, latest_bos=None, latest_choch=None,
        support_zones=[], resistance_zones=[], consolidation=None, breakout=None,
        volume_level="NORMAL", rvol=None, atr_value=1.0, volatility=_volatility(),
        opening_range=None, multi_timeframe_alignment=None, time_of_day="LATE_MORNING",
        warnings=[], data_quality_ok=True, swing_points=[], events=[], evidence={},
    )
    defaults.update(overrides)
    return MarketIntelligenceSnapshot(**defaults)


# ===================== BREAKOUT_RETEST_LONG =====================


def test_breakout_retest_long_valid_and_triggered():
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED",
        breakout=_breakout("UP", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT_RETEST),
    )
    result = setups.match_breakout_retest_long(snapshot, current_price=100.5)
    assert result is not None
    assert result.direction == "LONG"
    assert result.status == CandidateStatus.TRIGGERED.value
    assert result.structural_level == 100.0


def test_breakout_retest_long_almost_valid_forming():
    # Successful breakout but no retest yet -> FORMING, not TRIGGERED.
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED",
        breakout=_breakout("UP", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT),
    )
    result = setups.match_breakout_retest_long(snapshot, current_price=103.0)
    assert result is not None
    assert result.status == CandidateStatus.FORMING.value
    assert result.conditions_to_wait_for


def test_breakout_retest_long_invalid_wrong_market_state():
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED",
        breakout=_breakout("UP", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT_RETEST),
    )
    assert setups.match_breakout_retest_long(snapshot, current_price=100.5) is None


def test_breakout_retest_long_invalid_wrong_breakout_state():
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED",
        breakout=_breakout("UP", 100.0, BreakoutState.FAKEOUT),
    )
    assert setups.match_breakout_retest_long(snapshot, current_price=100.5) is None


# ===================== BREAKOUT_RETEST_SHORT =====================


def test_breakout_retest_short_valid_and_triggered():
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED",
        breakout=_breakout("DOWN", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT_RETEST),
    )
    result = setups.match_breakout_retest_short(snapshot, current_price=99.5)
    assert result is not None
    assert result.direction == "SHORT"
    assert result.status == CandidateStatus.TRIGGERED.value


def test_breakout_retest_short_invalid_wrong_direction():
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED",
        breakout=_breakout("UP", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT_RETEST),
    )
    assert setups.match_breakout_retest_short(snapshot, current_price=99.5) is None


# ===================== TREND_PULLBACK_LONG =====================


def test_trend_pullback_long_valid_and_triggered():
    zone = _zone("SUPPORT", upper=99.0, lower=98.0)
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED", trend_quality=_trend_quality("HEALTHY"), support_zones=[zone],
    )
    result = setups.match_trend_pullback_long(snapshot, current_price=98.5)  # inside the zone
    assert result is not None
    assert result.direction == "LONG"
    assert result.status == CandidateStatus.TRIGGERED.value


def test_trend_pullback_long_almost_valid_forming():
    zone = _zone("SUPPORT", upper=99.0, lower=98.0)
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED", trend_quality=_trend_quality("HEALTHY"), support_zones=[zone], atr_value=1.0,
    )
    result = setups.match_trend_pullback_long(snapshot, current_price=105.0)  # nowhere near the zone
    assert result is not None
    assert result.status == CandidateStatus.FORMING.value


def test_trend_pullback_long_invalid_reversal_developing():
    zone = _zone("SUPPORT", upper=99.0, lower=98.0)
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED", trend_quality=_trend_quality("REVERSAL_DEVELOPING"), support_zones=[zone],
    )
    assert setups.match_trend_pullback_long(snapshot, current_price=98.5) is None


def test_trend_pullback_long_invalid_no_support_zone():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", trend_quality=_trend_quality("HEALTHY"), support_zones=[])
    assert setups.match_trend_pullback_long(snapshot, current_price=98.5) is None


# ===================== TREND_PULLBACK_SHORT =====================


def test_trend_pullback_short_valid_and_triggered():
    zone = _zone("RESISTANCE", upper=102.0, lower=101.0)
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED", trend_quality=_trend_quality("HEALTHY"), resistance_zones=[zone],
    )
    result = setups.match_trend_pullback_short(snapshot, current_price=101.5)
    assert result is not None
    assert result.direction == "SHORT"
    assert result.status == CandidateStatus.TRIGGERED.value


# ===================== FAILED_BREAKOUT_REVERSAL_LONG =====================


def test_failed_breakout_reversal_long_valid_and_triggered():
    snapshot = _snapshot(
        market_state="RANGE",
        breakout=_breakout("DOWN", 100.0, BreakoutState.FAILED_BREAKOUT),
    )
    result = setups.match_failed_breakout_reversal_long(snapshot, current_price=100.5)  # reclaimed above 100
    assert result is not None
    assert result.direction == "LONG"
    assert result.status == CandidateStatus.TRIGGERED.value


def test_failed_breakout_reversal_long_almost_valid_forming():
    snapshot = _snapshot(
        market_state="RANGE",
        breakout=_breakout("DOWN", 100.0, BreakoutState.FAILED_BREAKOUT),
    )
    result = setups.match_failed_breakout_reversal_long(snapshot, current_price=99.5)  # not reclaimed yet
    assert result is not None
    assert result.status == CandidateStatus.FORMING.value


def test_failed_breakout_reversal_long_invalid_no_failed_breakdown():
    snapshot = _snapshot(market_state="RANGE", breakout=_breakout("DOWN", 100.0, BreakoutState.SUCCESSFUL_BREAKOUT))
    assert setups.match_failed_breakout_reversal_long(snapshot, current_price=100.5) is None


def test_failed_breakout_reversal_long_flags_counter_trend_risk():
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED", trend_quality=_trend_quality("STRONG"),
        breakout=_breakout("DOWN", 100.0, BreakoutState.FAILED_BREAKOUT),
    )
    result = setups.match_failed_breakout_reversal_long(snapshot, current_price=100.5)
    assert result is not None
    assert any("counter-trend" in r for r in result.reasons_against)


# ===================== FAILED_BREAKOUT_REVERSAL_SHORT =====================


def test_failed_breakout_reversal_short_valid_and_triggered():
    snapshot = _snapshot(
        market_state="RANGE",
        breakout=_breakout("UP", 100.0, BreakoutState.FAKEOUT),
    )
    result = setups.match_failed_breakout_reversal_short(snapshot, current_price=99.5)  # broke back below 100
    assert result is not None
    assert result.direction == "SHORT"
    assert result.status == CandidateStatus.TRIGGERED.value


# ===================== detect_candidates orchestration =====================


def test_detect_candidates_returns_only_relevant_setups():
    from engine.trade.candidate import detect_candidates

    zone = _zone("SUPPORT", upper=99.0, lower=98.0)
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED", trend_quality=_trend_quality("HEALTHY"), support_zones=[zone],
    )
    candidates = detect_candidates(snapshot, current_price=98.5)
    assert len(candidates) >= 1
    assert all(c.direction == "LONG" for c in candidates)  # no SHORT setups should match an uptrend


def test_detect_candidates_empty_when_nothing_matches():
    from engine.trade.candidate import detect_candidates

    snapshot = _snapshot(market_state="RANGE")
    candidates = detect_candidates(snapshot, current_price=100.0)
    assert candidates == []
