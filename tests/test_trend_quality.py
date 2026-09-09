from datetime import datetime, timedelta

from engine.market_state.trend_quality import classify_trend_quality
from engine.market_state.types import MarketState, SwingPoint, SwingSignificance, SwingType


def _pt(bar_index, price, swing_type):
    ts = datetime(2024, 1, 2) + timedelta(minutes=5 * bar_index)
    return SwingPoint(ts=ts, price=price, swing_type=swing_type, significance=SwingSignificance.MAJOR, score=0.9, bar_index=bar_index)


HEALTHY_POINTS = [
    _pt(0, 100.0, SwingType.LOW),
    _pt(10, 108.0, SwingType.HIGH),  # impulse0: +8 / 10 bars
    _pt(13, 106.0, SwingType.LOW),  # pullback0: -2 / 3 bars (shallow, fast)
    _pt(23, 116.0, SwingType.HIGH),  # impulse1: +10 / 10 bars (growing vs impulse0)
    _pt(26, 113.0, SwingType.LOW),  # pullback1: -3 / 3 bars (shallow, fast)
]

WEAKENING_POINTS = [
    _pt(0, 100.0, SwingType.LOW),
    _pt(10, 112.0, SwingType.HIGH),  # impulse0: +12 / 10 bars
    _pt(13, 109.0, SwingType.LOW),  # pullback0: -3 / 3 bars
    _pt(20, 114.0, SwingType.HIGH),  # impulse1: +5 / 7 bars (shrinking vs impulse0)
    _pt(32, 108.0, SwingType.LOW),  # pullback1: -6 / 12 bars (deep, slow)
]


def test_healthy_uptrend_scores_strong_or_healthy():
    result = classify_trend_quality(HEALTHY_POINTS, MarketState.UPTREND_CONFIRMED)
    assert result.quality in ("STRONG", "HEALTHY")
    assert result.score > 0.6


def test_weakening_uptrend_scores_weakening_or_failure_risk():
    result = classify_trend_quality(WEAKENING_POINTS, MarketState.UPTREND_CONFIRMED)
    assert result.quality in ("WEAKENING", "FAILURE_RISK")
    assert result.score < 0.45


def test_warning_state_is_always_reversal_developing_regardless_of_leg_shape():
    # Even "healthy-shaped" legs must report REVERSAL_DEVELOPING once a CHOCH has
    # put the market into a warning state — the state itself overrides the score.
    result = classify_trend_quality(HEALTHY_POINTS, MarketState.UPTREND_WARNING)
    assert result.quality == "REVERSAL_DEVELOPING"
    assert result.score is None


def test_range_state_has_no_applicable_trend_quality():
    result = classify_trend_quality(HEALTHY_POINTS, MarketState.RANGE)
    assert result.quality == "NOT_APPLICABLE"


def test_insufficient_major_points_is_reported_honestly():
    result = classify_trend_quality(HEALTHY_POINTS[:2], MarketState.UPTREND_CONFIRMED)
    assert result.quality == "INSUFFICIENT_DATA"


def test_choch_violation_in_recent_events_lowers_the_score():
    from engine.market_state.types import MarketStateEvent, StructureLabel

    events = [
        MarketStateEvent(ts=HEALTHY_POINTS[-1].ts, state=MarketState.UPTREND_CONFIRMED,
                          structure_label=StructureLabel.NONE, evidence={"event": "BEARISH_CHOCH"})
    ]
    with_violation = classify_trend_quality(HEALTHY_POINTS, MarketState.UPTREND_CONFIRMED, recent_events=events)
    without_violation = classify_trend_quality(HEALTHY_POINTS, MarketState.UPTREND_CONFIRMED, recent_events=[])
    assert with_violation.score < without_violation.score
