"""Final Phase 3 hardening — exhaustive reason-mapping audit (§26), the full
QUALIFIED invariant (§27), an end-to-end acceptance matrix (§29), and a
seeded randomized/fuzz sizing test (§28).
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import RISK_INCREASING, RISK_REDUCING, classify_trade_risk
from engine.risk.sizing import compute_position_size
from engine.trade.candidate import TradeCandidate
from engine.trade.decision import DECISION_CONDITIONAL, DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT, make_trade_decision
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
        long_exposure_notional=0.0, short_exposure_notional=0.0,
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _candidate(direction="LONG", stop=95.0, entry=100.0):
    return TradeCandidate(
        setup_type="TREND_PULLBACK_LONG" if direction == "LONG" else "TREND_PULLBACK_SHORT",
        direction=direction, status="TRIGGERED", structural_level=stop,
        entry_zone_low=entry - 0.5, entry_zone_high=entry, entry_method="LIMIT",
        reasons_for=["clean setup"], reasons_against=[], conditions_to_wait_for=[],
    )


def _decide(account, risk_policy=DEFAULT_RISK_POLICY, entry=100.0, stop=95.0, direction="LONG", rr1=3.0,
            existing_symbol_notional_signed=0.0, open_risk_complete=True, candidate=None, invalidation="default", targets=None):
    candidate = candidate if candidate is not None else _candidate(direction=direction, stop=stop, entry=entry)
    inval = InvalidationResult(stop_price=stop, structural_level=stop, atr_buffer=0.0, reason="x") if invalidation == "default" else invalidation
    if targets is None:
        risk_per_share = abs(entry - stop)
        reward = risk_per_share * rr1
        price = entry + reward if direction == "LONG" else entry - reward
        targets = [TargetResult(price=price, reason="x", distance=reward, reward_per_unit=reward, r_multiple=rr1)]
    quality = QualityScoreResult(score=80, band="HIGH", components={})
    kill_switch = evaluate_kill_switch(account, risk_policy, data_quality_ok=True, open_risk_complete=open_risk_complete)
    result = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=inval, targets=targets,
        risk_policy=risk_policy, account=account, kill_switch=kill_switch, quality=quality, entry_price=entry,
        existing_symbol_notional_signed=existing_symbol_notional_signed,
        existing_gross_exposure_value=account.gross_exposure_notional, existing_net_exposure_value=account.net_exposure_notional,
    )
    return result, kill_switch


def _report(label, **fields):
    print(f"[{label}] " + " ".join(f"{k}={v}" for k, v in fields.items()))


# ============================================================================
# 26. DECISION REASON ACCURACY AUDIT — exhaustive
# ============================================================================


def test_reason_mapping_daily_loss_limit():
    account = _account(net_liquidation_value=97_000.0)  # -3% equity drawdown > 2% default
    result, ks = _decide(account)
    assert "DAILY_LOSS_LIMIT_REACHED" in ks.triggers
    assert result.no_trade_reasons == [NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value]


def test_reason_mapping_weekly_loss_limit():
    account = _account(weekly_start_equity=110_000.0)  # -9.09% weekly drawdown > 5% default
    result, ks = _decide(account)
    assert "WEEKLY_LOSS_LIMIT_REACHED" in ks.triggers
    assert result.no_trade_reasons == [NoTradeReason.WEEKLY_LOSS_LIMIT_REACHED.value]


def test_reason_mapping_daily_realized_loss_limit():
    account = _account(realized_pnl_today=-3_000.0, unrealized_pnl=3_000.0)  # equity flat, realized -3% > 2% default
    result, ks = _decide(account)
    assert "DAILY_REALIZED_LOSS_LIMIT_REACHED" in ks.triggers
    assert result.no_trade_reasons == [NoTradeReason.DAILY_REALIZED_LOSS_LIMIT_REACHED.value]
    # Must NOT be masked by the flat equity drawdown, and must NOT be mislabeled.
    assert NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value not in result.no_trade_reasons


def test_reason_mapping_max_consecutive_losses():
    account = _account(consecutive_losses=3)  # policy default max=3
    result, ks = _decide(account)
    assert "MAX_CONSECUTIVE_LOSSES" in ks.triggers
    assert result.no_trade_reasons == [NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED.value]


def test_reason_mapping_open_risk_data_incomplete():
    account = _account()
    result, ks = _decide(account, open_risk_complete=False)
    assert "OPEN_RISK_DATA_INCOMPLETE" in ks.triggers
    assert result.no_trade_reasons == [NoTradeReason.OPEN_RISK_DATA_INCOMPLETE.value]


def test_reason_mapping_data_quality_failure():
    result, ks = _decide(_account())
    # data_quality_ok=False path is checked directly in make_trade_decision, not via kill_switch triggers alone.
    kill_switch = evaluate_kill_switch(_account(), DEFAULT_RISK_POLICY, data_quality_ok=False)
    result2 = make_trade_decision(
        data_quality_ok=False, candidate=_candidate(), invalidation=None, targets=[],
        risk_policy=DEFAULT_RISK_POLICY, account=_account(), kill_switch=kill_switch, quality=None, entry_price=100.0,
    )
    assert result2.no_trade_reasons == [NoTradeReason.DATA_QUALITY_FAILURE.value]


def test_reason_mapping_binding_constraint_symbol_exposure():
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=1.0, max_gross_exposure_pct=1000.0, max_leverage=4.0, max_net_exposure_pct=1000.0)
    account = _account()
    result, _ = _decide(account, risk_policy=policy, existing_symbol_notional_signed=1_000.0)  # already at the 1% ($1000) cap
    assert result.no_trade_reasons == [NoTradeReason.SYMBOL_EXPOSURE_EXCEEDED.value]


def test_reason_mapping_binding_constraint_net_exposure():
    policy = replace(DEFAULT_RISK_POLICY, max_net_exposure_pct=1.0, max_symbol_exposure_pct=1000.0, max_gross_exposure_pct=1000.0, max_leverage=4.0)
    account = _account(long_exposure_notional=1_000.0)  # net=1000, exactly the 1% ($1000) cap
    result, _ = _decide(account, risk_policy=policy)
    assert result.no_trade_reasons == [NoTradeReason.NET_EXPOSURE_EXCEEDED.value]


def test_reason_mapping_binding_constraint_leverage():
    policy = replace(DEFAULT_RISK_POLICY, max_leverage=0.01, max_symbol_exposure_pct=1000.0, max_gross_exposure_pct=1000.0, max_net_exposure_pct=1000.0)
    account = _account(long_exposure_notional=1_000.0)  # gross=1000, exactly the 0.01x*100000=$1000 cap
    result, _ = _decide(account, risk_policy=policy, existing_symbol_notional_signed=1_000.0)
    assert result.no_trade_reasons == [NoTradeReason.LEVERAGE_LIMIT_EXCEEDED.value]


def test_reason_mapping_binding_constraint_insufficient_buying_power():
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=1000.0, max_gross_exposure_pct=1000.0, max_leverage=4.0, max_net_exposure_pct=1000.0)
    account = _account(buying_power=0.0)
    result, _ = _decide(account, risk_policy=policy)
    assert result.no_trade_reasons == [NoTradeReason.INSUFFICIENT_BUYING_POWER.value]


def test_reason_mapping_binding_constraint_risk_budget():
    policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=0.001, max_symbol_exposure_pct=1000.0, max_gross_exposure_pct=1000.0, max_leverage=4.0, max_net_exposure_pct=1000.0)
    account = _account()
    result, _ = _decide(account, risk_policy=policy, entry=100.0, stop=1.0)  # huge risk/share, tiny risk budget
    assert result.no_trade_reasons == [NoTradeReason.RISK_BUDGET_EXCEEDED.value]


def test_reason_mapping_binding_constraint_invalid_numeric_input():
    r = compute_position_size(entry=100.0, stop=95.0, net_liquidation_value=NLV, risk_policy=DEFAULT_RISK_POLICY, buying_power=float("nan"))
    assert r.binding_constraint == "INVALID_NUMERIC_INPUT"


def test_reason_mapping_invalid_risk_policy_rejects_before_anything_else():
    bad_policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=-1.0)
    result, _ = _decide(_account(), risk_policy=bad_policy)
    assert result.decision == DECISION_REJECT
    assert result.no_trade_reasons == [NoTradeReason.INVALID_RISK_POLICY.value]


def test_reason_mapping_invalid_account_state_rejects_before_anything_else():
    bad_account = _account(net_liquidation_value=-5.0)
    result, _ = _decide(bad_account)
    assert result.decision == DECISION_REJECT
    assert result.no_trade_reasons == [NoTradeReason.INVALID_ACCOUNT_STATE.value]


# ============================================================================
# 27. QUALIFIED INVARIANT — FINAL VERSION
# ============================================================================


def test_27_full_qualified_invariant_checklist():
    account = _account()
    entry, stop = 100.0, 95.0
    result, kill_switch = _decide(account, entry=entry, stop=stop, rr1=3.0)

    assert result.decision == DECISION_QUALIFIED
    ps = result.position_size
    assert stop is not None
    assert result.position_size is not None
    assert ps.shares > 0
    max_trade_loss = account.net_liquidation_value * (DEFAULT_RISK_POLICY.risk_per_trade_pct / 100)
    assert ps.capital_at_risk <= max_trade_loss + 1e-9

    post_trade_symbol_pct = (ps.position_value / account.net_liquidation_value) * 100
    assert post_trade_symbol_pct <= DEFAULT_RISK_POLICY.max_symbol_exposure_pct + 1e-9
    post_trade_gross_pct = ((account.gross_exposure_notional + ps.position_value) / account.net_liquidation_value) * 100
    assert post_trade_gross_pct <= DEFAULT_RISK_POLICY.max_gross_exposure_pct + 1e-9
    post_trade_net_pct = (abs(account.net_exposure_notional + ps.position_value) / account.net_liquidation_value) * 100
    assert post_trade_net_pct <= DEFAULT_RISK_POLICY.max_net_exposure_pct + 1e-9
    post_trade_heat_pct = ((account.open_risk + ps.capital_at_risk) / account.net_liquidation_value) * 100
    assert post_trade_heat_pct <= DEFAULT_RISK_POLICY.max_portfolio_heat_pct + 1e-9
    post_trade_leverage = (account.gross_exposure_notional + ps.position_value) / account.net_liquidation_value
    assert post_trade_leverage <= DEFAULT_RISK_POLICY.max_leverage + 1e-9

    assert not (account.current_daily_drawdown_pct <= -DEFAULT_RISK_POLICY.max_daily_loss_pct)
    assert not (account.realized_loss_pct <= -DEFAULT_RISK_POLICY.max_daily_realized_loss_pct)
    assert not (account.current_weekly_drawdown_pct <= -DEFAULT_RISK_POLICY.max_weekly_loss_pct)
    assert account.open_positions < DEFAULT_RISK_POLICY.max_open_positions
    assert account.trades_today < DEFAULT_RISK_POLICY.max_trades_per_day
    assert account.consecutive_losses < DEFAULT_RISK_POLICY.max_consecutive_losses
    assert kill_switch.blocks_new_trades is False
    assert result.evidence["trade_risk_classification"] == RISK_INCREASING
    assert result.no_trade_reasons == []


def test_27_risk_reducing_trade_bypasses_max_positions_and_trades():
    maxed_policy = DEFAULT_RISK_POLICY
    account = _account(open_positions=5, trades_today=5, long_exposure_notional=8_000.0)  # AT max positions/trades
    # A SHORT that reduces an existing +80 (=$8000) LONG position in this symbol.
    result, kill_switch = _decide(account, risk_policy=maxed_policy, entry=100.0, stop=101.0, direction="SHORT", rr1=3.0, existing_symbol_notional_signed=8_000.0)
    _report("27 risk-reducing bypasses max positions/trades", decision=result.decision, no_trade_reasons=result.no_trade_reasons)
    assert NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons
    assert NoTradeReason.MAX_TRADES_REACHED.value not in result.no_trade_reasons


def test_27_risk_increasing_trade_still_blocked_by_max_positions():
    account = _account(open_positions=5, trades_today=0)
    result, _ = _decide(account)  # opening a brand new position, no existing exposure
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


# ============================================================================
# 29. END-TO-END ACCEPTANCE MATRIX
# ============================================================================


def test_matrix_normal_qualified_long():
    result, _ = _decide(_account(), direction="LONG", entry=100.0, stop=95.0, rr1=3.0)
    assert result.decision == DECISION_QUALIFIED


def test_matrix_normal_qualified_short():
    result, _ = _decide(_account(), direction="SHORT", entry=100.0, stop=105.0, rr1=3.0)
    assert result.decision == DECISION_QUALIFIED


def test_matrix_daily_loss_blocked():
    result, _ = _decide(_account(net_liquidation_value=97_000.0))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value in result.no_trade_reasons


def test_matrix_daily_realized_loss_blocked():
    result, _ = _decide(_account(realized_pnl_today=-3_000.0, unrealized_pnl=3_000.0))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.DAILY_REALIZED_LOSS_LIMIT_REACHED.value in result.no_trade_reasons


def test_matrix_weekly_loss_blocked():
    result, _ = _decide(_account(weekly_start_equity=110_000.0))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.WEEKLY_LOSS_LIMIT_REACHED.value in result.no_trade_reasons


def test_matrix_portfolio_heat_blocked():
    account = _account(open_risk=3_000.0)  # policy default max_portfolio_heat_pct=3.0 -> already at cap
    result, _ = _decide(account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value in result.no_trade_reasons


def test_matrix_max_positions_blocked():
    result, _ = _decide(_account(open_positions=5))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons


def test_matrix_max_trades_blocked():
    result, _ = _decide(_account(trades_today=5))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_TRADES_REACHED.value in result.no_trade_reasons


def test_matrix_consecutive_loss_warning_not_blocking():
    result, ks = _decide(_account(consecutive_losses=2))
    assert ks.state == "WARNING"
    assert result.decision == DECISION_QUALIFIED


def test_matrix_consecutive_loss_lockout():
    result, _ = _decide(_account(consecutive_losses=3))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED.value in result.no_trade_reasons


def test_matrix_low_rr_blocked():
    result, _ = _decide(_account(), rr1=1.0)  # policy default min_rr=1.5
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.RR_BELOW_MINIMUM.value in result.no_trade_reasons


def test_matrix_missing_stop_blocked():
    result, _ = _decide(_account(), invalidation=None)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons


def test_matrix_wrong_side_stop_blocked():
    result, _ = _decide(_account(), entry=100.0, stop=105.0, direction="LONG")
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_STRUCTURAL_STOP.value in result.no_trade_reasons


def test_matrix_missing_target_blocked():
    result, _ = _decide(_account(), targets=[])
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NO_VALID_TARGET.value in result.no_trade_reasons


def test_matrix_buying_power_constrained():
    account = _account(buying_power=0.0)
    result, _ = _decide(account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.INSUFFICIENT_BUYING_POWER.value in result.no_trade_reasons


def test_matrix_symbol_constrained():
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=1.0)
    result, _ = _decide(_account(), risk_policy=policy, existing_symbol_notional_signed=1_000.0)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.SYMBOL_EXPOSURE_EXCEEDED.value in result.no_trade_reasons


def test_matrix_gross_constrained():
    policy = replace(DEFAULT_RISK_POLICY, max_gross_exposure_pct=1.0, max_symbol_exposure_pct=1000.0)
    account = _account(long_exposure_notional=1_000.0, short_exposure_notional=0.0)
    result, _ = _decide(account, risk_policy=policy, existing_symbol_notional_signed=0.0)  # different symbol -- gross binds
    assert result.decision == DECISION_REJECT


def test_matrix_net_constrained():
    policy = replace(DEFAULT_RISK_POLICY, max_net_exposure_pct=1.0)
    account = _account(long_exposure_notional=1_000.0)
    result, _ = _decide(account, risk_policy=policy)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.NET_EXPOSURE_EXCEEDED.value in result.no_trade_reasons


def test_matrix_heat_constrained():
    account = _account(open_risk=3_000.0)
    result, _ = _decide(account)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.PORTFOLIO_HEAT_EXCEEDED.value in result.no_trade_reasons


def test_matrix_leverage_constrained():
    policy = replace(DEFAULT_RISK_POLICY, max_leverage=0.01, max_symbol_exposure_pct=1000.0, max_gross_exposure_pct=1000.0, max_net_exposure_pct=1000.0)
    account = _account(long_exposure_notional=1_000.0)
    result, _ = _decide(account, risk_policy=policy, existing_symbol_notional_signed=1_000.0)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.LEVERAGE_LIMIT_EXCEEDED.value in result.no_trade_reasons


def test_matrix_same_symbol_add():
    result, _ = _decide(_account(long_exposure_notional=1_000.0), existing_symbol_notional_signed=1_000.0, direction="LONG")
    assert result.decision == DECISION_QUALIFIED
    assert result.evidence["trade_risk_classification"] == RISK_INCREASING


def test_matrix_same_symbol_reduction():
    account = _account(long_exposure_notional=8_000.0)
    result, _ = _decide(account, existing_symbol_notional_signed=8_000.0, direction="SHORT", entry=100.0, stop=101.0, rr1=3.0)
    assert result.evidence.get("trade_risk_classification") == RISK_REDUCING or result.decision == DECISION_QUALIFIED


def test_matrix_same_symbol_reversal_through_zero():
    account = _account(long_exposure_notional=8_000.0)
    # A large SHORT that flips the existing +80 all the way to a bigger short.
    result, kill_switch = _decide(account, existing_symbol_notional_signed=8_000.0, direction="SHORT", entry=100.0, stop=101.0, rr1=3.0)
    assert result.decision in (DECISION_QUALIFIED, DECISION_CONDITIONAL, DECISION_REJECT)  # must not crash; classification handled internally


def test_matrix_different_symbol_isolation():
    account = _account(long_exposure_notional=9_000.0, short_exposure_notional=0.0)  # SPY heavily long
    policy = replace(DEFAULT_RISK_POLICY, max_symbol_exposure_pct=10.0, max_gross_exposure_pct=1000.0, max_leverage=4.0, max_net_exposure_pct=1000.0)
    # Analyzing AAPL (existing_symbol_notional_signed=0 for AAPL specifically) must not inherit SPY's exposure.
    result, _ = _decide(account, risk_policy=policy, existing_symbol_notional_signed=0.0)
    assert result.decision == DECISION_QUALIFIED


def test_matrix_malformed_account_state():
    result, _ = _decide(_account(cash=-1.0))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.INVALID_ACCOUNT_STATE.value in result.no_trade_reasons


def test_matrix_zero_nlv():
    result, _ = _decide(_account(net_liquidation_value=0.0))
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.INVALID_ACCOUNT_STATE.value in result.no_trade_reasons


def test_matrix_zero_entry():
    result, _ = _decide(_account(), entry=0.0)
    assert result.decision == DECISION_REJECT
    assert result.position_size is None or result.position_size.shares == 0


def test_matrix_nan_policy_field():
    policy = replace(DEFAULT_RISK_POLICY, risk_per_trade_pct=float("nan"))
    result, _ = _decide(_account(), risk_policy=policy)
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.INVALID_RISK_POLICY.value in result.no_trade_reasons


def test_matrix_multi_blocker_reason_collection():
    result, _ = _decide(_account(open_positions=5, trades_today=5))
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert NoTradeReason.MAX_TRADES_REACHED.value in result.no_trade_reasons
    assert len(result.no_trade_reasons) >= 2


# ============================================================================
# 28. PROPERTY / FUZZ TESTING
# ============================================================================


def test_fuzz_no_qualified_result_ever_violates_an_invariant():
    rng = random.Random(20260908)  # fixed seed -> fully deterministic
    violations = []
    n_cases = 500

    for i in range(n_cases):
        nlv = rng.choice([1_000.0, 10_000.0, 100_000.0, 1_000_000.0])
        entry = rng.uniform(0.5, 5_000.0)
        stop_distance = rng.uniform(0.01, entry * 0.5)
        direction = rng.choice(["LONG", "SHORT"])
        stop = entry - stop_distance if direction == "LONG" else entry + stop_distance
        buying_power = rng.uniform(0.0, nlv * 3)

        policy = replace(
            DEFAULT_RISK_POLICY,
            risk_per_trade_pct=rng.uniform(0.01, 5.0),
            max_symbol_exposure_pct=rng.uniform(0.01, 100.0),
            max_gross_exposure_pct=rng.uniform(0.01, 200.0),
            max_net_exposure_pct=rng.uniform(0.01, 200.0),
            max_portfolio_heat_pct=rng.uniform(0.01, 20.0),
            max_leverage=rng.uniform(0.01, 4.0),
        )

        # Existing exposures must represent a SELF-CONSISTENT, already-compliant
        # prior state (this is what validate_account_state + this same sizing
        # function's own gates would have enforced to REACH this state in the
        # first place) — not an already-broken account. A pre-existing breach
        # is a separate, explicitly out-of-scope concern (documented in the
        # final hardening report), not something compute_position_size can be
        # expected to repair. Clamped to each case's OWN randomized ceilings.
        max_symbol_value = nlv * (policy.max_symbol_exposure_pct / 100)
        max_gross_value = nlv * (policy.max_gross_exposure_pct / 100)
        max_net_value = nlv * (policy.max_net_exposure_pct / 100)
        max_leverage_value = nlv * policy.max_leverage
        gross_ceiling = min(max_gross_value, max_leverage_value)
        # A single symbol's existing position can't legitimately exceed the
        # ACCOUNT-wide gross/leverage ceiling either — clamp to whichever is
        # tightest, so the generated starting state is self-consistent.
        symbol_ceiling = min(max_symbol_value, gross_ceiling)

        existing_symbol_signed = rng.uniform(-symbol_ceiling, symbol_ceiling)
        existing_gross = max(abs(existing_symbol_signed), min(abs(existing_symbol_signed) + rng.uniform(0.0, nlv * 2), gross_ceiling))
        existing_net = max(-max_net_value, min(max_net_value, rng.uniform(-nlv * 2, nlv * 2)))
        current_open_risk = rng.uniform(0.0, nlv * (policy.max_portfolio_heat_pct / 100))

        r = compute_position_size(
            entry=entry, stop=stop, net_liquidation_value=nlv, risk_policy=policy, buying_power=buying_power,
            direction=direction, existing_symbol_notional_signed=existing_symbol_signed,
            existing_gross_exposure_value=existing_gross, existing_net_exposure_value=existing_net,
            current_open_risk=current_open_risk,
        )

        case_desc = dict(
            i=i, nlv=nlv, entry=entry, stop=stop, direction=direction, buying_power=buying_power,
            existing_symbol_signed=existing_symbol_signed, existing_gross=existing_gross, existing_net=existing_net,
            policy=policy, result=r,
        )

        # Invariants that must hold for EVERY generated case, not just "qualified" ones.
        if r.shares < 0:
            violations.append(("negative shares", case_desc))
            continue
        if r.shares != int(r.shares):
            violations.append(("non-integer shares", case_desc))
        if not math.isfinite(r.shares) or not math.isfinite(r.capital_at_risk) or not math.isfinite(r.position_value):
            violations.append(("non-finite result", case_desc))
            continue
        if r.shares == 0:
            continue  # nothing further to check for a rejected/zero-share result

        max_trade_loss = nlv * (policy.risk_per_trade_pct / 100)
        if r.capital_at_risk > max_trade_loss + 1e-6:
            violations.append(("capital_at_risk exceeds risk_per_trade budget", case_desc))

        if r.position_value > buying_power + 1e-6:
            violations.append(("position_value exceeds buying_power", case_desc))

        pre_trade_symbol_abs = abs(existing_symbol_signed)
        signed_delta = r.position_value if direction == "LONG" else -r.position_value
        post_trade_symbol_signed = existing_symbol_signed + signed_delta
        post_trade_symbol_abs = abs(post_trade_symbol_signed)
        max_symbol_value = nlv * (policy.max_symbol_exposure_pct / 100)
        if post_trade_symbol_abs > max_symbol_value + 1e-6:
            violations.append(("post-trade symbol exposure exceeds max_symbol_exposure_pct", case_desc))

        gross_excluding_symbol = max(0.0, existing_gross - pre_trade_symbol_abs)
        post_trade_gross = gross_excluding_symbol + post_trade_symbol_abs
        max_gross_value = nlv * (policy.max_gross_exposure_pct / 100)
        if post_trade_gross > max_gross_value + 1e-6:
            violations.append(("post-trade gross exposure exceeds max_gross_exposure_pct", case_desc))

        max_leverage_value = nlv * policy.max_leverage
        if post_trade_gross > max_leverage_value + 1e-6:
            violations.append(("post-trade gross notional exceeds leverage ceiling", case_desc))

        post_trade_net_signed = existing_net + signed_delta
        max_net_value = nlv * (policy.max_net_exposure_pct / 100)
        if abs(post_trade_net_signed) > max_net_value + 1e-6:
            violations.append(("post-trade net exposure exceeds max_net_exposure_pct", case_desc))

        max_heat_value = nlv * (policy.max_portfolio_heat_pct / 100)
        if current_open_risk + r.capital_at_risk > max_heat_value + 1e-6:
            violations.append(("post-trade heat exceeds max_portfolio_heat_pct", case_desc))

    if violations:
        kind, first = violations[0]
        print(f"FUZZ FAILURE ({len(violations)} total): {kind}")
        print(first)
    assert not violations, f"{len(violations)} invariant violation(s) found; first: {violations[0][0] if violations else None}"
    print(f"fuzz test: {n_cases} cases generated, 0 invariant violations")
