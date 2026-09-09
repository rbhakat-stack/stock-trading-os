"""Deterministic acceptance tests for the Phase 3 portfolio-heat gate.

Fixture numbers (as specified for acceptance testing):
    NLV = 100000
    Open risk = 900          -> current portfolio heat = 0.900%
    Max portfolio heat = 1.00%  (-> $1000 heat budget)
    Risk per trade = 0.50%      (-> $500 max single-trade risk budget)

No production logic is changed here — this file only exercises
engine/risk/portfolio_heat.py, engine/risk/sizing.py, and
engine/trade/decision.py exactly as they exist.

BOUNDARY POLICY (documented, not changed): exactly-at-limit is ALLOWED.
compute_position_size() floors shares to the largest count whose capital-at-
-risk fits within `remaining_heat_budget = max_heat_value - current_open_risk`
(a "<=" comparison), so a trade landing EXACTLY on the heat ceiling is sized
and permitted — a trade is blocked only once even the smallest achievable
position (1 share) would push heat *strictly past* the ceiling, at which
point sizing floors to 0 shares and the decision engine REJECTs with
PORTFOLIO_HEAT_EXCEEDED. This is consistent with limits.portfolio_heat_exceeded()
(used only for the kill-switch's REDUCE_RISK warning on CURRENT heat, a
different check) which uses ">=" against current heat — the two checks
answer different questions (already-over-heated vs. would-this-new-trade-
tip-it-over) and neither contradicts the other.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.portfolio_heat import compute_post_trade_heat_pct
from engine.trade.candidate import TradeCandidate
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult

NLV = 100_000.0
OPEN_RISK = 900.0  # -> 0.900% current heat
POLICY = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=0.50, max_portfolio_heat_pct=1.00)


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=NLV, cash=NLV, buying_power=NLV,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=NLV, weekly_start_equity=NLV,
        open_risk=OPEN_RISK, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _triggered_candidate(entry: float, stop: float) -> TradeCandidate:
    return TradeCandidate(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status="TRIGGERED", structural_level=stop,
        entry_zone_low=entry, entry_zone_high=entry, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED", "price has pulled back into the support zone"],
        reasons_against=[], conditions_to_wait_for=[],
    )


def _decide_for(entry: float, stop: float, account: AccountRiskState):
    """Builds an otherwise-fully-qualifying decision: valid entry, structural
    stop, a target well above the policy's min_rr, HIGH quality, no
    daily/weekly loss blockers, no max-position/trade blockers, NORMAL kill
    switch — isolating the portfolio-heat gate as the only thing under test."""
    risk_per_share = abs(entry - stop)
    invalidation = InvalidationResult(
        stop_price=stop, structural_level=stop, atr_buffer=0.0, reason="below structural support",
    )
    reward = risk_per_share * 3  # comfortably above DEFAULT_RISK_POLICY.min_rr=1.5
    target = TargetResult(
        price=entry + reward, reason="nearest resistance zone edge", distance=reward,
        reward_per_unit=reward, r_multiple=reward / risk_per_share,
    )
    quality = QualityScoreResult(score=80, band="HIGH", components={})
    kill_switch = evaluate_kill_switch(account, POLICY, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=_triggered_candidate(entry, stop), invalidation=invalidation,
        targets=[target], risk_policy=POLICY, account=account, kill_switch=kill_switch, quality=quality,
        entry_price=entry,
    )
    return result, kill_switch, target


# ===================== Main acceptance scenario (§1-7) =====================
# entry=200, stop=99 -> risk/share=$101. Remaining heat budget is only $100
# (max_heat $1000 - open_risk $900), so even a single share ($101 of risk)
# would push heat to 1.001% -> strictly above the 1.000% ceiling -> sizing
# floors to 0 shares -> REJECT/PORTFOLIO_HEAT_EXCEEDED.

_ENTRY, _STOP = 200.0, 99.0


def test_1_current_portfolio_heat_is_0_900_pct():
    account = _account()
    assert round(account.portfolio_heat_pct, 3) == 0.900


def test_2_through_7_full_acceptance_report():
    account = _account()
    result, kill_switch, target = _decide_for(_ENTRY, _STOP, account)
    ps = result.position_size

    risk_per_share = abs(_ENTRY - _STOP)
    heat_before = account.portfolio_heat_pct
    # Actual post-trade heat (0 shares were sized, so it's unchanged) ...
    heat_after_actual = compute_post_trade_heat_pct(account.open_risk, ps.capital_at_risk, account.net_liquidation_value)
    # ... and the heat a single share WOULD have produced, which is exactly
    # why the sizing algorithm floored to 0 rather than 1.
    heat_if_one_share = compute_post_trade_heat_pct(account.open_risk, risk_per_share, account.net_liquidation_value)

    report = (
        f"entry={_ENTRY} stop={_STOP} risk/share={risk_per_share} "
        f"proposed_shares={ps.shares} capital_at_risk={ps.capital_at_risk} "
        f"heat_before={heat_before:.3f}% heat_after={heat_after_actual:.3f}% "
        f"(1-share attempt would be {heat_if_one_share:.3f}%) "
        f"binding_constraint={ps.binding_constraint} decision={result.decision}"
    )
    print(report)

    # 2. proposed trade capital-at-risk is calculated (sizing ran; 0 shares is
    #    the correctly-computed answer, not a missing/skipped calculation)
    assert ps is not None
    assert ps.capital_at_risk == 0.0
    assert ps.shares == 0

    # 3. post-trade portfolio heat is calculated
    assert round(heat_after_actual, 3) == 0.900  # no shares taken -> unchanged from heat_before
    assert round(heat_if_one_share, 3) == 1.001  # the attempted single-share heat that triggered the block

    # 4. post-trade heat (of the smallest achievable trade) > 1.000% -> REJECT
    assert heat_if_one_share > POLICY.max_portfolio_heat_pct
    assert result.decision == DECISION_REJECT

    # 5. no_trade_reasons includes PORTFOLIO_HEAT_EXCEEDED
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value in result.no_trade_reasons

    # 6. QUALIFIED is impossible
    assert result.decision != DECISION_QUALIFIED

    # 7. exact figures, all present in `report` above
    assert ps.binding_constraint == "portfolio_heat"
    assert ps.risk_per_share == risk_per_share


# ===================== Boundary cases A/B/C =====================
# All three reuse the exact same NLV/open_risk/policy fixture — only
# entry/stop (hence risk/share) differ, to land the ACTUAL sizing result at
# each side of the 1.000% ceiling.


def test_case_a_post_trade_heat_exactly_at_limit_is_allowed():
    # entry=20, stop=19 -> risk/share=$1 -> floor($100 remaining / $1) = 100
    # shares exactly -> capital_at_risk=$100 -> heat_after = (900+100)/100000 = 1.000% exactly.
    account = _account()
    result, _, _ = _decide_for(20.0, 19.0, account)
    ps = result.position_size
    heat_after = compute_post_trade_heat_pct(account.open_risk, ps.capital_at_risk, account.net_liquidation_value)

    assert ps.shares == 100
    assert ps.binding_constraint == "portfolio_heat"
    assert round(heat_after, 3) == 1.000
    assert heat_after == POLICY.max_portfolio_heat_pct  # exactly at, not past
    assert result.decision != DECISION_REJECT
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value not in result.no_trade_reasons
    assert result.decision == DECISION_QUALIFIED  # exactly-at-limit is ALLOWED


def test_case_b_post_trade_heat_just_below_limit_is_allowed():
    # entry=111, stop=100 -> risk/share=$11 -> floor($100/$11)=9 shares ->
    # capital_at_risk=$99 -> heat_after = 999/100000 = 0.999%.
    account = _account()
    result, _, _ = _decide_for(111.0, 100.0, account)
    ps = result.position_size
    heat_after = compute_post_trade_heat_pct(account.open_risk, ps.capital_at_risk, account.net_liquidation_value)

    assert ps.shares == 9
    assert ps.capital_at_risk == 99.0
    assert ps.binding_constraint == "portfolio_heat"
    assert round(heat_after, 3) == 0.999
    assert heat_after < POLICY.max_portfolio_heat_pct
    assert result.decision == DECISION_QUALIFIED
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value not in result.no_trade_reasons


def test_case_c_post_trade_heat_just_above_limit_is_blocked():
    # Same fixture as the main scenario: entry=200, stop=99 -> risk/share=$101.
    # A single share would land at (900+101)/100000 = 1.001% -> strictly past
    # the 1.000% ceiling -> sizing floors to 0 shares -> REJECT.
    account = _account()
    result, _, _ = _decide_for(_ENTRY, _STOP, account)
    ps = result.position_size
    risk_per_share = abs(_ENTRY - _STOP)
    heat_if_one_share = compute_post_trade_heat_pct(account.open_risk, risk_per_share, account.net_liquidation_value)

    assert round(heat_if_one_share, 3) == 1.001
    assert ps.shares == 0
    assert ps.binding_constraint == "portfolio_heat"
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value in result.no_trade_reasons


def test_boundary_policy_is_consistent_between_pure_function_and_sizing():
    # The pure arithmetic at exactly the 3 boundary dollar amounts...
    assert round(compute_post_trade_heat_pct(900, 99, 100_000), 3) == 0.999
    assert round(compute_post_trade_heat_pct(900, 100, 100_000), 3) == 1.000
    assert round(compute_post_trade_heat_pct(900, 101, 100_000), 3) == 1.001

    # ...matches exactly what the real sizing algorithm produces in cases A/B/C
    # above: 0.999%/1.000% are both sized and allowed, only >1.000% is blocked.
    account = _account()
    result_a, _, _ = _decide_for(20.0, 19.0, account)
    result_b, _, _ = _decide_for(111.0, 100.0, account)
    result_c, _, _ = _decide_for(_ENTRY, _STOP, account)
    assert result_a.decision == DECISION_QUALIFIED  # 1.000% -> allowed
    assert result_b.decision == DECISION_QUALIFIED  # 0.999% -> allowed
    assert result_c.decision == DECISION_REJECT  # would-be 1.001% -> blocked
