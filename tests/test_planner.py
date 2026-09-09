from datetime import datetime

from engine.market_state.breakouts import BreakoutState, FollowThroughState, RetestState
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.risk.account_state import AccountRiskState
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT
from engine.trade.planner import EVENT_RISK_STATUS, STATISTICAL_VALIDATION_STATUS, build_trade_plan


def _zone(zone_type, upper, lower, strength=0.6, touch=3):
    return EnrichedZone(
        zone_type=zone_type, upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=1, break_count=0, retest_count=0,
        last_touch_time=None, role_reversal_history=[], source_bar_indices=[],
    )


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _snapshot(**overrides):
    from engine.market_state.breakouts import BreakoutEvaluation

    defaults = dict(
        symbol="SPY", timeframe="5min", as_of=datetime(2024, 1, 15, 10, 0), data_source="synthetic",
        market_state="UPTREND_CONFIRMED",
        trend_quality=TrendQualityEvaluation(quality="HEALTHY", score=0.75, evidence={}),
        latest_bos=None, latest_choch=None,
        support_zones=[_zone("SUPPORT", 99.0, 98.0)],
        resistance_zones=[_zone("RESISTANCE", 106.0, 105.0)],
        consolidation=None, breakout=None,
        volume_level="NORMAL", rvol=None, atr_value=1.0,
        volatility=VolatilityClassification(level="NORMAL", percentile=50.0, transition="NEUTRAL", evidence={}),
        opening_range=None, multi_timeframe_alignment=None, time_of_day="LATE_MORNING",
        warnings=[], data_quality_ok=True, swing_points=[], events=[], evidence={},
    )
    defaults.update(overrides)
    return MarketIntelligenceSnapshot(**defaults)


def test_full_pipeline_produces_qualified_for_a_clean_trend_pullback():
    snapshot = _snapshot()  # UPTREND_CONFIRMED, HEALTHY, support zone [98,99], resistance zone [105,106]
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())

    assert plan.decision.decision == DECISION_QUALIFIED
    assert plan.candidate is not None
    assert plan.invalidation is not None
    assert plan.invalidation.stop_price < plan.entry_price
    assert plan.targets
    assert plan.decision.position_size is not None
    assert plan.decision.position_size.shares > 0


def test_no_candidate_produces_reject_with_no_valid_setup():
    snapshot = _snapshot(market_state="RANGE", support_zones=[], resistance_zones=[])
    plan = build_trade_plan(snapshot, current_price=100.0, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision == DECISION_REJECT
    assert "NO_VALID_SETUP" in plan.decision.no_trade_reasons


def test_forming_candidate_produces_wait():
    snapshot = _snapshot(support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    plan = build_trade_plan(snapshot, current_price=110.0, risk_policy=DEFAULT_RISK_POLICY, account=_account())  # far from the zone
    assert plan.decision.decision == DECISION_WAIT


def test_data_quality_failure_produces_reject_regardless_of_structure():
    snapshot = _snapshot(data_quality_ok=False)
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision == DECISION_REJECT
    assert "DATA_QUALITY_FAILURE" in plan.decision.no_trade_reasons


def test_statistical_validation_and_event_risk_are_never_fabricated():
    snapshot = _snapshot()
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.statistical_validation == STATISTICAL_VALIDATION_STATUS == "NOT_YET_AVAILABLE"
    assert plan.event_risk == EVENT_RISK_STATUS == "NOT_AVAILABLE"
    assert "LIMITED" in plan.liquidity


def test_no_buy_sell_language_anywhere_in_the_plan():
    snapshot = _snapshot()
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    dump = str(plan)
    for forbidden in ("BUY", "SELL", "GUARANTEED", "HIGH PROBABILITY"):
        assert forbidden not in dump


def test_kill_switch_blocks_qualification_even_with_a_clean_setup():
    snapshot = _snapshot()
    account = _account(net_liquidation_value=97_000.0, daily_start_equity=100_000.0)  # daily loss limit hit
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=account)
    assert plan.decision.decision == DECISION_REJECT
    assert "DAILY_LOSS_LIMIT_REACHED" in plan.decision.no_trade_reasons
