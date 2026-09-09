from engine.trade.quality import compute_quality_score


def test_strong_everything_scores_high():
    result = compute_quality_score(
        trend_quality="STRONG", multi_timeframe_level="HIGH_ALIGNMENT", volume_level="ELEVATED",
        volatility_level="NORMAL", rr1=3.0, min_rr=1.5, setup_confidence=1.0,
    )
    assert result.band == "HIGH"
    assert 0 <= result.score <= 100


def test_weak_everything_scores_low():
    result = compute_quality_score(
        trend_quality="REVERSAL_DEVELOPING", multi_timeframe_level="CONFLICTED", volume_level="VERY_LOW",
        volatility_level="EXTREME", rr1=1.0, min_rr=1.5, setup_confidence=0.0,
    )
    assert result.band == "LOW"


def test_score_is_bounded_0_to_100():
    result = compute_quality_score(
        trend_quality="STRONG", multi_timeframe_level="HIGH_ALIGNMENT", volume_level="ELEVATED",
        volatility_level="NORMAL", rr1=10.0, min_rr=1.5, setup_confidence=1.0,
    )
    assert 0 <= result.score <= 100


def test_unknown_values_default_to_neutral_not_a_crash():
    result = compute_quality_score(
        trend_quality="SOMETHING_UNEXPECTED", multi_timeframe_level=None, volume_level=None,
        volatility_level=None, rr1=None, min_rr=1.5,
    )
    assert 0 <= result.score <= 100


def test_disclaimer_is_always_present():
    result = compute_quality_score(
        trend_quality="STRONG", multi_timeframe_level="HIGH_ALIGNMENT", volume_level="ELEVATED",
        volatility_level="NORMAL", rr1=3.0, min_rr=1.5,
    )
    assert "not probability of profit" in result.disclaimer


def test_no_rr_treated_as_zero_reward_risk_component_not_a_crash():
    result = compute_quality_score(
        trend_quality="HEALTHY", multi_timeframe_level="MODERATE_ALIGNMENT", volume_level="NORMAL",
        volatility_level="NORMAL", rr1=None, min_rr=1.5,
    )
    assert result.components["reward_risk"] == 0.0
