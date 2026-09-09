"""Phase 3 per-symbol standing-exposure-breach visibility (final narrow fix).

Root cause confirmed before any code changed: candidate-level sizing already
enforces max_single_symbol_exposure_pct correctly (engine/risk/sizing.py's
`symbol_exposure` binding constraint, evaluated per-trade against the SAME
signed-notional arithmetic used here). This was purely a STANDING-breach
VISIBILITY gap: engine/risk/limits.py::evaluate_current_policy_breaches
never looked at individual positions at all — only account-wide aggregates
(gross/net/leverage/open_positions) — so a symbol already over its own
concentration limit was invisible until a candidate for THAT exact symbol
was analyzed and floored to 0 shares. No trade-sizing or trade-decision
logic was changed to fix this — only evaluate_current_policy_breaches
gained an optional `positions` parameter that adds per-symbol PolicyBreach
entries, purely additive and purely for display.
"""
from dataclasses import replace
from datetime import datetime, timezone

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.limits import evaluate_current_policy_breaches, symbol_exposure_pct
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import AccountPosition, derive_exposure, derive_open_risk
from engine.trade.candidate import CandidateStatus, TradeCandidate
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.targets import TargetResult

_AS_OF = datetime(2024, 1, 15, tzinfo=timezone.utc)
_NLV = 100_000.0


def _position(symbol, qty, price, stop):
    return AccountPosition(symbol=symbol, quantity_signed=qty, reference_price=price, updated_at=_AS_OF, planned_stop_price=stop)


# Exact §5 fixture from the report.
_FIXTURE_POSITIONS = [
    _position("AMZ", 100, 200, 195),
    _position("GOOG", 25, 200, 195),
    _position("NVDA", 200, 400, 395),
    _position("SPY", 80, 100, 95),
    _position("TSLA", 50, 100, 95),
]
_POLICY = replace(
    DEFAULT_RISK_POLICY, max_symbol_exposure_pct=10.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0,
    max_leverage=1.0, max_open_positions=5, max_portfolio_heat_pct=3.0,
)


def _account_for(positions):
    exposure = derive_exposure(positions)
    open_risk = derive_open_risk(positions)
    return AccountRiskState(
        source="MANUAL", net_liquidation_value=_NLV, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=_NLV, weekly_start_equity=_NLV,
        open_risk=open_risk.open_risk, open_positions=exposure.open_positions, trades_today=0, consecutive_losses=0,
        as_of=_AS_OF, long_exposure_notional=exposure.long_exposure_notional, short_exposure_notional=exposure.short_exposure_notional,
    )


def _breach_by_code_symbol(breaches, code, symbol=None):
    return [b for b in breaches if b.code == code and (symbol is None or b.symbol == symbol)]


# ===================== §1 confirm sizing already correct, untouched =====================


def test_sizing_symbol_exposure_binding_constraint_is_unmodified_reference():
    # Not a new test of sizing.py's internals (out of scope, untouched) —
    # just confirms the module and its public constraint name still exist
    # exactly as before, so this round's report's §1.A claim is checkable.
    from engine.risk.sizing import compute_position_size
    result = compute_position_size(
        entry=200.0, stop=195.0, net_liquidation_value=_NLV, risk_policy=_POLICY, buying_power=100_000.0,
        direction="LONG", existing_symbol_notional_signed=95_000.0,  # already 95% of NLV in this symbol
    )
    assert result.binding_constraint == "symbol_exposure"
    assert result.shares == 0  # only ~5% of NLV remains before hitting the 10% cap... actually exhausted here


# ===================== §5/§15 Tests A-C: exact fixture =====================


def test_a_amz_20_percent_is_a_breach():
    account = _account_for(_FIXTURE_POSITIONS)
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=_FIXTURE_POSITIONS)
    matches = _breach_by_code_symbol(breaches, "SINGLE_SYMBOL_EXPOSURE_EXCEEDED", "AMZ")
    assert len(matches) == 1
    assert matches[0].current_value == 20.0
    assert matches[0].limit_value == 10.0
    assert "AMZ" in matches[0].message and "20.000%" in matches[0].message and "10.000%" in matches[0].message


def test_b_nvda_80_percent_is_a_breach():
    account = _account_for(_FIXTURE_POSITIONS)
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=_FIXTURE_POSITIONS)
    matches = _breach_by_code_symbol(breaches, "SINGLE_SYMBOL_EXPOSURE_EXCEEDED", "NVDA")
    assert len(matches) == 1
    assert matches[0].current_value == 80.0


def test_c_spy_goog_tsla_are_not_breaches():
    account = _account_for(_FIXTURE_POSITIONS)
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=_FIXTURE_POSITIONS)
    breached_symbols = {b.symbol for b in breaches if b.code == "SINGLE_SYMBOL_EXPOSURE_EXCEEDED"}
    assert breached_symbols == {"AMZ", "NVDA"}
    assert "SPY" not in breached_symbols
    assert "GOOG" not in breached_symbols
    assert "TSLA" not in breached_symbols


# ===================== §6/§15 Test D: exact-limit boundary =====================


def test_d_exactly_at_limit_is_not_exceeded():
    # AMZ +50 @ 200 = $10,000 notional = exactly 10% of $100,000 NLV.
    position = _position("AMZ", 50, 200, 195)
    account = _account_for([position])
    pct = symbol_exposure_pct(position, _NLV)
    assert pct == 10.0
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=[position])
    assert not _breach_by_code_symbol(breaches, "SINGLE_SYMBOL_EXPOSURE_EXCEEDED")


def test_d_just_over_limit_is_exceeded():
    # One cent of notional over 10% must flip to EXCEEDED — confirms the
    # boundary is a genuine strict `>`, not an off-by-something.
    position = _position("AMZ", 50, 200.01, 195)
    account = _account_for([position])
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=[position])
    assert _breach_by_code_symbol(breaches, "SINGLE_SYMBOL_EXPOSURE_EXCEEDED", "AMZ")


# ===================== §10/§15 Test E: short position sign doesn't matter =====================


def test_e_short_position_breach_uses_absolute_notional():
    position = _position("NVDA", -200, 400, 405)  # SHORT: stop above entry
    account = _account_for([position])
    pct = symbol_exposure_pct(position, _NLV)
    assert pct == 80.0
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=[position])
    matches = _breach_by_code_symbol(breaches, "SINGLE_SYMBOL_EXPOSURE_EXCEEDED", "NVDA")
    assert len(matches) == 1
    assert matches[0].current_value == 80.0


# ===================== §7/§15 Test F: multiple breaches all returned =====================


def test_f_both_violating_symbols_returned_not_just_the_first():
    account = _account_for(_FIXTURE_POSITIONS)
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=_FIXTURE_POSITIONS)
    symbol_breaches = [b for b in breaches if b.code == "SINGLE_SYMBOL_EXPOSURE_EXCEEDED"]
    assert len(symbol_breaches) == 2
    assert {b.symbol for b in symbol_breaches} == {"AMZ", "NVDA"}


# ===================== §7/§15 Test G: existing account-wide breaches preserved =====================


def test_g_existing_account_wide_breaches_remain_alongside_symbol_breaches():
    account = _account_for(_FIXTURE_POSITIONS)
    breaches = evaluate_current_policy_breaches(account, _POLICY, positions=_FIXTURE_POSITIONS)
    codes = {b.code for b in breaches}
    assert "MAX_POSITIONS_REACHED" in codes  # 5/5
    assert "GROSS_EXPOSURE_EXCEEDED" in codes  # 118% > 100%
    assert "NET_EXPOSURE_EXCEEDED" in codes  # 118% > 100%
    assert "LEVERAGE_LIMIT_EXCEEDED" in codes  # 1.18x > 1.0x
    assert "PORTFOLIO_HEAT_EXCEEDED" not in codes  # 2.275% < 3.0%, correctly NOT a breach
    # exact §5 math, unchanged by this round's addition
    assert account.gross_exposure_pct == 118.0
    assert account.net_exposure_pct == 118.0
    assert round(account.current_leverage, 2) == 1.18
    assert account.open_positions == 5
    assert round(account.portfolio_heat_pct, 3) == 2.275


def test_g_omitting_positions_preserves_old_behavior_exactly():
    # Backward compatibility: every pre-existing caller that doesn't pass
    # `positions` must see IDENTICAL results to before this round.
    account = _account_for(_FIXTURE_POSITIONS)
    breaches_without = evaluate_current_policy_breaches(account, _POLICY)
    assert not any(b.code == "SINGLE_SYMBOL_EXPOSURE_EXCEEDED" for b in breaches_without)
    codes = {b.code for b in breaches_without}
    assert {"MAX_POSITIONS_REACHED", "GROSS_EXPOSURE_EXCEEDED", "NET_EXPOSURE_EXCEEDED", "LEVERAGE_LIMIT_EXCEEDED"} <= codes


# ===================== §8/§15 Test H: trade-level decision reason unchanged =====================


def test_h_triggered_candidate_still_rejects_on_max_positions_reached_only():
    account = _account_for(_FIXTURE_POSITIONS)  # 5 positions, policy max=5
    candidate = TradeCandidate(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status=CandidateStatus.TRIGGERED.value,
        structural_level=98.0, entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED"], reasons_against=[], conditions_to_wait_for=[],
    )
    invalidation = InvalidationResult(stop_price=97.9, structural_level=98.0, atr_buffer=0.1, reason="below support")
    targets = [TargetResult(price=108.17, reason="resistance", distance=9.17, reward_per_unit=9.17, r_multiple=9.081)]
    kill_switch = evaluate_kill_switch(account, _POLICY, data_quality_ok=True)

    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=_POLICY, account=account, kill_switch=kill_switch, quality=None, entry_price=99.0,
    )
    assert decision.decision == DECISION_REJECT
    assert decision.no_trade_reasons == [NoTradeReason.MAX_POSITIONS_REACHED.value]
    # No new reason code leaked in from the standing per-symbol breach —
    # the decision engine was never touched by this round's fix.
    assert "SINGLE_SYMBOL_EXPOSURE_EXCEEDED" not in decision.no_trade_reasons


# ===================== §9 kill switch state unaffected =====================


def test_kill_switch_state_normal_despite_symbol_breaches():
    account = _account_for(_FIXTURE_POSITIONS)
    kill_switch = evaluate_kill_switch(account, _POLICY, data_quality_ok=True)
    assert kill_switch.state == "NORMAL"


# ===================== §15 Test I: clean account QUALIFIED path unaffected =====================


def test_i_clean_account_still_reaches_qualified():
    account = AccountRiskState(
        source="MANUAL", net_liquidation_value=_NLV, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=_NLV, weekly_start_equity=_NLV,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=_AS_OF,
    )
    candidate = TradeCandidate(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status=CandidateStatus.TRIGGERED.value,
        structural_level=98.0, entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED"], reasons_against=[], conditions_to_wait_for=[],
    )
    invalidation = InvalidationResult(stop_price=97.9, structural_level=98.0, atr_buffer=0.1, reason="below support")
    targets = [TargetResult(price=108.17, reason="resistance", distance=9.17, reward_per_unit=9.17, r_multiple=9.081)]
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)

    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=None, entry_price=99.0,
    )
    assert decision.decision == DECISION_QUALIFIED
    assert decision.position_size is not None
    assert decision.position_size.shares > 0

    # And the standing-breach evaluator (even with positions=None, the
    # common no-positions case) reports nothing wrong for this clean account.
    breaches = evaluate_current_policy_breaches(account, DEFAULT_RISK_POLICY)
    assert breaches == []


# ===================== §11 NLV <= 0 fail-closed safety =====================


def test_nlv_zero_never_divides_by_zero_and_never_fabricates_a_fallback():
    position = _position("AMZ", 100, 200, 195)
    assert symbol_exposure_pct(position, 0.0) == 0.0
    assert symbol_exposure_pct(position, -1.0) == 0.0
