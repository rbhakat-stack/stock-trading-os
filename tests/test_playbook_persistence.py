"""§24/§46 of the Phase 4 report — Decision History gains playbook_id/
playbook_version/playbook_family, backward compatible: every pre-Phase-4
call (no playbook_* kwargs) keeps producing NULL for these three fields,
exactly matching an existing Phase 3 persisted row.
"""
from datetime import datetime, timezone

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade.candidate import CandidateStatus, TradeCandidate
from engine.trade.decision import make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.planner import TradePlanningSnapshot
from engine.trade.targets import TargetResult
from repository.risk_repository import build_trade_decision_row

_AS_OF = datetime(2024, 1, 15, tzinfo=timezone.utc)


def _account(**kw):
    base = dict(source="MANUAL", net_liquidation_value=100_000.0, cash=50_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=_AS_OF)
    base.update(kw)
    return AccountRiskState(**base)


def _plan():
    account = _account()
    candidate = TradeCandidate(setup_type="TREND_PULLBACK_LONG", direction="LONG", status=CandidateStatus.TRIGGERED.value,
        structural_level=98.0, entry_zone_low=98.0, entry_zone_high=99.0, entry_method="LIMIT",
        reasons_for=["x"], reasons_against=[], conditions_to_wait_for=[])
    invalidation = InvalidationResult(stop_price=97.9, structural_level=98.0, atr_buffer=0.1, reason="x")
    targets = [TargetResult(price=108.0, reason="x", distance=9.0, reward_per_unit=9.0, r_multiple=9.0)]
    kill_switch = evaluate_kill_switch(account, DEFAULT_RISK_POLICY, data_quality_ok=True)
    decision = make_trade_decision(data_quality_ok=True, candidate=candidate, invalidation=invalidation, targets=targets,
        risk_policy=DEFAULT_RISK_POLICY, account=account, kill_switch=kill_switch, quality=None, entry_price=99.0)
    return TradePlanningSnapshot(symbol="SPY", timeframe="1hour", as_of=_AS_OF, data_source="synthetic",
        candidate=candidate, invalidation=invalidation, targets=targets, quality=None, risk_policy=DEFAULT_RISK_POLICY,
        account=account, kill_switch=kill_switch, decision=decision, entry_price=99.0, current_price=99.0)


def test_pre_phase4_call_produces_null_playbook_fields():
    row = build_trade_decision_row("user-1", "SPY", "1hour", _plan())  # no playbook_* kwargs at all
    assert row["playbook_id"] is None
    assert row["playbook_version"] is None
    assert row["playbook_family"] is None
    # every existing Phase 3 field must still be present and correct
    assert row["setup_type"] == "TREND_PULLBACK_LONG"
    assert row["decision"] == "QUALIFIED"


def test_phase4_call_populates_playbook_fields():
    row = build_trade_decision_row(
        "user-1", "SPY", "1hour", _plan(),
        playbook_id="TREND_PULLBACK_LONG", playbook_version="1.0", playbook_family="TREND_CONTINUATION",
    )
    assert row["playbook_id"] == "TREND_PULLBACK_LONG"
    assert row["playbook_version"] == "1.0"
    assert row["playbook_family"] == "TREND_CONTINUATION"


def test_playbook_fields_are_independent_optional_kwargs_not_all_or_nothing():
    row = build_trade_decision_row("user-1", "SPY", "1hour", _plan(), playbook_id="TREND_PULLBACK_LONG")
    assert row["playbook_id"] == "TREND_PULLBACK_LONG"
    assert row["playbook_version"] is None
    assert row["playbook_family"] is None


def test_registry_definitions_match_what_would_be_persisted():
    from engine.playbooks.registry import get_definition
    d = get_definition("TREND_PULLBACK_LONG")
    row = build_trade_decision_row(
        "user-1", "SPY", "1hour", _plan(),
        playbook_id=d.playbook_id, playbook_version=d.version, playbook_family=d.family,
    )
    assert row["playbook_id"] == "TREND_PULLBACK_LONG"
    assert row["playbook_version"] == "1.0"
    assert row["playbook_family"] == "TREND_CONTINUATION"
