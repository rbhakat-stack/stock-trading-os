"""Deterministic acceptance tests for the exposure/leverage hardening round:
max_net_exposure_pct and max_leverage are now real, enforced constraints in
engine/risk/sizing.py, not decorative RiskPolicy fields.

EXPOSURE MODEL: AccountRiskState gained two new fields,
`long_exposure_notional` and `short_exposure_notional` (both >= 0, magnitudes
not signed values). Everything else — gross exposure, net exposure, leverage
— is derived from those two via properties (see engine/risk/account_state.py):
    gross_exposure_notional = long + short
    net_exposure_notional   = long - short          (signed: + = net long)
    gross_exposure_pct      = gross_exposure_notional / NLV * 100
    net_exposure_pct        = abs(net_exposure_notional) / NLV * 100
    current_leverage        = gross_exposure_notional / NLV

BOUNDARY SEMANTICS: exactly-at-limit is ALLOWED for both net_exposure and
leverage, matching the precedent already established for portfolio heat
(engine/risk/sizing.py floors shares to the largest count whose post-trade
figure fits within the remaining budget via a "<=" comparison — a trade
landing exactly on the ceiling is sized and permitted; only a trade where
even 1 share would land strictly past it is blocked).

BINDING-CONSTRAINT PRECEDENCE: ties resolve by first-occurrence in the fixed
insertion order documented in engine/risk/sizing.py's module docstring:
risk_per_trade, buying_power, symbol_exposure, gross_exposure, net_exposure,
portfolio_heat, leverage.
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

# A policy with every OTHER constraint loosened far out of the way (but still
# within the platform validation ceilings in engine/risk/policy.py — the
# final hardening round wired validate_risk_policy into make_trade_decision
# itself, so a policy exceeding those ceilings is now correctly REJECTed
# before ever reaching sizing), so each test below isolates exactly the one
# constraint (net_exposure or leverage) under study.
_LOOSE = replace(
    DEFAULT_RISK_POLICY, risk_per_trade_pct=5.0, max_symbol_exposure_pct=1000.0,
    max_gross_exposure_pct=1000.0, max_portfolio_heat_pct=20.0, max_leverage=4.0, max_net_exposure_pct=1000.0,
)


def _report(label, **fields):
    print(f"[{label}] " + " ".join(f"{k}={v}" for k, v in fields.items()))


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=NLV, cash=NLV, buying_power=NLV,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=NLV, weekly_start_equity=NLV,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
        long_exposure_notional=0.0, short_exposure_notional=0.0,
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _candidate(direction="LONG", stop=99.0, entry=100.0):
    return TradeCandidate(
        setup_type="TREND_PULLBACK_LONG" if direction == "LONG" else "TREND_PULLBACK_SHORT",
        direction=direction, status="TRIGGERED", structural_level=stop,
        entry_zone_low=entry - 0.5, entry_zone_high=entry, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED", "price has pulled back into the support zone"],
        reasons_against=[], conditions_to_wait_for=[],
    )


def _decide(account, risk_policy=DEFAULT_RISK_POLICY, entry=100.0, stop=95.0, direction="LONG", rr1=3.0):
    """Full pipeline: an otherwise-fully-clean TRIGGERED candidate that would
    QUALIFY if no gate blocked it — valid entry, structural stop, target above
    min_rr, HIGH quality, NORMAL kill switch (flat daily/weekly equity, no
    consecutive losses). existing_gross/net_exposure_value are derived from
    `account`, exactly as engine/trade/planner.py now does for real."""
    candidate = _candidate(direction=direction, stop=stop, entry=entry)
    invalidation = InvalidationResult(stop_price=stop, structural_level=stop, atr_buffer=0.0, reason="below structural support")
    risk_per_share = abs(entry - stop)
    reward = risk_per_share * rr1
    price = entry + reward if direction == "LONG" else entry - reward
    target = TargetResult(price=price, reason="x", distance=reward, reward_per_unit=reward, r_multiple=rr1)
    quality = QualityScoreResult(score=80, band="HIGH", components={})
    kill_switch = evaluate_kill_switch(account, risk_policy, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=[target],
        risk_policy=risk_policy, account=account, kill_switch=kill_switch, quality=quality, entry_price=entry,
        existing_gross_exposure_value=account.gross_exposure_notional,
        existing_net_exposure_value=account.net_exposure_notional,
    )
    return result, kill_switch


# ============================================================================
# 9. BOUNDARY TESTS A-L
# ============================================================================

# ----- A/B/C: net exposure below / exactly at / just above limit -----
# max_net_exposure_pct=50% of NLV=100000 -> $50000 ceiling.
_POLICY_ABC = replace(_LOOSE, max_net_exposure_pct=50.0)


def test_a_net_exposure_below_limit_is_allowed():
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV,
        direction="LONG", existing_net_exposure_value=30_000.0,
    )
    _report("A net exposure below limit", existing_net=30_000, remaining_budget=r.evidence["candidates_shares"]["net_exposure"], shares=r.shares)
    assert r.shares > 0
    assert r.evidence["candidates_shares"]["net_exposure"] == 200.0  # (50000-30000)/100


def test_b_net_exposure_exactly_at_limit_is_allowed():
    # existing_net=49900 -> exactly 1 share fits -> post-trade net=50000 exactly.
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV,
        direction="LONG", existing_net_exposure_value=49_900.0,
    )
    _report("B net exposure exactly at limit", shares=r.shares, position_value=r.position_value, post_trade_net=49_900.0 + r.position_value)
    assert r.shares == 1
    assert 49_900.0 + r.position_value == 50_000.0  # lands exactly on the ceiling
    assert r.binding_constraint == "net_exposure"


def test_c_net_exposure_just_above_limit_is_blocked():
    # existing_net=50000 -- already exactly at the ceiling -- even 1 more share exceeds it.
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV,
        direction="LONG", existing_net_exposure_value=50_000.0,
    )
    _report("C net exposure just above limit", existing_net=50_000, shares=r.shares, binding=r.binding_constraint)
    assert r.shares == 0
    assert r.binding_constraint == "net_exposure"


# ----- D: LONG trade increases positive net exposure -----


def test_d_long_trade_increases_positive_net_exposure_shrinking_remaining_budget():
    r_low = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV, direction="LONG", existing_net_exposure_value=10_000.0)
    r_high = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV, direction="LONG", existing_net_exposure_value=40_000.0)
    _report("D LONG increases positive net", low_existing_net_budget=r_low.evidence["candidates_shares"]["net_exposure"], high_existing_net_budget=r_high.evidence["candidates_shares"]["net_exposure"])
    assert r_low.evidence["candidates_shares"]["net_exposure"] == 400.0
    assert r_high.evidence["candidates_shares"]["net_exposure"] == 100.0
    assert r_high.evidence["candidates_shares"]["net_exposure"] < r_low.evidence["candidates_shares"]["net_exposure"]


# ----- E: SHORT trade reduces positive net exposure -----


def test_e_short_trade_reduces_positive_net_exposure_generous_headroom():
    # existing_net=40000 (net long) -- shorting moves toward flat, so net
    # exposure is barely constraining (huge remaining budget), unlike gross
    # exposure which a SHORT still adds to.
    r = compute_position_size(entry=100.0, stop=101.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV, direction="SHORT", existing_net_exposure_value=40_000.0)
    _report("E SHORT reduces positive net", net_exposure_budget=r.evidence["candidates_shares"]["net_exposure"])
    assert r.evidence["candidates_shares"]["net_exposure"] == 900.0  # (40000+50000)/100 -- far looser than the other constraints


# ----- F: SHORT trade increases negative net exposure -----


def test_f_short_trade_increases_negative_net_exposure_shrinking_remaining_budget():
    r = compute_position_size(entry=100.0, stop=101.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV, direction="SHORT", existing_net_exposure_value=-40_000.0)
    _report("F SHORT increases negative net", net_exposure_budget=r.evidence["candidates_shares"]["net_exposure"], shares=r.shares)
    assert r.evidence["candidates_shares"]["net_exposure"] == 100.0  # (-40000+50000)/100
    assert r.binding_constraint == "net_exposure"
    assert r.shares == 100


def test_f_short_at_negative_limit_is_blocked():
    r = compute_position_size(entry=100.0, stop=101.0, net_liquidation_value=NLV, risk_policy=_POLICY_ABC, buying_power=NLV, direction="SHORT", existing_net_exposure_value=-50_000.0)
    _report("F SHORT already at negative limit", shares=r.shares, binding=r.binding_constraint)
    assert r.shares == 0
    assert r.binding_constraint == "net_exposure"


# ----- G/H/I: leverage below / exactly at / just above limit -----
# max_leverage=0.5 -> $50000 ceiling.
_POLICY_GHI = replace(_LOOSE, max_leverage=0.5)


def test_g_leverage_below_limit_is_allowed():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_GHI, buying_power=NLV, existing_gross_exposure_value=30_000.0)
    _report("G leverage below limit", shares=r.shares, binding=r.binding_constraint)
    assert r.shares > 0
    assert r.evidence["candidates_shares"]["leverage"] == 200.0


def test_h_leverage_exactly_at_limit_is_allowed():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_GHI, buying_power=NLV, existing_gross_exposure_value=49_900.0)
    _report("H leverage exactly at limit", shares=r.shares, position_value=r.position_value, binding=r.binding_constraint)
    assert r.shares == 1
    assert 49_900.0 + r.position_value == 50_000.0
    assert r.binding_constraint == "leverage"


def test_i_leverage_just_above_limit_is_blocked():
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_GHI, buying_power=NLV, existing_gross_exposure_value=50_000.0)
    _report("I leverage just above limit", shares=r.shares, binding=r.binding_constraint)
    assert r.shares == 0
    assert r.binding_constraint == "leverage"


# ----- J: gross exposure vs leverage binding independently -----


def test_j_gross_exposure_binds_when_tighter_than_leverage():
    policy = replace(_LOOSE, max_gross_exposure_pct=50.0, max_leverage=2.0)  # $50k vs $200k
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=policy, buying_power=NLV)
    _report("J gross tighter than leverage", binding=r.binding_constraint, shares=r.shares)
    assert r.binding_constraint == "gross_exposure"


def test_j_leverage_binds_when_tighter_than_gross_exposure():
    policy = replace(_LOOSE, max_gross_exposure_pct=300.0, max_leverage=0.5)  # $300k vs $50k
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=policy, buying_power=NLV)
    _report("J leverage tighter than gross", binding=r.binding_constraint, shares=r.shares)
    assert r.binding_constraint == "leverage"


# ----- K: tie between two constraints -----


def test_k_tie_between_gross_exposure_and_leverage_resolves_to_gross_exposure():
    # max_gross_exposure_pct=100% ($100k) and max_leverage=1.0 ($100k) -- an
    # exact numeric tie on the same underlying quantity. buying_power is
    # loosened far past this so it can't also tie.
    policy = replace(_LOOSE, max_gross_exposure_pct=100.0, max_leverage=1.0)
    r = compute_position_size(entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=policy, buying_power=10_000_000.0)
    _report("K tie gross_exposure vs leverage", binding=r.binding_constraint, gross=r.evidence["candidates_shares"]["gross_exposure"], leverage=r.evidence["candidates_shares"]["leverage"])
    assert r.evidence["candidates_shares"]["gross_exposure"] == r.evidence["candidates_shares"]["leverage"]
    assert r.binding_constraint == "gross_exposure"  # declared before leverage in the precedence order


# ----- L: 1-share minimum exceeds limit => zero shares + REJECT (full pipeline) -----


def test_l_one_share_minimum_exceeds_net_exposure_limit_rejects_full_pipeline():
    account = _account(long_exposure_notional=50_000.0, short_exposure_notional=0.0)  # net=50000, exactly the limit
    result, kill_switch = _decide(account, risk_policy=_POLICY_ABC)
    _report("L full pipeline reject", decision=result.decision, no_trade_reasons=result.no_trade_reasons, shares=result.position_size.shares if result.position_size else None)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NET_EXPOSURE_EXCEEDED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED
    assert result.position_size.shares == 0


def test_l_one_share_minimum_exceeds_leverage_limit_rejects_full_pipeline():
    policy = replace(_LOOSE, max_leverage=0.5)
    account = _account(long_exposure_notional=50_000.0, short_exposure_notional=0.0)  # gross=50000, exactly the $50k leverage ceiling
    result, kill_switch = _decide(account, risk_policy=policy)
    _report("L full pipeline reject (leverage)", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.LEVERAGE_LIMIT_EXCEEDED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


# ============================================================================
# 10. EXAMPLE FIXTURE
# ============================================================================
# NLV=100000, current long=60000, current short=10000
# -> gross=70000 (70%), net=50000 (50%), leverage=0.70x.
# max_gross_exposure_pct=100%, max_net_exposure_pct=60%, max_leverage=1.0.


def test_10_example_fixture_derived_figures():
    account = _account(long_exposure_notional=60_000.0, short_exposure_notional=10_000.0)
    _report(
        "10 example fixture", gross_notional=account.gross_exposure_notional, net_notional=account.net_exposure_notional,
        gross_pct=account.gross_exposure_pct, net_pct=account.net_exposure_pct, leverage=account.current_leverage,
    )
    assert account.gross_exposure_notional == 70_000.0
    assert account.net_exposure_notional == 50_000.0
    assert account.gross_exposure_pct == 70.0
    assert account.net_exposure_pct == 50.0
    assert account.current_leverage == 0.70


_POLICY_10 = replace(
    DEFAULT_RISK_POLICY, risk_per_trade_pct=100.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0,
    max_portfolio_heat_pct=100.0, max_net_exposure_pct=60.0, max_leverage=1.0,
)


def test_10_proposed_long_binds_on_net_exposure():
    account = _account(long_exposure_notional=60_000.0, short_exposure_notional=10_000.0)
    r = compute_position_size(
        entry=100.0, stop=99.0, net_liquidation_value=NLV, risk_policy=_POLICY_10, buying_power=NLV,
        direction="LONG", existing_gross_exposure_value=account.gross_exposure_notional,
        existing_net_exposure_value=account.net_exposure_notional,
    )
    _report("10 LONG", shares=r.shares, binding=r.binding_constraint, candidates=r.evidence["candidates_shares"])
    # remaining net budget = (60% of 100000) - 50000 = 10000 -> 100 shares at entry=100 -- tighter than gross (30000/100=300) and leverage (300).
    assert r.shares == 100
    assert r.binding_constraint == "net_exposure"


def test_10_proposed_short_binds_on_gross_exposure_not_net():
    account = _account(long_exposure_notional=60_000.0, short_exposure_notional=10_000.0)
    r = compute_position_size(
        entry=100.0, stop=101.0, net_liquidation_value=NLV, risk_policy=_POLICY_10, buying_power=NLV,
        direction="SHORT", existing_gross_exposure_value=account.gross_exposure_notional,
        existing_net_exposure_value=account.net_exposure_notional,
    )
    _report("10 SHORT", shares=r.shares, binding=r.binding_constraint, candidates=r.evidence["candidates_shares"])
    # Shorting into an already-net-long book has huge net-exposure headroom
    # (net_exposure budget = (50000+60000)/100 = 1100 shares) -- gross exposure
    # (remaining 30000/100=300) binds instead, since a SHORT still adds to gross.
    assert r.shares == 300
    assert r.binding_constraint == "gross_exposure"


# ============================================================================
# 11. QUALIFIED PATH REGRESSION
# ============================================================================
# The exact positive-QUALIFIED fixture from the prior acceptance round, now
# with the new exposure fields present (flat, well within every limit) --
# confirming the newly-enforced net_exposure/leverage constraints do not
# incorrectly block a valid trade when comfortably within limits.


def test_11_qualified_path_unaffected_by_new_constraints_when_within_limits():
    account = _account(long_exposure_notional=0.0, short_exposure_notional=0.0)
    result, kill_switch = _decide(account, risk_policy=DEFAULT_RISK_POLICY, entry=100.0, stop=95.0, direction="LONG", rr1=3.0)
    _report("11 QUALIFIED regression", decision=result.decision, binding=result.position_size.binding_constraint if result.position_size else None)
    assert result.decision == DECISION_QUALIFIED
    assert result.no_trade_reasons == []
    assert result.position_size is not None
    assert result.position_size.shares > 0
    assert "net_exposure" in result.position_size.evidence["candidates_shares"]
    assert "leverage" in result.position_size.evidence["candidates_shares"]


def test_11_qualified_path_with_moderate_pre_existing_exposure_still_qualifies():
    # Some pre-existing exposure, but nowhere near any configured limit.
    account = _account(long_exposure_notional=20_000.0, short_exposure_notional=5_000.0)
    result, kill_switch = _decide(account, risk_policy=DEFAULT_RISK_POLICY, entry=100.0, stop=95.0, direction="LONG", rr1=3.0)
    _report("11 QUALIFIED with moderate exposure", decision=result.decision, gross_pct=account.gross_exposure_pct, net_pct=account.net_exposure_pct, leverage=account.current_leverage)
    assert result.decision == DECISION_QUALIFIED
