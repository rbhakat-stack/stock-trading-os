"""Phase 3 policy-visibility regression tests (see the bug report: 6 saved
positions / max_open_positions=5, FORMING candidate, no breach shown
anywhere). Covers engine/risk/limits.py::evaluate_current_policy_breaches
(new, display-only) plus the pre-existing enforcement semantics it must
never contradict or duplicate.
"""
from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.limits import evaluate_current_policy_breaches
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import AccountPosition, effective_account_state
from engine.trade.candidate import CandidateStatus, TradeCandidate
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT, make_trade_decision
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


def _candidate(status=CandidateStatus.TRIGGERED.value, direction="LONG", **overrides):
    defaults = dict(
        setup_type="TREND_PULLBACK_LONG", direction=direction, status=status, structural_level=98.0,
        entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED"], reasons_against=[], conditions_to_wait_for=[],
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def _kill_switch_normal(account):
    return evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)


def _decide(**overrides):
    defaults = dict(
        data_quality_ok=True, candidate=_candidate(),
        invalidation=InvalidationResult(stop_price=97.0, structural_level=98.0, atr_buffer=0.2, reason="below support"),
        targets=[TargetResult(price=104.0, reason="nearest resistance", distance=4.0, reward_per_unit=4.0, r_multiple=2.0)],
        risk_policy=DEFAULT_RISK_POLICY, account=_account(), kill_switch=None,
        quality=QualityScoreResult(score=80, band="HIGH", components={}), entry_price=100.0,
    )
    defaults.update(overrides)
    if defaults["kill_switch"] is None:
        defaults["kill_switch"] = _kill_switch_normal(defaults["account"])
    return make_trade_decision(**defaults)


# DEFAULT_RISK_POLICY.max_open_positions is 5 — matches the exact reported scenario.
assert DEFAULT_RISK_POLICY.max_open_positions == 5


# ===================== §6 boundary semantics =====================


def test_case_a_four_positions_no_breach():
    breaches = evaluate_current_policy_breaches(_account(open_positions=4), DEFAULT_RISK_POLICY)
    assert not any(b.code == "MAX_POSITIONS_REACHED" for b in breaches)


def test_case_b_five_positions_at_limit_reached_wording():
    breaches = evaluate_current_policy_breaches(_account(open_positions=5), DEFAULT_RISK_POLICY)
    matches = [b for b in breaches if b.code == "MAX_POSITIONS_REACHED"]
    assert len(matches) == 1
    assert "REACHED" in matches[0].message
    assert "EXCEEDED" not in matches[0].message
    assert "5" in matches[0].message


def test_case_c_six_positions_over_limit_exceeded_wording():
    breaches = evaluate_current_policy_breaches(_account(open_positions=6), DEFAULT_RISK_POLICY)
    matches = [b for b in breaches if b.code == "MAX_POSITIONS_REACHED"]
    assert len(matches) == 1
    assert "EXCEEDED" in matches[0].message
    assert "6" in matches[0].message and "5" in matches[0].message


def test_case_d_risk_reducing_trade_at_six_positions_bypasses_gate():
    # Existing LONG position in this symbol; a SHORT candidate that only
    # partially offsets it (never overshoots past flat) is RISK_REDUCING —
    # the documented §20/§27 bypass must still apply even with 6 >= 5.
    account = _account(open_positions=6)
    result = _decide(
        account=account, candidate=_candidate(direction="SHORT"),
        existing_symbol_notional_signed=5_000.0, existing_symbol_shares_signed=50.0,
    )
    assert NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons
    assert result.decision != DECISION_REJECT or NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons


# ===================== §7 FORMING still shows breach =====================


def test_forming_candidate_decision_never_mentions_max_positions():
    # The decision engine short-circuits to WAIT for FORMING candidates
    # BEFORE it ever reaches the max-positions check — this is the exact
    # mechanism behind the reported "no visibility" symptom. Confirmed here
    # so a future change to that ordering doesn't silently reintroduce it
    # without this test failing.
    account = _account(open_positions=6)
    result = _decide(
        account=account,
        candidate=_candidate(status=CandidateStatus.FORMING.value, conditions_to_wait_for=["price pulls back into zone"]),
    )
    assert result.decision == DECISION_WAIT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons


def test_forming_scenario_still_flagged_by_breach_evaluator():
    # Exact reported scenario: 6 positions, max=5, FORMING/WAIT candidate.
    # evaluate_current_policy_breaches is candidate-independent, so it must
    # flag the breach regardless of what the decision engine returned above.
    account = _account(open_positions=6)
    breaches = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY)
    assert any(b.code == "MAX_POSITIONS_REACHED" and "EXCEEDED" in b.message for b in breaches)


# ===================== §8 TRIGGERED acceptance =====================


def test_triggered_clean_candidate_rejects_at_six_positions():
    result = _decide(account=_account(open_positions=6))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_triggered_clean_candidate_rejects_at_five_positions():
    result = _decide(account=_account(open_positions=5))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_triggered_clean_candidate_clears_gate_at_four_positions():
    result = _decide(account=_account(open_positions=4))
    assert NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons
    assert result.decision == DECISION_QUALIFIED


# ===================== Other current-policy breaches (§10) =====================


def test_max_trades_reached_breach():
    account = _account(trades_today=DEFAULT_RISK_POLICY.max_trades_per_day)
    breaches = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY)
    assert any(b.code == "MAX_TRADES_REACHED" for b in breaches)


def test_portfolio_heat_breach_suppressed_when_open_risk_incomplete():
    account = _account(open_risk=DEFAULT_RISK_POLICY.max_portfolio_heat_pct / 100 * 100_000.0)
    breaches_complete = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY, open_risk_complete=True)
    breaches_incomplete = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY, open_risk_complete=False)
    assert any(b.code == "PORTFOLIO_HEAT_EXCEEDED" for b in breaches_complete)
    assert not any(b.code == "PORTFOLIO_HEAT_EXCEEDED" for b in breaches_incomplete)


def test_gross_net_leverage_breaches():
    policy = replace(DEFAULT_RISK_POLICY, max_gross_exposure_pct=50.0, max_net_exposure_pct=50.0, max_leverage=0.5)
    account = _account(long_exposure_notional=76_500.0, short_exposure_notional=0.0)  # 76.5% gross, matches the report
    breaches = evaluate_current_policy_breaches(account, policy)
    codes = {b.code for b in breaches}
    assert {"GROSS_EXPOSURE_EXCEEDED", "NET_EXPOSURE_EXCEEDED", "LEVERAGE_LIMIT_EXCEEDED"} <= codes


def test_daily_weekly_realized_loss_breaches():
    policy = DEFAULT_RISK_POLICY
    account = _account(
        net_liquidation_value=97_000.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        realized_pnl_today=-5_000.0,
    )
    breaches = evaluate_current_policy_breaches(account, policy)
    codes = {b.code for b in breaches}
    assert "DAILY_LOSS_LIMIT_REACHED" in codes
    assert "DAILY_REALIZED_LOSS_LIMIT_REACHED" in codes


def test_consecutive_loss_lockout_breach():
    account = _account(consecutive_losses=DEFAULT_RISK_POLICY.max_consecutive_losses)
    breaches = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY)
    assert any(b.code == "MAX_CONSECUTIVE_LOSSES_REACHED" for b in breaches)


def test_no_breaches_for_a_healthy_account():
    breaches = evaluate_current_policy_breaches(_account(open_positions=1), DEFAULT_RISK_POLICY)
    assert breaches == []


# ===================== §11 display/derivation invariants =====================


def test_displayed_effective_open_positions_equals_derived_count():
    positions = [
        AccountPosition(
            symbol=f"SYM{i}", quantity_signed=10.0, reference_price=100.0, updated_at=datetime(2024, 1, 15),
            planned_stop_price=95.0, average_price=100.0,
        )
        for i in range(6)
    ]
    raw_account = _account(open_positions=0)  # stale/irrelevant manual value
    effective, _complete = effective_account_state(raw_account, positions)
    assert effective.open_positions == 6
    breaches = evaluate_current_policy_breaches(effective, DEFAULT_RISK_POLICY)
    assert any(b.code == "MAX_POSITIONS_REACHED" and "current: 6" in b.message for b in breaches)


def test_stale_manual_open_positions_cannot_override_derived_count():
    # Manual field says 0 (or anything) — once real positions exist, they
    # are authoritative; a stale manual figure must never suppress the
    # breach (see the Round J root-cause fix this builds on).
    positions = [
        AccountPosition(
            symbol=f"SYM{i}", quantity_signed=10.0, reference_price=100.0, updated_at=datetime(2024, 1, 15),
            planned_stop_price=95.0, average_price=100.0,
        )
        for i in range(6)
    ]
    raw_account = _account(open_positions=0)
    effective, _complete = effective_account_state(raw_account, positions)
    assert effective.open_positions != raw_account.open_positions
    assert effective.open_positions == 6
