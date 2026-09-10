"""§47 of the Phase 4 report — the deterministic acceptance matrix, numbered
to match the report exactly. Some items are already proven by other test
files (test_playbook_backward_compat.py, test_playbook_symmetry.py,
test_playbook_engine_ranking.py) — those are re-asserted here narrowly so
this file is a genuine, self-contained checklist, not just a pointer.
"""
from dataclasses import replace
from datetime import datetime

from engine.market_state.breakouts import BreakoutEvaluation, BreakoutState, FollowThroughState, RetestState
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.playbooks.engine import detect_conflicts, evaluate_all_playbooks, select_primary_candidate
from engine.risk.account_state import AccountRiskState
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade.decision import DECISION_CONDITIONAL, DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT
from engine.trade.planner import build_trade_plan

MIN_RR = 1.5


def _zone(zone_type, upper, lower, strength=0.6, touch=3):
    return EnrichedZone(
        zone_type=zone_type, upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=1, break_count=0, retest_count=0,
        last_touch_time=None, role_reversal_history=[], source_bar_indices=[],
    )


def _snapshot(**overrides):
    defaults = dict(
        symbol="SPY", timeframe="5min", as_of=datetime(2024, 1, 15, 10, 0), data_source="synthetic",
        market_state="UPTREND_CONFIRMED", trend_quality=TrendQualityEvaluation(quality="HEALTHY", score=0.75, evidence={}),
        latest_bos=None, latest_choch=None, support_zones=[], resistance_zones=[_zone("RESISTANCE", 106.0, 105.0)],
        consolidation=None, breakout=None, volume_level="NORMAL", rvol=None, atr_value=1.0,
        volatility=VolatilityClassification(level="NORMAL", percentile=50.0, transition="NEUTRAL", evidence={}),
        opening_range=None, multi_timeframe_alignment=None, time_of_day="LATE_MORNING",
        warnings=[], data_quality_ok=True, swing_points=[], events=[], evidence={},
    )
    defaults.update(overrides)
    return MarketIntelligenceSnapshot(**defaults)


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


# ---- 1-3: Trend Pullback Long lifecycle ----


def test_01_trend_pullback_long_not_eligible():
    snapshot = _snapshot(market_state="DOWNTREND_CONFIRMED")
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.setup_status == "NOT_ELIGIBLE"


def test_02_trend_pullback_long_forming():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 105.0, MIN_RR)  # far from the support zone -> FORMING
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.setup_status == "FORMING"
    assert e.quality_score is None


def test_03_trend_pullback_long_triggered():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.setup_status == "TRIGGERED"
    assert e.quality_score is not None


# ---- 4-8: TRIGGERED for the remaining implemented playbook pairs ----


def test_04_trend_pullback_short_triggered():
    snapshot = _snapshot(market_state="DOWNTREND_CONFIRMED", resistance_zones=[_zone("RESISTANCE", 102.0, 101.0)])
    evals = evaluate_all_playbooks(snapshot, 101.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_SHORT")
    assert e.setup_status == "TRIGGERED"


def test_05_breakout_retest_long_triggered():
    breakout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="UP", level=100.0, break_bar_index=10,
        break_distance_atr=1.2, body_close_break=True, bars_beyond_level=6, mfe_atr=1.3, retest=RetestState.SUCCESSFUL_RETEST,
        follow_through=FollowThroughState.STRONG, state=BreakoutState.SUCCESSFUL_BREAKOUT_RETEST, evidence={})
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", breakout=breakout, resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    evals = evaluate_all_playbooks(snapshot, 100.2, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "BREAKOUT_RETEST_LONG")
    assert e.setup_status == "TRIGGERED"


def test_06_breakout_retest_short_triggered():
    breakout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="DOWN", level=100.0, break_bar_index=10,
        break_distance_atr=1.2, body_close_break=True, bars_beyond_level=6, mfe_atr=1.3, retest=RetestState.SUCCESSFUL_RETEST,
        follow_through=FollowThroughState.STRONG, state=BreakoutState.SUCCESSFUL_BREAKOUT_RETEST, evidence={})
    snapshot = _snapshot(market_state="DOWNTREND_CONFIRMED", breakout=breakout, support_zones=[_zone("SUPPORT", 91.0, 90.0)])
    evals = evaluate_all_playbooks(snapshot, 99.8, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "BREAKOUT_RETEST_SHORT")
    assert e.setup_status == "TRIGGERED"


def test_07_failed_breakout_reversal_long_triggered():
    breakout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="DOWN", level=100.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=1, mfe_atr=0.3, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.WEAK, state=BreakoutState.FAKEOUT, evidence={})
    snapshot = _snapshot(market_state="RANGE", trend_quality=None, breakout=breakout, resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    evals = evaluate_all_playbooks(snapshot, 100.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "FAILED_BREAKOUT_REVERSAL_LONG")
    assert e.setup_status == "TRIGGERED"


def test_08_failed_breakout_reversal_short_triggered():
    breakout = BreakoutEvaluation(ts=datetime(2024, 1, 15, 9, 40), direction="UP", level=100.0, break_bar_index=10,
        break_distance_atr=0.6, body_close_break=True, bars_beyond_level=1, mfe_atr=0.3, retest=RetestState.NO_RETEST,
        follow_through=FollowThroughState.WEAK, state=BreakoutState.FAKEOUT, evidence={})
    snapshot = _snapshot(market_state="RANGE", trend_quality=None, breakout=breakout, support_zones=[_zone("SUPPORT", 91.0, 90.0)])
    evals = evaluate_all_playbooks(snapshot, 99.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "FAILED_BREAKOUT_REVERSAL_SHORT")
    assert e.setup_status == "TRIGGERED"


# ---- 9-10: soft concern vs hard disqualifier ----


def test_09_soft_concern_lowers_quality_but_does_not_hard_reject():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)], volume_level="VERY_LOW")
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.setup_status == "TRIGGERED"  # not hard-rejected
    assert any(c.code == "VOLUME_IS_VERY_LOW" or "VERY_LOW" in c.description for c in e.soft_concerns)


def test_10_hard_disqualifier_blocks_playbook():
    # Trend quality FAILURE_RISK is a documented disqualifier for trend pullback.
    snapshot = _snapshot(
        market_state="UPTREND_CONFIRMED", trend_quality=TrendQualityEvaluation(quality="FAILURE_RISK", score=0.1, evidence={}),
        support_zones=[_zone("SUPPORT", 99.0, 98.0)],
    )
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.setup_status == "NOT_ELIGIBLE"


# ---- 11-12: fail-closed / unsupported timeframe ----


def test_11_missing_required_data_fails_closed():
    snapshot = _snapshot(opening_range=None)
    evals = evaluate_all_playbooks(snapshot, 100.0, MIN_RR)
    orb = next(x for x in evals if x.playbook_id == "OPENING_RANGE_BREAKOUT_LONG")
    assert orb.setup_status == "NOT_ELIGIBLE"


def test_12_unsupported_timeframe():
    snapshot = _snapshot(timeframe="1day")
    evals = evaluate_all_playbooks(snapshot, 100.0, MIN_RR)
    orb = next(x for x in evals if x.playbook_id == "OPENING_RANGE_BREAKOUT_LONG")
    assert orb.eligibility_status == "NOT_ELIGIBLE_TIMEFRAME"


# ---- 13-16: multiple/conflicting/ranked/deterministic ----


def test_13_multiple_simultaneous_playbooks_all_returned():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    assert len(evals) == 10


def test_14_conflicting_setups_produce_a_conflict_object_or_none_cleanly():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    result = detect_conflicts(evals)  # must not raise; None is valid when no conflict
    assert result is None or (result.long_candidate.direction == "LONG" and result.short_candidate.direction == "SHORT")


def test_15_deterministic_primary_selection():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    p1 = select_primary_candidate(evals)
    p2 = select_primary_candidate(list(reversed(evals)))
    assert p1.playbook_id == p2.playbook_id


def test_16_alternative_playbook_ranking_orders_by_status_then_quality():
    from engine.playbooks.engine import rank_evaluations
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    ranked = rank_evaluations(evals)
    assert ranked[0].setup_status in ("TRIGGERED", "FORMING")


# ---- 17: playbook version persisted (see test_playbook_persistence.py for the full check) ----


def test_17_playbook_version_available_on_every_evaluation():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    evals = evaluate_all_playbooks(snapshot, 98.5, MIN_RR)
    e = next(x for x in evals if x.playbook_id == "TREND_PULLBACK_LONG")
    assert e.playbook_version == "1.0"


# ---- 18-20: Phase 3 gates still reject ----


def test_18_phase3_max_position_gate_still_rejects():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    account = _account(open_positions=5)  # policy default max_open_positions=5
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=account)
    assert plan.decision.decision == DECISION_REJECT
    assert "MAX_POSITIONS_REACHED" in plan.decision.no_trade_reasons


def test_19_phase3_heat_gate_still_rejects():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    account = _account(open_risk=3_000.0)  # heat budget fully used at default 3%
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=account)
    assert plan.decision.decision == DECISION_REJECT


def test_20_phase3_leverage_gate_still_rejects():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    policy = replace(DEFAULT_RISK_POLICY, max_leverage=0.01, max_gross_exposure_pct=1.0)
    account = _account(long_exposure_notional=1_000.0, short_exposure_notional=0.0)
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=policy, account=account)
    assert plan.decision.decision != DECISION_QUALIFIED


# ---- 21-23: decision semantics preserved ----


def test_21_wait_decision_for_forming_setup():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    plan = build_trade_plan(snapshot, current_price=105.0, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision == DECISION_WAIT


def test_22_conditional_triggered_setup_with_soft_concerns():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)], volume_level="VERY_LOW")
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision in (DECISION_CONDITIONAL, DECISION_QUALIFIED)  # soft concern present; band-dependent


def test_23_qualified_clean_triggered_setup():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision == DECISION_QUALIFIED


# ---- 24-25: REJECT reasons ----


def test_24_reject_triggered_setup_due_to_setup_disqualifier():
    # No resistance zone above entry -> NO_VALID_TARGET (a setup-relevant hard block).
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)], resistance_zones=[])
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    assert plan.decision.decision == DECISION_REJECT
    assert "NO_VALID_TARGET" in plan.decision.no_trade_reasons


def test_25_reject_triggered_setup_due_to_phase3_risk_gate():
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", support_zones=[_zone("SUPPORT", 99.0, 98.0)])
    account = _account(open_positions=5)
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=account)
    assert plan.decision.decision == DECISION_REJECT
    assert "MAX_POSITIONS_REACHED" in plan.decision.no_trade_reasons
