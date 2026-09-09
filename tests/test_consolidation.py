from conftest import make_flat_df

from engine.features.volatility import atr
from engine.market_state.consolidation import evaluate_consolidation
from engine.market_state.structure import label_structure
from engine.market_state.swings import detect_swings

TRENDING_CLOSES = [
    10.0, 10.6, 11.2, 10.7, 10.1, 10.6, 11.0, 11.8, 11.3, 10.9,
    10.8, 11.1, 11.6, 12.1, 11.7, 11.3, 11.6, 11.4, 11.5,
]

RANGE_CLOSES = [
    10.0, 10.5, 11.0, 10.5, 10.0, 10.4, 10.9, 10.4, 10.0, 10.5,
    11.0, 10.5, 10.1, 10.5, 10.9, 10.4, 10.0, 10.5, 10.9, 10.4,
]

COMPRESSION_CLOSES = [
    10.0, 11.0, 9.5, 11.2, 9.3, 10.8, 9.6, 10.5, 9.7, 10.3,
    9.8, 10.15, 9.9, 10.05, 9.95, 10.02, 9.98, 10.0, 9.99, 10.01,
]


def _evaluate(closes, left_bars=2, right_bars=2, window_bars=None):
    df = make_flat_df(closes)
    points = label_structure(detect_swings(df, left_bars=left_bars, right_bars=right_bars))
    atr_series = atr(df, period=5)
    return evaluate_consolidation(df, points, atr_series, window_bars=window_bars or len(closes))


def test_trending_market_is_not_consolidation():
    result = _evaluate(TRENDING_CLOSES)
    assert result.state == "NONE"


def test_range_bound_market_is_consolidation():
    result = _evaluate(RANGE_CLOSES)
    assert result.state == "CONSOLIDATION"
    assert result.range_high is not None
    assert result.touch_count_high >= 2
    assert result.touch_count_low >= 2


def test_narrowing_wedge_is_compression_even_with_a_single_boundary_touch():
    # This series intentionally does NOT re-touch its original outer extremes —
    # a real narrowing wedge shrinks progressively tighter, so requiring repeated
    # touches of the exact same boundary would make compression undetectable.
    result = _evaluate(COMPRESSION_CLOSES, left_bars=1, right_bars=1)
    assert result.state == "COMPRESSION"


def test_consolidation_reports_range_metrics():
    result = _evaluate(RANGE_CLOSES)
    assert result.range_low < result.range_midpoint < result.range_high
    assert result.range_width == round(result.range_high - result.range_low, 4)


def test_evidence_always_reports_the_analysis_window_regardless_of_state():
    for closes in (TRENDING_CLOSES, RANGE_CLOSES):
        result = _evaluate(closes)
        assert "lookback_bars" in result.evidence
        assert "range_width" in result.evidence
        assert "range_width_atr" in result.evidence
        assert "majors_in_window" in result.evidence
        assert "directional_progress_atr" in result.evidence


# ---- A confirmed uptrend overall + a locally consolidating recent window is a
# VALID combination, not a contradiction — consolidation is scoped ONLY to its
# documented local window, independent of the FSM's confirmed trend state ----


TAIL_RANGE_BOUND = [11.6, 11.9, 11.65, 12.0, 11.6, 11.95, 11.65, 11.9, 11.6, 11.95, 11.65, 11.9]


def test_confirmed_uptrend_with_a_locally_consolidating_recent_window_is_valid():
    from engine.market_state.trend import classify_trend

    df = make_flat_df(TRENDING_CLOSES + TAIL_RANGE_BOUND)
    points = label_structure(detect_swings(df, left_bars=2, right_bars=2))
    atr_series = atr(df)

    events = classify_trend(df, points)
    assert events[-1].state.value == "UPTREND_CONFIRMED"  # the overall FSM state, untouched by this module

    consolidation = evaluate_consolidation(df, points, atr_series, window_bars=len(TAIL_RANGE_BOUND))
    assert consolidation.state == "CONSOLIDATION"  # the LOCAL, last-12-bars-only classification
    assert consolidation.evidence["lookback_bars"] == len(TAIL_RANGE_BOUND)


def test_consolidation_classification_depends_only_on_its_window_not_on_prior_history():
    # Two completely different price histories (an uptrend and a downtrend)
    # leading into the IDENTICAL tail — the window-scoped outputs (state, range
    # boundaries, majors_in_window) must be identical either way. ATR-normalized
    # values may legitimately differ (ATR is a rolling indicator that reflects
    # volatility from before the window too — that's correct, not a locality
    # violation) so those are intentionally not asserted here.
    uptrend_history = TRENDING_CLOSES
    downtrend_history = [20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0, 12.5, 12.2, 12.0, 11.9]

    results = {}
    for name, history in (("uptrend", uptrend_history), ("downtrend", downtrend_history)):
        df = make_flat_df(history + TAIL_RANGE_BOUND)
        points = label_structure(detect_swings(df, left_bars=2, right_bars=2))
        atr_series = atr(df)
        results[name] = evaluate_consolidation(df, points, atr_series, window_bars=len(TAIL_RANGE_BOUND))

    assert results["uptrend"].state == results["downtrend"].state == "CONSOLIDATION"
    assert results["uptrend"].range_high == results["downtrend"].range_high
    assert results["uptrend"].range_low == results["downtrend"].range_low
    assert results["uptrend"].evidence["majors_in_window"] == results["downtrend"].evidence["majors_in_window"]
