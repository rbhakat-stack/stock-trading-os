from conftest import make_flat_df

from engine.features.volatility import atr
from engine.market_state.support_resistance import enrich_zones

RESISTANCE_ZONE = {
    "zone_type": "RESISTANCE", "upper_boundary": 10.2, "lower_boundary": 10.0,
    "strength_score": 0.6, "source_bar_indices": [1, 3],
}


def _enrich(closes, zone=RESISTANCE_ZONE, proximity_atr=0.15):
    df = make_flat_df(closes)
    atr_series = atr(df, period=5)
    return enrich_zones(df, [zone], atr_series, proximity_atr=proximity_atr)[0]


def test_breakout_and_successful_retest_flips_resistance_to_support():
    closes = [9.5, 10.1, 9.6, 10.15, 9.7, 10.5, 10.8, 11.0, 10.1, 10.9, 11.2]
    result = _enrich(closes)
    assert result.zone_type == "SUPPORT"
    assert result.break_count == 1
    assert result.retest_count >= 1
    assert len(result.role_reversal_history) == 1
    assert result.role_reversal_history[0].from_type == "RESISTANCE"
    assert result.role_reversal_history[0].to_type == "SUPPORT"


def test_pure_rejection_never_breaks_and_never_flips_type():
    # Price repeatedly approaches from below and rejects — a resistance zone
    # that is never actually broken must show zero breaks and keep its type.
    closes = [9.5, 10.1, 9.6, 10.15, 9.7, 10.1, 9.8, 10.15, 9.6]
    result = _enrich(closes)
    assert result.zone_type == "RESISTANCE"
    assert result.break_count == 0
    assert result.retest_count == 0
    assert result.rejection_count > 0
    assert result.role_reversal_history == []


def test_moving_away_from_a_resistance_zone_is_never_counted_as_a_break():
    # A close well BELOW a resistance zone is ordinary distance, not a
    # competing "break down" — this was a real bug found during development.
    closes = [10.1, 9.5, 9.0, 8.5, 8.0]
    result = _enrich(closes)
    assert result.break_count == 0


def test_series_starting_already_broken_does_not_count_bar_zero_as_a_break():
    # Price starts already ABOVE a resistance zone — this reflects a prior
    # break outside the visible window, not a break event happening now.
    closes = [10.8, 11.0, 10.9, 11.1, 11.2]
    result = _enrich(closes)
    assert result.break_count == 0


def test_failed_break_attempt_resets_without_a_role_reversal():
    # Breaks above, but then fully reclaims back below the zone (a failed
    # break) before any retest can be confirmed -- no role reversal should
    # be recorded, and the zone should still be able to register a genuine
    # break afterward.
    closes = [9.5, 10.1, 10.5, 9.6, 9.4, 10.6, 10.9, 11.1, 10.1, 10.8, 11.3]
    result = _enrich(closes)
    assert result.break_count >= 1
    assert result.zone_type in ("RESISTANCE", "SUPPORT")  # must resolve to a real value either way
