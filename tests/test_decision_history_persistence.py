"""Phase 3 Decision History persistence audit (final, pre-freeze).

Root cause: repository/risk_repository.py::insert_trade_decision read
`decision.quality` (only ever set on TradeDecisionResult's final
QUALIFIED/CONDITIONAL return in engine/trade/decision.py) instead of
`plan.quality` (the TradePlanningSnapshot-level field, set unconditionally
for any TRIGGERED candidate in engine/trade/planner.py::build_trade_plan,
independent of what the decision engine does with it afterward). A TRIGGERED
candidate rejected by an account/policy gate — e.g. MAX_POSITIONS_REACHED —
returns early from make_trade_decision without ever setting decision.quality,
so a genuinely-already-computed quality_score/quality_band was silently
dropped at persistence time even though it was known at decision time.

This is a persistence-mapping bug only — no trading/risk/sizing/policy
enforcement logic was touched. See build_trade_decision_row's docstring for
the general rule this enforces: persist anything genuinely known at decision
time, independent of final outcome; never fabricate what wasn't computed.
"""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.sizing import PositionSizeResult
from engine.trade.candidate import CandidateStatus, TradeCandidate
from engine.trade.decision import (
    DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT, TradeDecisionResult, make_trade_decision,
)
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.planner import TradePlanningSnapshot
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult
from repository.risk_repository import build_trade_decision_row

_AS_OF = datetime(2024, 1, 15, tzinfo=timezone.utc)


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=_AS_OF,
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _candidate(**overrides):
    defaults = dict(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status=CandidateStatus.TRIGGERED.value,
        structural_level=98.0, entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED"], reasons_against=[], conditions_to_wait_for=[],
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def _invalidation(stop=97.9):
    return InvalidationResult(stop_price=stop, structural_level=98.0, atr_buffer=0.1, reason="below support")


def _targets(rr=9.081, price=108.17):
    return [TargetResult(price=price, reason="resistance", distance=price - 99.0, reward_per_unit=price - 99.0, r_multiple=rr)]


def _quality(score=74, band="HIGH"):
    return QualityScoreResult(score=score, band=band, components={})


def _plan(*, candidate, invalidation, targets, quality, account, policy, decision, entry_price=99.0) -> TradePlanningSnapshot:
    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True)
    return TradePlanningSnapshot(
        symbol="SPY", timeframe="1hour", as_of=_AS_OF, data_source="synthetic",
        candidate=candidate, invalidation=invalidation, targets=targets, quality=quality,
        risk_policy=policy, account=account, kill_switch=kill_switch, decision=decision,
        entry_price=entry_price, current_price=entry_price,
    )


# ===================== §7 exact deterministic fixture =====================


def test_triggered_candidate_rejected_on_max_positions_preserves_already_known_fields():
    # Exact reported scenario: TRIGGERED, quality 74/HIGH, RR1≈9.081,
    # otherwise-valid setup, open_positions=6 > max_open_positions=5.
    policy = replace(DEFAULT_RISK_POLICY, max_open_positions=5)
    account = _account(open_positions=6)
    candidate = _candidate()
    invalidation = _invalidation()
    targets = _targets()
    quality = _quality()
    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True)

    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=policy, account=account, kill_switch=kill_switch, quality=quality, entry_price=99.0,
    )

    assert decision.decision == DECISION_REJECT
    assert decision.no_trade_reasons == [NoTradeReason.MAX_POSITIONS_REACHED.value]
    # decision.quality is genuinely None here — the MAX_POSITIONS_REACHED
    # early-return in make_trade_decision never echoes it back onto the
    # result object, even though quality WAS already computed upstream.
    # This is the exact mechanism behind the reported symptom; asserted here
    # so build_trade_decision_row is never "fixed" by reading this field.
    assert decision.quality is None
    assert decision.position_size is None  # sizing never ran — gate hit before step 10/11

    plan = _plan(
        candidate=candidate, invalidation=invalidation, targets=targets, quality=quality,
        account=account, policy=policy, decision=decision,
    )
    assert plan.quality is quality  # planner-level field: always set for a TRIGGERED candidate

    row = build_trade_decision_row("user-1", "SPY", "1hour", plan)

    assert row["decision"] == DECISION_REJECT
    assert row["no_trade_reasons"] == [NoTradeReason.MAX_POSITIONS_REACHED.value]
    assert row["quality_score"] == 74
    assert row["quality_band"] == "HIGH"
    assert row["rr1"] == pytest.approx(9.081)
    assert row["entry_price"] == 99.0
    assert row["stop"] == 97.9
    assert row["target_1"] == pytest.approx(108.17)
    assert row["position_size"] is None  # §5: None = sizing not performed


# ===================== §3 WAIT/FORMING semantics =====================


def test_forming_candidate_persists_quality_as_none_not_fabricated():
    # quality is genuinely never computed for a FORMING candidate (see
    # build_trade_plan — quality is only computed inside the
    # `candidate.status == "TRIGGERED"` branch) — None is the correct,
    # honest value here, not a bug to "fix" by inventing a band.
    account = _account()
    candidate = _candidate(status=CandidateStatus.FORMING.value, conditions_to_wait_for=["price pulls back into zone"])
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=None, targets=[],
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=None, entry_price=None,
    )
    assert decision.decision == DECISION_WAIT

    plan = _plan(
        candidate=candidate, invalidation=None, targets=[], quality=None,
        account=account, policy=DEFAULT_RISK_POLICY, decision=decision, entry_price=None,
    )
    row = build_trade_decision_row("user-1", "SPY", "1hour", plan)
    assert row["decision"] == DECISION_WAIT
    assert row["quality_score"] is None
    assert row["quality_band"] is None


# ===================== §5 position_size None-vs-zero semantics =====================


def test_reject_before_sizing_persists_position_size_as_none():
    # RR below minimum -> rejected before position sizing is ever reached.
    policy = DEFAULT_RISK_POLICY
    account = _account()
    candidate = _candidate()
    invalidation = _invalidation()
    targets = _targets(rr=0.5)  # below policy default min_rr
    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True)
    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=policy, account=account, kill_switch=kill_switch, quality=None, entry_price=99.0,
    )
    assert decision.decision == DECISION_REJECT
    assert decision.position_size is None

    plan = _plan(candidate=candidate, invalidation=invalidation, targets=targets, quality=None, account=account, policy=policy, decision=decision)
    row = build_trade_decision_row("user-1", "SPY", "1hour", plan)
    assert row["position_size"] is None


def test_reject_after_sizing_floored_to_zero_persists_position_size_as_zero_not_none():
    # sizing DID run and produced a real (zero-share) result — this must be
    # distinguishable from "sizing never ran" at persistence time.
    zero_size = PositionSizeResult(
        shares=0, position_value=0.0, capital_at_risk=0.0, risk_per_share=1.0, binding_constraint="risk_per_trade",
    )
    decision = TradeDecisionResult(
        decision=DECISION_REJECT, no_trade_reasons=[NoTradeReason.RISK_BUDGET_EXCEEDED.value],
        position_size=zero_size,
    )
    account = _account()
    candidate = _candidate()
    invalidation = _invalidation()
    targets = _targets()
    plan = _plan(candidate=candidate, invalidation=invalidation, targets=targets, quality=_quality(), account=account, policy=DEFAULT_RISK_POLICY, decision=decision)

    row = build_trade_decision_row("user-1", "SPY", "1hour", plan)
    assert row["position_size"] == 0
    assert row["position_size"] is not None


# ===================== §6 QUALIFIED/CONDITIONAL still correct =====================


def test_qualified_decision_persists_quality_from_both_sources_consistently():
    policy = DEFAULT_RISK_POLICY
    account = _account(open_positions=0)
    candidate = _candidate()
    invalidation = _invalidation()
    targets = _targets()
    quality = _quality()
    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True)
    decision = make_trade_decision(
        data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=policy, account=account, kill_switch=kill_switch, quality=quality, entry_price=99.0,
    )
    assert decision.decision == DECISION_QUALIFIED
    assert decision.quality == quality  # DOES survive on this path, unlike the REJECT case above

    plan = _plan(candidate=candidate, invalidation=invalidation, targets=targets, quality=quality, account=account, policy=policy, decision=decision)
    row = build_trade_decision_row("user-1", "SPY", "1hour", plan)
    assert row["quality_score"] == 74
    assert row["quality_band"] == "HIGH"
    assert row["position_size"] is not None
    assert row["position_size"] == decision.position_size.shares
