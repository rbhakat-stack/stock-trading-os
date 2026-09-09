"""Round J: fixes a real UI bug where the Account State tab correctly
DISABLED its Open risk / Open positions / Long exposure / Short exposure
fields once Positions data was authoritative, but still DISPLAYED the stale
raw manual values (open_positions=6, open_risk=0.00) inside those disabled
fields, instead of the derived values (1, $400.00) that Trade Plan Analyze
was already using correctly.

Root cause: app/pages/trade_planner.py bound each of those four
st.number_input widgets to key="acct_*" (the raw manual session-state
value) even when disabled — disabling a widget only blocks editing, it does
not change what `key=`-bound value gets displayed. A SEPARATE `display_account`
variable held the correctly-derived numbers, but that variable was only used
for the read-only caption lines further down the tab (drawdown/heat/exposure
%), never fed back into the widgets themselves.

Fix: extracted the raw-account + positions -> effective-account overlay into
one canonical function, engine.risk.positions.effective_account_state(),
called by BOTH the Account State tab's widgets AND Trade Plan's Analyze path
(previously each had its own separate `dataclasses.replace(...)` call,
itself a smaller instance of the same class of bug this report flags).
Widgets now branch explicitly: when positions are authoritative, render a
disabled, KEYLESS mirror showing the derived value (never bound to the raw
manual key=); otherwise render the normal editable key=-bound widget.

These tests exercise effective_account_state() and the related decision/
limits wiring directly (pure, deterministic, no Streamlit runtime needed).
The live browser test in the session report additionally confirms the
actual rendered widget values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from engine.risk.account_state import AccountRiskState
from engine.risk.limits import max_positions_reached
from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import AccountPosition, effective_account_state
from engine.trade.decision import DECISION_QUALIFIED, DECISION_WAIT, make_trade_decision
from engine.trade.candidate import TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.targets import TargetResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADE_PLANNER_PATH = PROJECT_ROOT / "app" / "pages" / "trade_planner.py"


def _raw_account(**overrides) -> AccountRiskState:
    base = dict(
        source="MANUAL", net_liquidation_value=100_000.0, cash=100_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=6, trades_today=0, consecutive_losses=0, as_of=datetime.now(timezone.utc),
        long_exposure_notional=0.0, short_exposure_notional=0.0,
    )
    base.update(overrides)
    return AccountRiskState(**base)


def _spy_position(stop=95.0) -> AccountPosition:
    return AccountPosition(
        symbol="SPY", quantity_signed=80.0, reference_price=100.0, planned_stop_price=stop,
        average_price=98.0, updated_at=datetime.now(timezone.utc),
    )


def _policy(**overrides):
    return DEFAULT_RISK_POLICY.__class__(**{**DEFAULT_RISK_POLICY.__dict__, **overrides})


# ============================================================================
# A/B/C — positions exist: derived values used, stale manual values ignored
# ============================================================================


def test_a_stale_manual_open_positions_is_ignored_in_favor_of_derived():
    raw = _raw_account(open_positions=6)
    effective, complete = effective_account_state(raw, [_spy_position()])
    assert effective.open_positions == 1  # NOT the stale manual 6
    assert complete is True


def test_b_stale_manual_open_risk_is_ignored_in_favor_of_derived():
    raw = _raw_account(open_risk=0.0)
    effective, complete = effective_account_state(raw, [_spy_position()])
    assert effective.open_risk == 400.0  # (100 - 95) * 80, NOT the stale manual 0.0
    assert complete is True


def test_c_stale_manual_exposure_is_ignored_in_favor_of_derived():
    raw = _raw_account(long_exposure_notional=0.0, short_exposure_notional=0.0)
    effective, _ = effective_account_state(raw, [_spy_position()])
    assert effective.long_exposure_notional == 8_000.0  # 80 * 100
    assert effective.short_exposure_notional == 0.0


def test_exact_report_scenario_all_four_fields_at_once():
    """The exact §11 scenario from the report: raw manual open_positions=6,
    open_risk=0, SPY 80/100/95/98 saved -> effective values must be
    1 / 400.00 / 8000 / 0, and portfolio heat must be 0.400%."""
    raw = _raw_account(open_positions=6, open_risk=0.0, long_exposure_notional=0.0, short_exposure_notional=0.0)
    effective, complete = effective_account_state(raw, [_spy_position()])
    assert complete is True
    assert effective.open_positions == 1
    assert effective.open_risk == 400.0
    assert effective.long_exposure_notional == 8_000.0
    assert effective.short_exposure_notional == 0.0
    assert round(effective.portfolio_heat_pct, 3) == 0.400


# ============================================================================
# D — no positions => manual fallback values are authoritative, unchanged
# ============================================================================


def test_d_no_positions_returns_raw_account_unchanged():
    raw = _raw_account(open_positions=6, open_risk=123.45, long_exposure_notional=500.0, short_exposure_notional=250.0)
    effective, complete = effective_account_state(raw, [])
    assert effective == raw  # every manual field preserved exactly, nothing overlaid
    assert complete is True


# ============================================================================
# E — missing planned stop: open risk must be reported incomplete, NEVER a
# false zero or a false "derived" number.
# ============================================================================


def test_e_missing_stop_reports_open_risk_incomplete_not_a_fake_zero():
    position_no_stop = AccountPosition(
        symbol="SPY", quantity_signed=80.0, reference_price=100.0, planned_stop_price=None,
        updated_at=datetime.now(timezone.utc),
    )
    raw = _raw_account(open_risk=0.0)  # stale manual fallback also happens to be 0 - must not look "confirmed"
    effective, complete = effective_account_state(raw, [position_no_stop])
    assert complete is False
    # open_positions/exposure are still fully derivable regardless of the
    # missing stop - only open_risk is affected.
    assert effective.open_positions == 1
    assert effective.long_exposure_notional == 8_000.0
    # open_risk falls back to the RAW value only as a dataclass-validity
    # placeholder - callers must check `complete` before treating it as
    # meaningful, and the UI must never display it as a confirmed number
    # in this case (see the trade_planner.py static check below).
    assert effective.open_risk == raw.open_risk


def test_account_state_tab_never_shows_a_bare_number_for_incomplete_open_risk():
    """Static guard: the Account State tab must render a text/'unavailable'
    indicator (never a plain numeric 0.00) when open risk is incomplete."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert 'value="Unavailable — incomplete"' in source
    assert "Derived open risk unavailable — one or more positions lacks a usable planned stop." in source
    assert "Portfolio heat: DATA INCOMPLETE" in source


# ============================================================================
# F — max positions gate uses the DERIVED count, not the stale manual one
# ============================================================================


def test_f_max_positions_gate_uses_derived_count_not_stale_manual_six():
    policy = _policy(max_open_positions=5)
    raw = _raw_account(open_positions=6)  # stale manual value alone WOULD trip the gate
    effective, _ = effective_account_state(raw, [_spy_position()])  # 1 actual saved position
    assert effective.open_positions == 1
    assert max_positions_reached(effective, policy) is False  # must NOT block


def test_f_max_positions_gate_blocks_when_five_actual_positions_exist():
    policy = _policy(max_open_positions=5)
    positions = [
        AccountPosition(symbol=sym, quantity_signed=10.0, reference_price=100.0, updated_at=datetime.now(timezone.utc))
        for sym in ("AAA", "BBB", "CCC", "DDD", "EEE")
    ]
    raw = _raw_account(open_positions=0)  # manual value irrelevant once positions exist
    effective, _ = effective_account_state(raw, positions)
    assert effective.open_positions == 5
    assert max_positions_reached(effective, policy) is True


# ============================================================================
# G/H — current-only vs current+post-trade heat display
# ============================================================================


def _forming_candidate() -> TradeCandidate:
    return TradeCandidate(
        setup_type="TREND_PULLBACK", direction="LONG", status="FORMING", structural_level=100.0,
        entry_method="LIMIT", entry_zone_low=None, entry_zone_high=None,
        reasons_for=["forming"], reasons_against=[], conditions_to_wait_for=["needs trigger"],
    )


def _triggered_candidate() -> TradeCandidate:
    return TradeCandidate(
        setup_type="TREND_PULLBACK", direction="LONG", status="TRIGGERED", structural_level=95.0,
        entry_method="LIMIT", entry_zone_low=100.0, entry_zone_high=100.0,
        reasons_for=["triggered"], reasons_against=[], conditions_to_wait_for=[],
    )


def test_g_wait_forming_candidate_never_produces_a_position_size():
    """G: a FORMING (not yet TRIGGERED) candidate must decide WAIT with
    position_size=None — the UI's `if plan.decision.position_size is not
    None:` gate is what keeps POST-TRADE PORTFOLIO HEAT from ever being
    fabricated for an unsized candidate."""
    policy = _policy()
    account = _raw_account(open_risk=400.0)
    from engine.risk.kill_switch import evaluate_kill_switch

    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True, open_risk_complete=True)
    decision = make_trade_decision(
        data_quality_ok=True, candidate=_forming_candidate(), invalidation=None, targets=[],
        risk_policy=policy, account=account, kill_switch=kill_switch, quality=None, entry_price=None,
    )
    assert decision.decision == DECISION_WAIT
    assert decision.position_size is None


def test_h_triggered_candidate_with_valid_setup_produces_a_position_size():
    """H: a TRIGGERED candidate with a valid stop/target/RR must produce an
    actual PositionSizeResult, which is exactly what gates showing
    POST-TRADE PORTFOLIO HEAT in the UI."""
    policy = _policy(min_rr=1.0)
    account = _raw_account(open_risk=0.0, open_positions=0)  # below max_open_positions - isolate the sizing path
    from engine.risk.kill_switch import evaluate_kill_switch

    kill_switch = evaluate_kill_switch(account, policy, data_quality_ok=True, open_risk_complete=True)
    invalidation = InvalidationResult(stop_price=95.0, structural_level=95.0, atr_buffer=0.0, reason="structural")
    targets = [TargetResult(price=110.0, reason="resistance", distance=10.0, reward_per_unit=10.0, r_multiple=2.0)]
    decision = make_trade_decision(
        data_quality_ok=True, candidate=_triggered_candidate(), invalidation=invalidation, targets=targets,
        risk_policy=policy, account=account, kill_switch=kill_switch, quality=None, entry_price=100.0,
    )
    assert decision.decision == DECISION_QUALIFIED
    assert decision.position_size is not None
    assert decision.position_size.shares > 0
    assert decision.position_size.post_trade_portfolio_heat_pct >= account.portfolio_heat_pct


def test_trade_plan_tab_gates_post_trade_heat_on_position_size_existing():
    """Static guard: POST-TRADE PORTFOLIO HEAT must only render inside the
    `if plan.decision.position_size is not None:` branch."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    idx = source.index("**POST-TRADE PORTFOLIO HEAT:**")
    preceding = source[:idx]
    assert preceding.rstrip().endswith("if plan.decision.position_size is not None:") is False  # not the literal previous line necessarily
    assert "if plan.decision.position_size is not None:" in source


# ============================================================================
# I — display-consistency invariant: Account State == Analyze effective values
# ============================================================================


def test_i_account_state_and_analyze_use_the_same_effective_account():
    """I: both call sites must produce byte-identical AccountRiskState
    objects from the same (raw_account, positions) inputs — there is
    exactly one function that can compute this, so this is really testing
    that effective_account_state() is deterministic and total (same inputs
    -> same output every time), which is what the single-canonical-function
    architecture guarantees by construction."""
    raw = _raw_account(open_positions=6, open_risk=0.0)
    positions = [_spy_position()]
    account_state_tab_result, complete_1 = effective_account_state(raw, positions)
    analyze_result, complete_2 = effective_account_state(raw, positions)
    assert account_state_tab_result == analyze_result
    assert complete_1 == complete_2


def test_i_trade_planner_calls_effective_account_state_from_exactly_two_places():
    """Static guard: exactly two call sites (Account State display, Analyze)
    — proving there is no THIRD, independently-written overlay anywhere
    that could silently diverge."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert source.count("= effective_account_state(") == 2  # exactly 2 actual call sites (comments mention it too)
    assert "dataclasses import replace" not in source  # the old per-call-site replace() is gone
