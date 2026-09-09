import pandas as pd

from engine.market_state.volatility_regime import (
    MIN_BARS_FOR_VOLATILITY_REGIME,
    VolatilityTransition,
    classify_volatility,
    detect_expansion_contraction,
)


def test_insufficient_data_with_short_history():
    series = pd.Series([0.5] * 10)
    result = classify_volatility(series, bar_index=9)
    assert result.level == "INSUFFICIENT_DATA"
    assert result.percentile is None


def test_insufficient_data_when_atr_itself_is_nan():
    series = pd.Series([float("nan")] * 25)
    result = classify_volatility(series, bar_index=24)
    assert result.level == "INSUFFICIENT_DATA"


def test_extreme_volatility_when_current_atr_is_a_clear_outlier():
    series = pd.Series([1.0] * 59 + [5.0])
    result = classify_volatility(series, bar_index=59, history_window=60)
    assert result.level == "EXTREME"
    assert result.percentile > 99.0


def test_very_low_volatility_when_current_atr_is_a_clear_low_outlier():
    series = pd.Series([5.0] * 59 + [0.1])
    result = classify_volatility(series, bar_index=59, history_window=60)
    assert result.level == "VERY_LOW"


def test_normal_volatility_for_flat_history():
    series = pd.Series([1.0] * 60)
    result = classify_volatility(series, bar_index=59, history_window=60)
    assert result.level == "NORMAL"


def test_expansion_detected_when_atr_ramps_up():
    series = pd.Series([1.0] * 5 + [1.0, 1.5, 2.0, 2.5, 3.0])
    assert detect_expansion_contraction(series, bar_index=9) == VolatilityTransition.EXPANSION.value


def test_contraction_detected_when_atr_ramps_down():
    series = pd.Series([3.0, 2.7, 2.4, 2.1, 1.8, 1.5, 1.2, 1.0, 0.8, 0.6])
    assert detect_expansion_contraction(series, bar_index=9) == VolatilityTransition.CONTRACTION.value


def test_neutral_when_atr_is_flat():
    series = pd.Series([1.0] * 10)
    assert detect_expansion_contraction(series, bar_index=9) == VolatilityTransition.NEUTRAL.value


def test_expansion_contraction_neutral_with_insufficient_window():
    series = pd.Series([1.0, 2.0, 3.0])
    assert detect_expansion_contraction(series, bar_index=2) == VolatilityTransition.NEUTRAL.value
