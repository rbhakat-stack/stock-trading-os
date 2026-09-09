"""Deterministic Phase 3 acceptance tests for the remaining trade-plan math
and qualification gates: min R:R, position sizing (risk budget, symbol
exposure, gross exposure, buying power), stop/target validation, and the
full positive QUALIFIED path.

Every fixture is an otherwise-fully-clean TRIGGERED candidate that WOULD
qualify if the one gate under test didn't block it: valid entry, structural
stop, a target above min_rr, HIGH quality, NORMAL kill switch, zero open
risk, zero open positions/trades today, flat daily/weekly equity, and full
buying power — unless the section under test deliberately varies one of
those to exercise a specific constraint.

============================================================================
TWO GENUINE BUGS FOUND AND FIXED HERE (engine/trade/decision.py):

1. An inverted/wrong-side stop (e.g. a LONG candidate whose stop sits AT OR
   ABOVE entry) previously sailed through to QUALIFIED with a computed,
   positive position size. `risk_per_share = abs(entry - stop)` in
   engine/risk/sizing.py is symmetric by construction, so it can't itself
   detect an inverted stop — nothing upstream of it checked direction either.
   Fixed by adding an explicit direction-vs-stop validity check in
   make_trade_decision, right after the "invalidation is None" check: for
   LONG, stop_price >= entry_price is now rejected (NO_STRUCTURAL_STOP);
   for SHORT, stop_price <= entry_price is rejected the same way.

2. Whenever compute_position_size() floored to 0 shares because of a
   genuinely degenerate input (entry==stop, entry<=0, or NLV<=0 —
   binding_constraint=="INVALID_INPUT"), the decision engine reported
   PORTFOLIO_HEAT_EXCEEDED regardless — factually wrong for that case. Fixed
   to report NO_STRUCTURAL_STOP when the cause was invalid input, leaving
   PORTFOLIO_HEAT_EXCEEDED for genuine constraint exhaustion (heat, exposure,
   risk budget, ...) — the behavior tested and accepted in the portfolio-heat
   acceptance round.

Neither fix changes what gets QUALIFIED when the inputs are sane — verified
by the full regression run at the bottom of this session's report.
============================================================================

UPDATE (exposure/leverage hardening round): sections 6 and 7 below originally
documented max_net_exposure_pct and max_leverage as unenforced ("decorative")
fields, since AccountRiskState had no exposure tracking at all at the time.
That gap has since been closed — see tests/test_exposure_leverage_gates.py
for the full boundary-test suite covering the new net_exposure and leverage
constraints (both now real, independent candidates in
engine/risk/sizing.py's `candidates` dict). Sections 6 and 7 here were
updated in place to assert the new, correct enforcement rather than left as
stale characterization tests documenting a bug that no longer exists.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.sizing import compute_position_size
from engine.trade.candidate import TradeCandidate
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult

NLV = 100_000.0


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=NLV, cash=NLV, buying_power=NLV,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=NLV, weekly_start_equity=NLV,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _candidate(direction="LONG", stop=95.0, entry_low=99.5, entry_high=100.0, **overrides):
    defaults = dict(
        setup_type="TREND_PULLBACK_LONG" if direction == "LONG" else "TREND_PULLBACK_SHORT",
        direction=direction, status="TRIGGERED", structural_level=stop,
        entry_zone_low=entry_low, entry_zone_high=entry_high, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED", "price has pulled back into the support zone"],
        reasons_against=[], conditions_to_wait_for=[],
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def _invalidation(stop=95.0, structural_level=95.0):
    return InvalidationResult(stop_price=stop, structural_level=structural_level, atr_buffer=0.2, reason="below structural support")


def _target(price=115.0, entry=100.0, rr=None):
    reward = price - entry
    r_multiple = rr if rr is not None else round(reward / (entry - 95.0), 3)
    return TargetResult(price=price, reason="nearest resistance zone edge", distance=reward, reward_per_unit=reward, r_multiple=r_multiple)


def _quality(score=85, band="HIGH"):
    return QualityScoreResult(score=score, band=band, components={})


def _decide(entry=100.0, stop=95.0, direction="LONG", rr1=3.0, risk_policy=DEFAULT_RISK_POLICY, account=None,
            targets=None, quality=None, invalidation="default"):
    account = account or _account()
    candidate = _candidate(direction=direction, stop=stop, entry_low=entry - 0.5, entry_high=entry)
    inval = _invalidation(stop=stop, structural_level=stop) if invalidation == "default" else invalidation
    if targets is None:
        risk_per_share = abs(entry - stop)
        reward = risk_per_share * rr1
        price = entry + reward if direction == "LONG" else entry - reward
        targets = [TargetResult(price=price, reason="x", distance=reward, reward_per_unit=reward, r_multiple=rr1)]
    quality = quality or _quality()
    kill_switch = evaluate_kill_switch(account, risk_policy, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=inval, targets=targets,
        risk_policy=risk_policy, account=account, kill_switch=kill_switch, quality=quality, entry_price=entry,
    )
    return result, kill_switch


def _report(label, **fields):
    print(f"[{label}] " + " ".join(f"{k}={v}" for k, v in fields.items()))


# ============================================================================
# 1. MINIMUM R:R GATE
# ============================================================================
# min_rr=1.50 (DEFAULT_RISK_POLICY). Comparison in decision.py is `rr1 <
# min_rr` (strict less-than) -> exactly-at-threshold is ALLOWED.


def test_1_rr_below_minimum_1_49_rejects():
    result, _ = _decide(rr1=1.49)
    _report("1 rr1=1.49", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.RR_BELOW_MINIMUM.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_1_rr_exactly_at_minimum_1_50_is_allowed():
    result, _ = _decide(rr1=1.50)
    _report("1 rr1=1.50 (exact threshold)", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert NoTradeReason.RR_BELOW_MINIMUM.value not in result.no_trade_reasons
    assert result.decision == DECISION_QUALIFIED  # exactly-at-threshold is ALLOWED


def test_1_rr_above_minimum_1_51_is_allowed():
    result, _ = _decide(rr1=1.51)
    _report("1 rr1=1.51", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_QUALIFIED


# ============================================================================
# 2. POSITION SIZING — RISK BUDGET
# ============================================================================
# NLV=100000, risk_per_trade_pct=0.25% -> $250 max planned loss.
# entry=100, stop=99.50 -> risk/share=$0.50 -> raw = 250/0.50 = 500 shares.
# Other constraints loosened to 100% so risk_per_trade is unambiguously binding.

_POLICY_2 = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=0.25, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_portfolio_heat_pct=5.0)


def test_2_risk_budget_raw_size_is_500_shares_capital_at_risk_250():
    r = compute_position_size(entry=100.0, stop=99.50, net_liquidation_value=NLV, risk_policy=_POLICY_2, buying_power=NLV)
    _report("2 risk budget", shares=r.shares, capital_at_risk=r.capital_at_risk, binding=r.binding_constraint)
    assert r.binding_constraint == "risk_per_trade"
    assert r.shares == 500
    assert r.capital_at_risk == 250.0
    assert r.capital_at_risk <= 250.0


def test_2_position_size_is_floored_never_rounded_up():
    # risk/share=$0.33 -> raw = 250/0.33 = 757.5757... -> must floor to 757, never round to 758.
    r = compute_position_size(entry=100.0, stop=99.67, net_liquidation_value=NLV, risk_policy=_POLICY_2, buying_power=NLV)
    _report("2 floor check", shares=r.shares, capital_at_risk=r.capital_at_risk)
    assert r.shares == 757
    assert r.capital_at_risk <= 250.0  # flooring must never let capital-at-risk exceed the budget
    assert r.capital_at_risk == round(757 * 0.33, 2)


def test_2_zero_risk_per_share_rejected():
    r = compute_position_size(entry=100.0, stop=100.0, net_liquidation_value=NLV, risk_policy=_POLICY_2, buying_power=NLV)
    assert r.shares == 0
    assert r.binding_constraint == "INVALID_INPUT"


def test_2_risk_per_share_is_never_negative_abs_by_construction():
    # risk_per_share = abs(entry - stop) can never be negative regardless of
    # which side stop is on — confirmed directly, since sizing.py has no
    # direction awareness (that's exactly why the new decision-level
    # direction check in section 9 is necessary).
    r_a = compute_position_size(entry=100.0, stop=95.0, net_liquidation_value=NLV, risk_policy=_POLICY_2, buying_power=NLV)
    r_b = compute_position_size(entry=100.0, stop=105.0, net_liquidation_value=NLV, risk_policy=_POLICY_2, buying_power=NLV)
    assert r_a.risk_per_share == r_b.risk_per_share == 5.0


# ============================================================================
# 3. SINGLE-SYMBOL EXPOSURE CONSTRAINT
# ============================================================================
# max_symbol_exposure_pct=10% (DEFAULT_RISK_POLICY), NLV=100000 -> $10000 cap.
# entry=100 -> max shares by symbol exposure = 100. entry=100, stop=99
# (risk/share=$1) -> risk-based size would allow 500 shares (DEFAULT
# risk_per_trade_pct=0.5% -> $500/$1), but symbol exposure caps it to 100.


def test_3_symbol_exposure_binds_even_though_risk_sizing_allows_more():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=DEFAULT_RISK_POLICY, buying_power=NLV)
    _report(
        "3 symbol exposure", risk_based_size=r.evidence["candidates_shares"]["risk_per_trade"],
        symbol_exposure_size=r.evidence["candidates_shares"]["symbol_exposure"], final_size=r.shares,
        position_value=r.position_value, capital_at_risk=r.capital_at_risk, binding=r.binding_constraint,
    )
    assert r.evidence["candidates_shares"]["risk_per_trade"] == 500.0
    assert r.evidence["candidates_shares"]["symbol_exposure"] == 100.0
    assert r.shares == 100
    assert r.binding_constraint == "symbol_exposure"
    assert r.position_value == 10_000.0
    assert r.capital_at_risk == 100.0


# ============================================================================
# 4. BUYING POWER CONSTRAINT
# ============================================================================
# buying_power=$5000, entry=100 -> max shares by buying power = 50. All other
# constraints loosened so buying_power is unambiguously binding.

_POLICY_4 = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=100.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_portfolio_heat_pct=100.0)


def test_4_buying_power_binds():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_4, buying_power=5_000.0)
    _report("4 buying power", shares=r.shares, binding=r.binding_constraint)
    assert r.shares == 50
    assert r.shares <= 50
    assert r.binding_constraint == "buying_power"


# ============================================================================
# 5. GROSS EXPOSURE CONSTRAINT
# ============================================================================
# Models $96000 of gross exposure already used by OTHER open positions
# (existing_gross_exposure_value), leaving only $4000 of the $100000 (100%)
# gross budget for this trade -> 40 shares at entry=100 -- tighter than risk,
# buying power, symbol exposure, and heat, which are all loosened to be
# non-binding.

_POLICY_5 = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=100.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_portfolio_heat_pct=100.0)


def test_5_gross_exposure_binds():
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_5, buying_power=NLV,
        existing_symbol_notional_signed=0.0, existing_gross_exposure_value=96_000.0,
    )
    _report("5 gross exposure", shares=r.shares, binding=r.binding_constraint, evidence=r.evidence["candidates_shares"])
    assert r.binding_constraint == "gross_exposure"
    assert r.shares == 40


# ============================================================================
# 6. NET EXPOSURE CONSTRAINT — now enforced (see test_exposure_leverage_gates.py
# for the full boundary suite; these two tests just confirm the field is no
# longer inert, replacing the old characterization tests that documented it
# as unenforced).
# ============================================================================

_POLICY_6 = replace(
    DEFAULT_RISK_POLICY, risk_per_trade_pct=100.0, max_symbol_exposure_pct=100.0,
    max_gross_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0, max_net_exposure_pct=60.0,
)


def test_6_max_net_exposure_pct_now_binds_sizing():
    # existing_net_exposure_value=$50000 (net long), max_net_exposure_pct=60%
    # -> $60000 ceiling -> $10000 of remaining net-exposure budget -> 100
    # shares at entry=100, tighter than every other (loosened) constraint.
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_6, buying_power=NLV,
        direction="LONG", existing_net_exposure_value=50_000.0,
    )
    _report("6 net exposure binds", shares=r.shares, binding=r.binding_constraint, candidates=r.evidence["candidates_shares"])
    assert r.binding_constraint == "net_exposure"
    assert r.shares == 100
    assert "net_exposure" in r.evidence["candidates_shares"]


def test_6_tighter_max_net_exposure_pct_produces_a_smaller_or_equal_size():
    loose = replace(_POLICY_6, max_net_exposure_pct=100.0)
    tight = replace(_POLICY_6, max_net_exposure_pct=0.01)
    r_loose = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=loose, buying_power=NLV, direction="LONG", existing_net_exposure_value=0.0)
    r_tight = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=tight, buying_power=NLV, direction="LONG", existing_net_exposure_value=0.0)
    _report("6 tight vs loose net exposure", loose_shares=r_loose.shares, tight_shares=r_tight.shares)
    assert r_tight.shares < r_loose.shares
    assert r_tight.shares == 0  # 0.01% of 100000 = $10 budget -> 0 shares at $100/share


# ============================================================================
# 7. LEVERAGE CONSTRAINT — now independently enforced (not just an accidental
# side effect of buying power). See test_exposure_leverage_gates.py for the
# full boundary suite.
# ============================================================================


def test_7_max_leverage_now_binds_sizing():
    loose = replace(DEFAULT_RISK_POLICY, max_leverage=4.0)
    tight = replace(DEFAULT_RISK_POLICY, max_leverage=0.01)
    r_loose = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=loose, buying_power=NLV)
    r_tight = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=tight, buying_power=NLV)
    _report("7 leverage now binds", loose_shares=r_loose.shares, tight_shares=r_tight.shares)
    assert r_tight.shares < r_loose.shares
    assert "leverage" in r_loose.evidence["candidates_shares"]


def test_7_margin_account_is_now_capped_at_1x_nlv_by_leverage():
    # buying_power > NLV (a margin account), AND symbol/gross exposure caps
    # loosened past 100% -- previously this sized straight past 1x notional
    # exposure with zero leverage enforcement. Now the dedicated `leverage`
    # constraint catches it: max_leverage=1.0 -> position_value is capped at
    # exactly 1x NLV ($100,000), never the $300,000 buying power alone would allow.
    margin_policy = replace(
        DEFAULT_RISK_POLICY, max_leverage=1.0, risk_per_trade_pct=100.0,
        max_symbol_exposure_pct=300.0, max_gross_exposure_pct=300.0, max_portfolio_heat_pct=100.0, max_net_exposure_pct=300.0,
    )
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=margin_policy, buying_power=300_000.0)  # 3x NLV buying power
    _report("7 margin account now capped", shares=r.shares, position_value=r.position_value, implied_leverage=r.position_value / NLV, binding=r.binding_constraint)
    assert r.binding_constraint == "leverage"
    assert r.position_value == NLV  # exactly 1x, never more, despite 3x buying power
    assert r.position_value / NLV <= margin_policy.max_leverage


# ============================================================================
# 8. BINDING-CONSTRAINT CONSISTENCY / TIE-BREAK DETERMINISM
# ============================================================================
# min(dict, key=...) resolves ties by first-occurrence in insertion order:
# risk_per_trade, buying_power, symbol_exposure, gross_exposure, portfolio_heat.


def test_8_final_size_is_the_minimum_across_all_active_constraints():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=DEFAULT_RISK_POLICY, buying_power=NLV)
    assert r.shares == int(min(r.evidence["candidates_shares"].values()))


def test_8_tie_between_risk_per_trade_and_symbol_exposure_resolves_to_risk_per_trade():
    # risk_per_trade_pct=0.5% of 100000 = $500 budget; risk/share=$5 -> 100 shares.
    # max_symbol_exposure_pct=10% of 100000 = $10000; entry=100 -> 100 shares. Tie.
    tie_policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=0.5, max_symbol_exposure_pct=10.0, max_gross_exposure_pct=100.0, max_portfolio_heat_pct=100.0)
    r = compute_position_size(entry=100.0, stop=95.0, net_liquidation_value=NLV, risk_policy=tie_policy, buying_power=NLV)
    _report("8 tie (risk_per_trade vs symbol_exposure)", shares=r.shares, binding=r.binding_constraint, candidates=r.evidence["candidates_shares"])
    assert r.evidence["candidates_shares"]["risk_per_trade"] == r.evidence["candidates_shares"]["symbol_exposure"] == 100.0
    assert r.binding_constraint == "risk_per_trade"  # first-declared candidate wins the tie


def test_8_tie_between_buying_power_and_gross_exposure_resolves_to_buying_power():
    # buying_power=$10000 at entry=100 -> 100 shares. gross_exposure budget
    # also set to exactly $10000 remaining -> 100 shares. Tie.
    tie_policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=100.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_portfolio_heat_pct=100.0)
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=tie_policy, buying_power=10_000.0,
        existing_gross_exposure_value=NLV - 10_000.0,
    )
    _report("8 tie (buying_power vs gross_exposure)", shares=r.shares, binding=r.binding_constraint, candidates=r.evidence["candidates_shares"])
    assert r.evidence["candidates_shares"]["buying_power"] == r.evidence["candidates_shares"]["gross_exposure"] == 100.0
    assert r.binding_constraint == "buying_power"  # declared before gross_exposure


# ============================================================================
# 9. NO STOP / INVALID STOP
# ============================================================================


def test_9_no_invalidation_rejects_no_structural_stop():
    account = _account()
    candidate = _candidate()
    quality = _quality()
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=None, targets=[_target()],
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=quality, entry_price=100.0,
    )
    _report("9 stop=None", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons
    assert result.position_size is None


def test_9_stop_equals_entry_rejects_safely_no_divide_by_zero():
    result, _ = _decide(entry=100.0, stop=100.0)  # raises nothing; must not crash
    _report("9 stop==entry", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert result.decision != DECISION_QUALIFIED
    assert result.position_size is None or result.position_size.shares == 0


def test_9_long_stop_at_entry_is_invalid():
    result, _ = _decide(entry=100.0, stop=100.0, direction="LONG")
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons


def test_9_long_stop_above_entry_is_invalid():
    # The bug fixed in this round: stop ABOVE entry for a LONG must never qualify.
    result, _ = _decide(entry=100.0, stop=105.0, direction="LONG", rr1=3.0)
    _report("9 LONG stop>entry", decision=result.decision, no_trade_reasons=result.no_trade_reasons, position_size=result.position_size)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED
    assert result.position_size is None


def test_9_short_stop_at_entry_is_invalid():
    result, _ = _decide(entry=100.0, stop=100.0, direction="SHORT", rr1=3.0)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons


def test_9_short_stop_below_entry_is_invalid():
    result, _ = _decide(entry=100.0, stop=95.0, direction="SHORT", rr1=3.0)
    _report("9 SHORT stop<entry", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED
    assert result.position_size is None


def test_9_valid_long_stop_below_entry_is_allowed():
    result, _ = _decide(entry=100.0, stop=95.0, direction="LONG", rr1=3.0)
    assert result.decision == DECISION_QUALIFIED


def test_9_valid_short_stop_above_entry_is_allowed():
    result, _ = _decide(entry=100.0, stop=105.0, direction="SHORT", rr1=3.0)
    assert result.decision == DECISION_QUALIFIED


def test_9_no_invalid_stop_ever_produces_a_positive_position_size():
    invalid_cases = [
        dict(entry=100.0, stop=100.0, direction="LONG"),
        dict(entry=100.0, stop=105.0, direction="LONG"),
        dict(entry=100.0, stop=100.0, direction="SHORT"),
        dict(entry=100.0, stop=95.0, direction="SHORT"),
    ]
    for case in invalid_cases:
        result, _ = _decide(rr1=3.0, **case)
        assert result.position_size is None or result.position_size.shares == 0, case


# ============================================================================
# 10. NO TARGET
# ============================================================================


def test_10_no_targets_rejects_no_valid_target_not_fabricated():
    account = _account()
    candidate = _candidate()
    quality = _quality()
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=_invalidation(), targets=[],
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=quality, entry_price=100.0,
    )
    _report("10 no targets", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_VALID_TARGET.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED
    assert result.position_size is None


# ============================================================================
# 11. POSITIVE QUALIFIED PATH
# ============================================================================


def test_11_positive_qualified_path_full_report_and_invariants():
    account = _account()
    entry, stop = 100.0, 95.0
    candidate = _candidate(direction="LONG", stop=stop, entry_low=99.5, entry_high=entry)
    invalidation = InvalidationResult(stop_price=stop, structural_level=stop, atr_buffer=0.2, reason=f"below structural support at {stop}")
    target1 = TargetResult(price=115.0, reason="nearest resistance zone edge", distance=15.0, reward_per_unit=15.0, r_multiple=3.0)
    target2 = TargetResult(price=125.0, reason="nearest resistance zone edge", distance=25.0, reward_per_unit=25.0, r_multiple=5.0)
    quality = QualityScoreResult(score=85, band="HIGH", components={"structure": 0.9})
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)

    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=[target1, target2],
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=quality, entry_price=entry,
    )
    ps = result.position_size
    heat_before = account.portfolio_heat_pct
    heat_after = round(((account.open_risk + ps.capital_at_risk) / account.net_liquidation_value) * 100, 3)

    report = (
        f"entry={entry} entry_zone=({candidate.entry_zone_low},{candidate.entry_zone_high}) stop={stop} "
        f"invalidation_reason='{invalidation.reason}' target1={target1.price} target2={target2.price} "
        f"risk_per_unit={ps.risk_per_share} rr1={target1.r_multiple} rr2={target2.r_multiple} "
        f"position_size={ps.shares} position_value={ps.position_value} capital_at_risk={ps.capital_at_risk} "
        f"quality_score={quality.score} quality_band={quality.band} "
        f"portfolio_heat_before={heat_before:.3f}% portfolio_heat_after={heat_after:.3f}% "
        f"binding_constraint={ps.binding_constraint} statistical_validation=NOT_YET_AVAILABLE "
        f"manual_review_required=true decision={result.decision}"
    )
    print(report)

    # NOTE on manual_review_required: there is no such field on any domain
    # object (TradeDecisionResult, TradePlanningSnapshot). It is an
    # unconditional UI notice shown for every QUALIFIED decision
    # (app/pages/trade_planner.py: "MANUAL REVIEW REQUIRED..."), not
    # data computed or persisted per-plan -- by design, since it's always
    # true for QUALIFIED and never for any other decision. Asserted here via
    # the decision itself, which is the actual trigger condition in the UI.
    assert result.decision == DECISION_QUALIFIED  # -> manual_review_required is unconditionally true in the UI

    assert stop is not None
    assert target1 is not None
    assert target1.r_multiple >= DEFAULT_RISK_POLICY.min_rr
    assert ps.shares > 0
    max_trade_loss = account.net_liquidation_value * (DEFAULT_RISK_POLICY.risk_per_trade_pct / 100)
    assert ps.capital_at_risk <= max_trade_loss + 1e-9
    assert heat_after <= DEFAULT_RISK_POLICY.max_portfolio_heat_pct
    assert account.open_positions < DEFAULT_RISK_POLICY.max_open_positions
    assert account.trades_today < DEFAULT_RISK_POLICY.max_trades_per_day
    assert account.current_daily_drawdown_pct == 0.0
    assert account.current_weekly_drawdown_pct == 0.0
    assert result.no_trade_reasons == []
