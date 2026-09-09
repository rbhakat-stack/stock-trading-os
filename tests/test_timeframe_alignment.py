from engine.market_state.timeframe import compute_multi_timeframe_alignment
from engine.market_state.types import MarketState


def test_high_alignment_when_all_timeframes_agree():
    states = {"5min": MarketState.UPTREND_CONFIRMED, "15min": MarketState.UPTREND_CONFIRMED, "1hour": MarketState.UPTREND_CONFIRMED}
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "HIGH_ALIGNMENT"


def test_moderate_alignment_when_some_are_neutral_but_none_conflict():
    states = {"5min": MarketState.UPTREND_CONFIRMED, "15min": MarketState.UPTREND_CONFIRMED, "1hour": MarketState.RANGE}
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "MODERATE_ALIGNMENT"


def test_conflicted_when_bullish_and_bearish_coexist():
    states = {"5min": MarketState.DOWNTREND_CONFIRMED, "15min": MarketState.UPTREND_CONFIRMED, "1hour": MarketState.UPTREND_CONFIRMED}
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "CONFLICTED"


def test_lower_timeframe_does_not_override_a_higher_timeframe_conflict():
    # Five bullish lower timeframes cannot outvote one bearish higher timeframe —
    # any mix of bullish and bearish anywhere is CONFLICTED, never averaged away.
    states = {
        "1min": MarketState.UPTREND_CONFIRMED, "5min": MarketState.UPTREND_CONFIRMED,
        "15min": MarketState.UPTREND_CONFIRMED, "30min": MarketState.UPTREND_CONFIRMED,
        "1hour": MarketState.UPTREND_CONFIRMED, "1day": MarketState.DOWNTREND_CONFIRMED,
    }
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "CONFLICTED"


def test_no_directional_bias_when_everything_is_range():
    states = {"5min": MarketState.RANGE, "15min": MarketState.RANGE}
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "NO_DIRECTIONAL_BIAS"


def test_insufficient_data_with_fewer_than_two_usable_timeframes():
    states = {"5min": MarketState.UPTREND_CONFIRMED, "1day": None}
    result = compute_multi_timeframe_alignment(states)
    assert result.level == "INSUFFICIENT_DATA"


def test_warning_state_is_treated_as_neutral_not_directional():
    states = {"5min": MarketState.UPTREND_WARNING, "15min": MarketState.UPTREND_CONFIRMED}
    result = compute_multi_timeframe_alignment(states)
    # Only one directional (bullish) timeframe -> not a conflict, not full agreement.
    assert result.level == "MODERATE_ALIGNMENT"
