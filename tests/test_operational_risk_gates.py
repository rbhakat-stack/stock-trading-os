"""Deterministic Phase 3 acceptance tests for the operational risk gates:
max open positions, max trades per day, and the consecutive-losses
cooldown/lockout distinction.

Every fixture here is an otherwise-fully-clean TRIGGERED candidate that WOULD
become QUALIFIED if no operational gate blocked it: valid entry, structural
stop, a target well above min_rr, HIGH quality, flat daily/weekly equity
(NORMAL drawdown), zero open risk (adequate heat), full buying power, and
data_quality_ok=True. Only the field under test (open_positions,
trades_today, or consecutive_losses) varies, isolating each gate.

INVESTIGATION FINDING (documented here, not invented): before writing these
tests, `cooldown_after_losses` and `max_consecutive_losses` were inspected
directly (engine/risk/kill_switch.py). Both fields ARE used, with a genuine,
intentional distinction:
  - consecutive_losses >= max_consecutive_losses -> trigger
    "MAX_CONSECUTIVE_LOSSES" -> kill-switch state NO_NEW_TRADES (hard lockout,
    blocks_new_trades=True).
  - consecutive_losses >= cooldown_after_losses (and < max_consecutive_losses)
    -> trigger "COOLDOWN_AFTER_LOSSES" -> kill-switch state WARNING (soft,
    informational only, does NOT block new trades).
  - Below both thresholds -> NORMAL, no trigger.
This is an if/elif in evaluate_kill_switch, so the two triggers are mutually
exclusive; the hard-lockout check is evaluated first, so reaching
max_consecutive_losses always wins over the (lower) cooldown threshold.

BUG FOUND AND FIXED (see the same commit as these tests): the GATE itself
(blocking new trades once consecutive_losses hits max_consecutive_losses)
already worked correctly — but engine/trade/decision.py mapped the
"MAX_CONSECUTIVE_LOSSES" kill-switch trigger to
NoTradeReason.DAILY_LOSS_LIMIT_REACHED (a copy-paste leftover — there was no
dedicated NoTradeReason for this case), so a decision blocked purely by
consecutive losses (with a perfectly flat account and zero daily/weekly
drawdown) would misleadingly report "DAILY_LOSS_LIMIT_REACHED" in
no_trade_reasons. Fixed by adding NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED
and mapping the trigger to it. The blocking behavior itself did not change.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from engine.risk.account_state import AccountRiskState
from engine.risk.kill_switch import KillSwitchState, evaluate_kill_switch
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.trade.candidate import TradeCandidate
from engine.trade.decision import DECISION_QUALIFIED, DECISION_REJECT, make_trade_decision
from engine.trade.invalidation import InvalidationResult
from engine.trade.no_trade import NoTradeReason
from engine.trade.quality import QualityScoreResult
from engine.trade.targets import TargetResult

NLV = 100_000.0
POLICY = DEFAULT_RISK_POLICY  # max_open_positions=5, max_trades_per_day=5, cooldown_after_losses=2, max_consecutive_losses=3
ENTRY, STOP = 100.0, 95.0  # risk/share=$5 -> well within heat/exposure budgets
RISK_PER_SHARE = ENTRY - STOP


def _account(**overrides):
    defaults = dict(
        source="MANUAL", net_liquidation_value=NLV, cash=NLV, buying_power=NLV,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=NLV, weekly_start_equity=NLV,
        open_risk=0.0,  # adequate heat: 0% used
        open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime(2024, 1, 15),
    )
    defaults.update(overrides)
    return AccountRiskState(**defaults)


def _clean_candidate() -> TradeCandidate:
    return TradeCandidate(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status="TRIGGERED", structural_level=STOP,
        entry_zone_low=ENTRY, entry_zone_high=ENTRY, entry_method="LIMIT",
        reasons_for=["market state is UPTREND_CONFIRMED", "price has pulled back into the support zone"],
        reasons_against=[], conditions_to_wait_for=[],
    )


def _decide(account: AccountRiskState):
    invalidation = InvalidationResult(stop_price=STOP, structural_level=STOP, atr_buffer=0.0, reason="below structural support")
    reward = RISK_PER_SHARE * 3  # rr=3.0, comfortably above policy min_rr=1.5
    target = TargetResult(
        price=ENTRY + reward, reason="nearest resistance zone edge", distance=reward,
        reward_per_unit=reward, r_multiple=reward / RISK_PER_SHARE,
    )
    quality = QualityScoreResult(score=80, band="HIGH", components={})
    kill_switch = evaluate_kill_switch(account, POLICY, data_quality_ok=True)
    result = make_trade_decision(
        data_quality_ok=True, candidate=_clean_candidate(), invalidation=invalidation, targets=[target],
        risk_policy=POLICY, account=account, kill_switch=kill_switch, quality=quality, entry_price=ENTRY,
    )
    return result, kill_switch


def _report(label: str, account: AccountRiskState, result, kill_switch) -> str:
    line = (
        f"[{label}] candidate=TRIGGERED "
        f"open_positions={account.open_positions}/{POLICY.max_open_positions} "
        f"trades_today={account.trades_today}/{POLICY.max_trades_per_day} "
        f"consecutive_losses={account.consecutive_losses} "
        f"(cooldown_after={POLICY.cooldown_after_losses}, max={POLICY.max_consecutive_losses}) "
        f"kill_switch={kill_switch.state} triggers={kill_switch.triggers} "
        f"no_trade_reasons={result.no_trade_reasons} decision={result.decision}"
    )
    print(line)
    return line


# Sanity check: with all fields at their clean defaults, the fixture really
# does reach QUALIFIED — proving every later REJECT is caused by the one
# field under test, not some other unrelated gate.
def test_0_baseline_fixture_qualifies_when_nothing_is_blocking():
    result, kill_switch = _decide(_account())
    _report("baseline", _account(), result, kill_switch)
    assert result.decision == DECISION_QUALIFIED
    assert result.no_trade_reasons == []


# ===================== 1. MAX OPEN POSITIONS =====================


def test_1a_open_positions_at_max_rejects():
    account = _account(open_positions=5)
    result, kill_switch = _decide(account)
    _report("1A open_positions=5", account, result, kill_switch)

    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_1b_open_positions_below_max_proceeds():
    account = _account(open_positions=4)
    result, kill_switch = _decide(account)
    _report("1B open_positions=4", account, result, kill_switch)

    assert NoTradeReason.MAX_POSITIONS_REACHED.value not in result.no_trade_reasons
    assert result.decision == DECISION_QUALIFIED


# ===================== 2. MAX TRADES PER DAY =====================


def test_2a_trades_today_at_max_rejects():
    account = _account(trades_today=5)
    result, kill_switch = _decide(account)
    _report("2A trades_today=5", account, result, kill_switch)

    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_TRADES_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_2b_trades_today_below_max_proceeds():
    account = _account(trades_today=4)
    result, kill_switch = _decide(account)
    _report("2B trades_today=4", account, result, kill_switch)

    assert NoTradeReason.MAX_TRADES_REACHED.value not in result.no_trade_reasons
    assert result.decision == DECISION_QUALIFIED


# ===================== 3. CONSECUTIVE LOSSES / COOLDOWN =====================
# cooldown_after_losses=2 (soft, WARNING, non-blocking) vs
# max_consecutive_losses=3 (hard, NO_NEW_TRADES, blocking) — see module docstring.


def test_3_consecutive_losses_0_is_normal():
    account = _account(consecutive_losses=0)
    result, kill_switch = _decide(account)
    _report("3 consecutive_losses=0", account, result, kill_switch)

    assert kill_switch.state == KillSwitchState.NORMAL.value
    assert kill_switch.triggers == []
    assert result.decision == DECISION_QUALIFIED


def test_3_consecutive_losses_1_is_normal():
    account = _account(consecutive_losses=1)
    result, kill_switch = _decide(account)
    _report("3 consecutive_losses=1", account, result, kill_switch)

    assert kill_switch.state == KillSwitchState.NORMAL.value
    assert kill_switch.triggers == []
    assert result.decision == DECISION_QUALIFIED


def test_3_consecutive_losses_2_is_cooldown_warning_not_blocking():
    account = _account(consecutive_losses=2)
    result, kill_switch = _decide(account)
    _report("3 consecutive_losses=2", account, result, kill_switch)

    assert kill_switch.state == KillSwitchState.WARNING.value
    assert "COOLDOWN_AFTER_LOSSES" in kill_switch.triggers
    assert kill_switch.blocks_new_trades is False
    assert result.decision == DECISION_QUALIFIED  # soft warning only, does not block
    assert result.no_trade_reasons == []


def test_3_consecutive_losses_3_is_hard_lockout():
    account = _account(consecutive_losses=3)
    result, kill_switch = _decide(account)
    _report("3 consecutive_losses=3", account, result, kill_switch)

    assert kill_switch.state == KillSwitchState.NO_NEW_TRADES.value
    assert "MAX_CONSECUTIVE_LOSSES" in kill_switch.triggers
    assert kill_switch.blocks_new_trades is True
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED.value in result.no_trade_reasons
    # The bug fixed alongside these tests: must NOT be mislabeled as a daily-loss-limit breach.
    assert NoTradeReason.DAILY_LOSS_LIMIT_REACHED.value not in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


def test_3_consecutive_losses_4_stays_hard_lockout():
    account = _account(consecutive_losses=4)
    result, kill_switch = _decide(account)
    _report("3 consecutive_losses=4", account, result, kill_switch)

    assert kill_switch.state == KillSwitchState.NO_NEW_TRADES.value
    assert "MAX_CONSECUTIVE_LOSSES" in kill_switch.triggers
    assert result.decision == DECISION_REJECT
    assert NoTradeReason.MAX_CONSECUTIVE_LOSSES_REACHED.value in result.no_trade_reasons
    assert result.decision != DECISION_QUALIFIED


# ===================== 4. MULTIPLE SIMULTANEOUS BLOCKERS =====================


def test_4_max_positions_and_max_trades_both_collected_not_just_first():
    account = _account(open_positions=5, trades_today=5)
    result, kill_switch = _decide(account)
    _report("4 open_positions=5 AND trades_today=5", account, result, kill_switch)

    assert NoTradeReason.MAX_POSITIONS_REACHED.value in result.no_trade_reasons
    assert NoTradeReason.MAX_TRADES_REACHED.value in result.no_trade_reasons
    assert len(result.no_trade_reasons) >= 2
    assert result.decision == DECISION_REJECT
    assert result.decision != DECISION_QUALIFIED


# ===================== 5. Hard invariants =====================


def test_5_invariant_open_positions_at_or_above_max_never_qualifies():
    for open_positions in (5, 6, 10):
        account = _account(open_positions=open_positions)
        result, _ = _decide(account)
        assert result.decision != DECISION_QUALIFIED, f"open_positions={open_positions}"


def test_5_invariant_trades_today_at_or_above_max_never_qualifies():
    for trades_today in (5, 6, 10):
        account = _account(trades_today=trades_today)
        result, _ = _decide(account)
        assert result.decision != DECISION_QUALIFIED, f"trades_today={trades_today}"


def test_5_invariant_consecutive_loss_hard_lockout_never_qualifies():
    for consecutive_losses in (3, 4, 10):
        account = _account(consecutive_losses=consecutive_losses)
        result, kill_switch = _decide(account)
        assert kill_switch.blocks_new_trades is True, f"consecutive_losses={consecutive_losses}"
        assert result.decision != DECISION_QUALIFIED, f"consecutive_losses={consecutive_losses}"
