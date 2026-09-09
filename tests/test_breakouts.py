from conftest import make_flat_df

from engine.features.volatility import atr
from engine.market_state.breakouts import BreakoutState, RetestState, classify_breakout

PRE = [9.8, 9.9, 10.0, 9.9, 9.8, 9.9, 10.0]  # flat lead-in so ATR settles small and stable
LEVEL = 10.0


def _classify(post, direction="UP", lookforward_bars=30):
    df = make_flat_df(PRE + post)
    atr_series = atr(df, period=5)
    break_idx = len(PRE)
    return classify_breakout(df, LEVEL, direction, break_idx, atr_series, lookforward_bars=lookforward_bars)


def test_fakeout_brief_break_then_immediate_rejection():
    # Wick/near-instant reject: pokes above, back inside within FAKEOUT_MAX_BARS.
    result = _classify([10.5, 9.7, 9.6, 9.8, 9.7])
    assert result.state == BreakoutState.FAKEOUT
    assert result.bars_beyond_level <= 2


def test_failed_breakout_more_bars_than_fakeout_but_never_accepted():
    result = _classify([10.4, 10.5, 10.6, 10.3, 9.8, 9.7, 9.6])
    assert result.state == BreakoutState.FAILED_BREAKOUT
    assert result.bars_beyond_level == 3  # > FAKEOUT_MAX_BARS(2), < ACCEPTANCE_BARS(5)


def test_weak_breakout_tiny_penetration_still_developing():
    result = _classify([10.01, 10.02, 10.01, 10.02, 10.01])
    assert result.state == BreakoutState.WEAK_BREAKOUT
    assert result.break_distance_atr < 0.3


def test_breakout_attempt_real_penetration_too_early_to_resolve():
    result = _classify([10.6, 10.7])
    assert result.state == BreakoutState.BREAKOUT_ATTEMPT
    assert result.retest == RetestState.NO_RETEST


def test_successful_breakout_sustained_no_return():
    result = _classify([10.6, 10.8, 11.0, 11.2, 11.4, 11.6, 11.8, 12.0])
    assert result.state == BreakoutState.SUCCESSFUL_BREAKOUT
    assert result.bars_beyond_level >= 5


def test_successful_breakout_retest_holds_former_resistance_as_support():
    # Sustained acceptance, dips back near the level without meaningfully closing
    # through it, then resumes in the breakout direction — a genuine role reversal.
    result = _classify([10.6, 10.8, 11.0, 11.2, 11.4, 11.5, 9.98, 10.5, 10.9, 11.3])
    assert result.state == BreakoutState.SUCCESSFUL_BREAKOUT_RETEST
    assert result.retest == RetestState.SUCCESSFUL_RETEST


def test_breakout_that_later_failed_distinct_from_failed_breakout():
    # Genuine sustained acceptance (5+ bars, large excursion) established first,
    # THEN loses the level later — must not collapse into FAILED_BREAKOUT/FAKEOUT.
    result = _classify([10.6, 10.8, 11.0, 11.2, 11.4, 11.5, 9.98, 9.5])
    assert result.state == BreakoutState.BREAKOUT_THAT_LATER_FAILED
    assert result.retest == RetestState.FAILED_RETEST


def test_breakdown_direction_mirrors_breakout_direction():
    result = _classify([9.5, 10.3, 10.4, 10.2], direction="DOWN")
    assert result.state == BreakoutState.FAKEOUT
    assert result.direction == "DOWN"


def test_body_close_break_flag_reflects_the_break_bar_close():
    result = _classify([10.6, 10.8, 11.0, 11.2, 11.4, 11.6, 11.8, 12.0])
    assert result.body_close_break is True


def test_evidence_exposes_the_thresholds_used():
    result = _classify([10.6, 10.8, 11.0, 11.2, 11.4, 11.6, 11.8, 12.0])
    assert result.evidence["acceptance_bars_threshold"] == 5
    assert result.evidence["fakeout_max_bars"] == 2
    assert "returned_inside" in result.evidence
