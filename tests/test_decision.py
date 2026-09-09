from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade.candidate import CandidateStatus, TradeCandidate
from engine.trade.decision import DECISION_CONDITIONAL, DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT, make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _candidate(status=CandidateStatus.TRIGGERED.value, **overrides):
    defaults = dict(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status=status, structural_level=98.0,
        entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED"], reasons_against=[], conditions_to_wait_for=[],
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def _invalidation(stop=97.0):
    return InvalidationResult(stop_price=stop, structural_level=98.0, atr_buffer=0.2, reason="below support")


def _target(price=104.0, rr=2.0):
    return TargetResult(price=price, reason="nearest resistance", distance=price - 100.0, reward_per_unit=price - 100.0, r_multiple=rr)


def _kill_switch_normal():
    account = _account()
    return evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)


def _quality(band="HIGH", score=80):
    return QualityScoreResult(score=score, band=band, components={})


_ENTRY = 100.0


def _decide(**overrides):
    defaults = dict(
        data_quality_ok=True, candidate=_candidate(), invalidation=_invalidation(), targets=[_target()],
        risk_policy=DEFAULT_RISK_POLICY, account=_account(), kill_switch=_kill_switch_normal(),
        quality=_quality(), entry_price=_ENTRY,
    )
    defaults.update(overrides)
    return make_trade_decision(**defaults)


# ===================== REJECT =====================


def test_reject_on_data_quality_failure():
    result = _decide(data_quality_ok=False)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.DATA_QUALITY_FAILURE.value in result.no_trade_reasons


def test_reject_on_no_candidate():
    result = _decide(candidate=None)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_VALID_SETUP.value in result.no_trade_reasons


def test_reject_on_no_structural_stop():
    result = _decide(invalidation=None)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons


def test_reject_on_no_valid_target():
    result = _decide(targets=[])
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_VALID_TARGET.value in result.no_trade_reasons


def test_reject_on_rr_below_minimum():
    # policy default min_rr=1.5
    result = _decide(targets=[_target(rr=1.0)])
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.RR_BELOW_MINIMUM.value in result.no_trade_reasons


def test_reject_on_daily_loss_limit():
    account = _account(net_liquidation_value=97_000.0, daily_start_equity=100_000.0)  # -3% > 2% limit
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    result = _decide(account=account, kill_switch=kill_switch)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value in result.no_trade_reasons


def test_reject_on_weekly_loss_limit():
    account = _account(net_liquidation_value=90_000.0, weekly_start_equity=100_000.0)  # -10% > 5% limit
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    result = _decide(account=account, kill_switch=kill_switch)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.WEEKLY_LOSS_LIMIT_REACHED.value in result.no_trade_reasons


def test_reject_on_max_positions_reached():
    account = _account(open_positions=5)  # policy default max_open_positions=5
    result = _decide(account=account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons


def test_reject_on_max_trades_reached():
    account = _account(trades_today=5)  # policy default max_trades_per_day=5
    result = _decide(account=account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_TRADES_REACHED.value in result.no_trade_reasons


def test_reject_on_portfolio_heat_exhausted_via_sizing():
    account = _account(open_risk=3_000.0)  # heat budget (3%) already fully used -> 0 shares
    result = _decide(account=account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value in result.no_trade_reasons


# ===================== WAIT =====================


def test_wait_when_candidate_is_forming():
    result = _decide(candidate=_candidate(status=CandidateStatus.FORMING.value, conditions_to_wait_for=["price pulls back into zone"]))
    assert result.decision == DECISION_WAIT
    assert result.conditions_to_wait_for == ["price pulls back into zone"]


# ===================== CONDITIONAL =====================


def test_conditional_when_soft_reasons_against_present():
    candidate = _candidate(reasons_against=["volume is VERY_LOW"])
    result = _decide(candidate=candidate)
    assert result.decision == DECISION_CONDITIONAL
    assert "volume is VERY_LOW" in result.conditions_to_wait_for


def test_conditional_when_quality_is_low():
    result = _decide(quality=_quality(band="LOW", score=20))
    assert result.decision == DECISION_CONDITIONAL
    assert any("LOW" in c for c in result.conditions_to_wait_for)


# ===================== QUALIFIED =====================


def test_qualified_when_everything_passes_cleanly():
    result = _decide()
    assert result.decision == DECISION_QUALIFIED
    assert result.no_trade_reasons == []
    assert result.position_size is not None
    assert result.position_size.shares > 0


# ===================== Invariants (§35) =====================


def test_invariant_no_qualified_trade_without_target():
    result = _decide(targets=[])
    assert result.decision != DECISION_QUALIFIED


def test_invariant_no_qualified_trade_with_rr_below_policy_minimum():
    result = _decide(targets=[_target(rr=0.5)])
    assert result.decision != DECISION_QUALIFIED


def test_invariant_no_qualified_trade_if_daily_loss_limit_hit():
    account = _account(net_liquidation_value=97_000.0, daily_start_equity=100_000.0)
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    result = _decide(account=account, kill_switch=kill_switch)
    assert result.decision != DECISION_QUALIFIED


def test_invariant_no_qualified_trade_for_reported_acceptance_bug_scenario():
    # Exact reproduction: start-of-day equity 100000, NLV 98500 (-1.50%), max daily loss 1.00%.
    # An otherwise-perfectly-clean setup must still never reach QUALIFIED.
    policy = replace(DEFAULT_RISK_POLICY, max_daily_loss_pct=1.00)
    account = _account(net_liquidation_value=98_500.0, daily_start_equity=100_000.0)
    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True)
    assert kill_switch.blocks_new_trades is True
    result = _decide(account=account, risk_policy=policy, kill_switch=kill_switch)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_invariant_no_qualified_trade_if_portfolio_heat_exceeds_max():
    account = _account(open_risk=3_000.0)
    result = _decide(account=account)
    assert result.decision != DECISION_QUALIFIED


def test_invariant_no_qualified_trade_if_data_quality_fails():
    result = _decide(data_quality_ok=False)
    assert result.decision != DECISION_QUALIFIED


def test_invariant_position_size_never_exceeds_risk_budget():
    result = _decide()
    max_trade_loss = 100_000.0 * (DEFAULT_RISK_POLICY.risk_per_trade_pct / 100)
    assert result.position_size.capital_at_risk <= max_trade_loss + 1e-6


def test_invariant_position_size_never_negative():
    result = _decide(targets=[_target(rr=1.0)])  # rejected before sizing -> position_size stays None
    assert result.position_size is None or result.position_size.shares >= 0
