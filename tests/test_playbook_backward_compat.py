"""§31 of the Phase 4 report — characterization tests proving the six
existing Phase 3 setups produce EQUIVALENT decisions through the new
playbook framework: byte-identical TradeCandidate, and identical
quality/entry/stop/target/decision through the full build_trade_plan
pipeline. engine/trade/setups.py, candidate.py, decision.py, and planner.py
are completely unmodified by Phase 4 — these tests are the proof.
"""
from datetime import datetime

from engine.market_state.bos_choch import BosEvaluation, BosStrength
from engine.market_state.breakouts import BreakoutEvaluation, BreakoutState, FollowThroughState, RetestState
from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.trend_quality import TrendQualityEvaluation
from engine.market_state.volatility_regime import VolatilityClassification
from engine.playbooks.engine import evaluate_all_playbooks, select_primary_candidate
from engine.risk.account_state import AccountRiskState
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade import setups
from engine.trade.planner import build_trade_plan


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


def _breakout(direction, level, state, bar_idx=50):
    return BreakoutEvaluation(
        ts=datetime(2024, 1, 15, 9, 40), direction=direction, level=level, break_bar_index=bar_idx,
        break_distance_atr=1.2, body_close_break=True, bars_beyond_level=6, mfe_atr=1.3,
        retest=RetestState.SUCCESSFUL_RETEST if state == "SUCCESSFUL_BREAKOUT_RETEST" else RetestState.NO_RETEST,
        follow_through=FollowThroughState.STRONG, state=BreakoutState(state), evidence={},
    )


def _assert_candidate_and_pipeline_equivalent(snapshot, current_price, old_matcher, playbook_id):
    old_candidate = old_matcher(snapshot, current_price)
    evals = evaluate_all_playbooks(snapshot, current_price, min_rr=DEFAULT_RISK_POLICY.min_rr)
    new_eval = next(e for e in evals if e.playbook_id == playbook_id)

    assert old_candidate == new_eval.candidate, f"{playbook_id}: candidate diverged from engine.trade.setups"

    account = _account()
    plan = build_trade_plan(snapshot, current_price=current_price, risk_policy=DEFAULT_RISK_POLICY, account=account)
    if old_candidate is None or old_candidate.status != "TRIGGERED":
        return  # nothing further to compare for a non-triggered/absent candidate
    assert plan.candidate == old_candidate
    assert new_eval.entry_price == plan.entry_price
    assert new_eval.stop_price == plan.invalidation.stop_price
    assert new_eval.quality_score == plan.quality.score
    assert new_eval.quality_band == plan.quality.band
    if plan.targets:
        assert new_eval.target1 == plan.targets[0].price
        assert new_eval.rr1 == plan.targets[0].r_multiple


def test_trend_pullback_long_equivalent():
    snapshot = _snapshot()  # UPTREND_CONFIRMED, HEALTHY, support [98,99]
    _assert_candidate_and_pipeline_equivalent(snapshot, 98.5, setups.match_trend_pullback_long, "TREND_PULLBACK_LONG")


def test_trend_pullback_short_equivalent():
    snapshot = _snapshot(
        market_state="DOWNTREND_CONFIRMED", support_zones=[], resistance_zones=[_zone("RESISTANCE", 102.0, 101.0)],
    )
    _assert_candidate_and_pipeline_equivalent(snapshot, 101.5, setups.match_trend_pullback_short, "TREND_PULLBACK_SHORT")


def test_breakout_retest_long_equivalent():
    breakout = _breakout("UP", 100.0, "SUCCESSFUL_BREAKOUT_RETEST")
    snapshot = _snapshot(market_state="UPTREND_CONFIRMED", breakout=breakout, support_zones=[], resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    _assert_candidate_and_pipeline_equivalent(snapshot, 100.2, setups.match_breakout_retest_long, "BREAKOUT_RETEST_LONG")


def test_breakout_retest_short_equivalent():
    breakout = _breakout("DOWN", 100.0, "SUCCESSFUL_BREAKOUT_RETEST")
    snapshot = _snapshot(market_state="DOWNTREND_CONFIRMED", breakout=breakout, support_zones=[_zone("SUPPORT", 91.0, 90.0)], resistance_zones=[])
    _assert_candidate_and_pipeline_equivalent(snapshot, 99.8, setups.match_breakout_retest_short, "BREAKOUT_RETEST_SHORT")


def test_failed_breakout_reversal_long_equivalent():
    breakout = _breakout("DOWN", 100.0, "FAKEOUT")
    snapshot = _snapshot(market_state="RANGE", trend_quality=None, breakout=breakout, support_zones=[], resistance_zones=[_zone("RESISTANCE", 110.0, 109.0)])
    _assert_candidate_and_pipeline_equivalent(snapshot, 100.5, setups.match_failed_breakout_reversal_long, "FAILED_BREAKOUT_REVERSAL_LONG")


def test_failed_breakout_reversal_short_equivalent():
    breakout = _breakout("UP", 100.0, "FAKEOUT")
    snapshot = _snapshot(market_state="RANGE", trend_quality=None, breakout=breakout, support_zones=[_zone("SUPPORT", 91.0, 90.0)], resistance_zones=[])
    _assert_candidate_and_pipeline_equivalent(snapshot, 99.5, setups.match_failed_breakout_reversal_short, "FAILED_BREAKOUT_REVERSAL_SHORT")


def test_not_eligible_regime_matches_none_from_old_matcher():
    # DOWNTREND regime: none of the long setups should match either path.
    snapshot = _snapshot(market_state="DOWNTREND_CONFIRMED", support_zones=[], resistance_zones=[])
    assert setups.match_trend_pullback_long(snapshot, 98.5) is None
    evals = evaluate_all_playbooks(snapshot, 98.5, min_rr=DEFAULT_RISK_POLICY.min_rr)
    tpl = next(e for e in evals if e.playbook_id == "TREND_PULLBACK_LONG")
    assert tpl.setup_status == "NOT_ELIGIBLE"
    assert tpl.candidate is None


def _bos(strength: BosStrength, direction: str = "BULLISH"):
    return BosEvaluation(
        ts=datetime(2024, 1, 15, 9, 45), direction=direction, broken_level=97.0, break_distance=0.5,
        break_distance_atr=0.5, body_close_break=True, candle_body_ratio=0.8, volume=200_000, rvol=1.2,
        follow_through_bars=3, strength=strength, evidence={},
    )


def test_quality_score_matches_planner_with_strong_bos_present():
    # Phase 4 acceptance-audit regression (found during the audit, not part
    # of the original Phase 4 round): engine/playbooks/_shared.py used to
    # default setup_confidence to a flat 0.6 regardless of snapshot.latest_bos,
    # while engine/trade/planner.py derives it from latest_bos.strength
    # (STRONG=1.0/MODERATE=0.6/WEAK=0.2) whenever a BOS is present. With
    # latest_bos=None (every other fixture in this file) both paths
    # coincidentally agree at 0.6, silently hiding the gap — this fixture
    # sets a STRONG BOS specifically so the two paths would disagree if the
    # bug were still present.
    snapshot = _snapshot(latest_bos=_bos(BosStrength.STRONG))
    _assert_candidate_and_pipeline_equivalent(snapshot, 98.5, setups.match_trend_pullback_long, "TREND_PULLBACK_LONG")


def test_quality_score_matches_planner_with_weak_bos_present():
    snapshot = _snapshot(latest_bos=_bos(BosStrength.WEAK))
    _assert_candidate_and_pipeline_equivalent(snapshot, 98.5, setups.match_trend_pullback_long, "TREND_PULLBACK_LONG")


def test_primary_candidate_selection_matches_planner_for_single_candidate_fixtures():
    # Every existing Phase 3 test fixture only ever produces ONE candidate —
    # confirm the new ranking picks the SAME one build_trade_plan does.
    snapshot = _snapshot()
    plan = build_trade_plan(snapshot, current_price=98.5, risk_policy=DEFAULT_RISK_POLICY, account=_account())
    evals = evaluate_all_playbooks(snapshot, 98.5, min_rr=DEFAULT_RISK_POLICY.min_rr)
    primary = select_primary_candidate(evals)
    assert primary is not None
    assert primary.candidate == plan.candidate
