from engine.trade.invalidation import compute_long_invalidation, compute_short_invalidation


def test_long_invalidation_sits_below_structural_level():
    result = compute_long_invalidation(structural_level=100.0, atr_value=1.0, buffer_atr=0.1)
    assert result.stop_price == 99.9
    assert result.stop_price < result.structural_level


def test_short_invalidation_sits_above_structural_level():
    result = compute_short_invalidation(structural_level=100.0, atr_value=1.0, buffer_atr=0.1)
    assert result.stop_price == 100.1
    assert result.stop_price > result.structural_level


def test_zero_buffer_places_stop_exactly_at_structural_level():
    result = compute_long_invalidation(structural_level=100.0, atr_value=1.0, buffer_atr=0.0)
    assert result.stop_price == 100.0


def test_negative_atr_never_produces_a_negative_buffer():
    result = compute_long_invalidation(structural_level=100.0, atr_value=-5.0, buffer_atr=0.1)
    assert result.atr_buffer == 0.0
    assert result.stop_price == 100.0


def test_reason_references_the_structural_level():
    result = compute_long_invalidation(structural_level=157.5, atr_value=0.5, buffer_atr=0.2)
    assert "157.5" in result.reason
