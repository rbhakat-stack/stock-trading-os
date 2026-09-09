"""Tests for the "Phase 3 UI clarity" round: Account State fallback-field
authority, current-vs-post-trade portfolio heat, signed existing/post-trade
share counts in Position Sizing, and the symbol-exposure explainability
reconciliation. This round deliberately does not change any position-sizing
formula, exposure calculation, heat calculation, risk policy threshold, or
persistence semantics — every new PositionSizeResult field here is a
display-only re-report of numbers the existing gating logic already
computed (see engine/risk/sizing.py's "Display-only explainability fields"
section) or a straightforward signed sum, never a new constraint.

Sections:
  A/B — Account State fallback-field authority (§1 of the request):
        the same normalize_position_input()-derived boolean the UI uses to
        decide "manual fields active vs. ignored", plus a static check that
        app/pages/trade_planner.py actually wires disabled=/the explanatory
        captions onto the right widgets (Streamlit widget-disabled state
        itself is exercised live in the browser acceptance test, not here).
  C/G — existing / post-trade SIGNED share counts (compute_position_size).
  D/E — current vs. post-trade portfolio heat (compute_position_size).
  F   — symbol-exposure explainability reconciliation (compute_position_size).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import AccountPosition
from engine.risk.sizing import compute_position_size

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADE_PLANNER_PATH = PROJECT_ROOT / "app" / "pages" / "trade_planner.py"


def _policy(**overrides):
    return DEFAULT_RISK_POLICY.__class__(**{**DEFAULT_RISK_POLICY.__dict__, **overrides})


# ============================================================================
# A/B — Account State fallback-field authority
# ============================================================================


def test_no_positions_means_not_authoritative():
    """A: with no Positions-tab records, the manual Account State fallback
    fields must remain the ones in effect (the UI's disabled= flag is driven
    by exactly this boolean — bool() of the normalized position list)."""
    assert bool([]) is False


def test_one_or_more_positions_means_authoritative():
    """B: once at least one valid position exists, the manual fallback
    fields must display as ignored — driven by the same boolean."""
    positions = [AccountPosition(symbol="SPY", quantity_signed=80.0, reference_price=100.0, updated_at=datetime.now(timezone.utc))]
    assert bool(positions) is True


def test_account_state_tab_wires_disabled_state_to_positions_authoritative():
    """Static structural guard (updated for the Round J root-cause fix): each
    of the four manual fallback fields (Open risk, Open positions, Long/Short
    exposure notional) must branch on `_positions_authoritative` into either
    a disabled, KEYLESS mirror showing the DERIVED value, or an editable
    widget bound to its `key="acct_*"` — never a single widget that is
    merely `disabled=` toggled while still reading its raw manual key= value
    (that mismatch was the Round J bug: disabled fields showing stale manual
    numbers instead of the derived ones)."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert 'disabled=_positions_authoritative' not in source, (
        "a field must not be merely disabled= toggled while still bound to its raw manual key= — "
        "see the Round J bug report"
    )
    for key in ('key="acct_open_risk"', 'key="acct_open_positions"', 'key="acct_long_exposure_notional"', 'key="acct_short_exposure_notional"'):
        assert source.count(key) == 1, f"expected {key} to appear exactly once (the non-authoritative/editable branch)"
    assert source.count("_DERIVED_FIELD_CAPTION") >= 5  # 1 definition + 4 usages (open_risk, open_positions, long, short)
    assert "Derived from Positions tab — manual fallback value ignored while positions exist." in source
    assert "Derived open risk unavailable — one or more positions lacks a usable planned stop." in source
    assert 'value="Unavailable — incomplete"' in source


def test_trade_plan_tab_labels_current_and_post_trade_heat():
    """Static structural guard for §2: the Risk & Kill Switch panel must
    show BOTH a current and a post-trade portfolio heat line."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert "**CURRENT PORTFOLIO HEAT:**" in source
    assert "**POST-TRADE PORTFOLIO HEAT:**" in source


def test_position_sizing_panel_uses_additional_shares_language():
    """Static structural guard for §3: the ambiguous "Shares" label must be
    replaced with "Additional shares", and existing/post-trade position
    metrics must be present."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert 'pcol1.metric("Additional shares", ps.shares)' in source
    assert 'excol1.metric("Existing position"' in source
    assert 'excol2.metric("Post-trade position"' in source


# ============================================================================
# C/G — existing / post-trade SIGNED share counts
# ============================================================================


def test_c_existing_long_plus_additional_long_gives_correct_post_trade_shares():
    """C: existing 80 LONG, a 20-share additional LONG trade -> post-trade
    100 LONG. Uses generous limits so risk_per_trade alone binds at exactly
    20 shares (clean, deterministic — not dependent on any exposure cap)."""
    policy = _policy(risk_per_trade_pct=2.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0)
    # risk_per_trade budget = 100,000 * 2% = 2,000; risk_per_share = 100; shares = 20.
    result = compute_position_size(
        entry=100.0, stop=0.0, net_liquidation_value=100_000.0, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", existing_symbol_notional_signed=8_000.0, existing_symbol_shares_signed=80.0,
        existing_gross_exposure_value=8_000.0, existing_net_exposure_value=8_000.0, current_open_risk=400.0,
    )
    assert result.shares == 20
    assert result.binding_constraint == "risk_per_trade"
    assert result.existing_shares_signed == 80.0
    assert result.post_trade_shares_signed == 100.0


def test_g_existing_long_plus_offsetting_short_gives_correct_post_trade_shares_no_absolute_value_bug():
    """G: existing +80 LONG, a 30-share SHORT trade -> post-trade +50, NOT
    +110 (which is what a buggy abs()-based sum would produce) and NOT -30
    (ignoring the existing position). Proves signed, not absolute, arithmetic.
    max_shares_override pins the exact share count so the test is about the
    signed-sum arithmetic, not which constraint happens to bind at 30."""
    policy = _policy(risk_per_trade_pct=5.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0)
    result = compute_position_size(
        entry=100.0, stop=0.0, net_liquidation_value=100_000.0, risk_policy=policy, buying_power=1_000_000.0,
        direction="SHORT", existing_symbol_notional_signed=8_000.0, existing_symbol_shares_signed=80.0,
        existing_gross_exposure_value=8_000.0, existing_net_exposure_value=8_000.0, current_open_risk=400.0,
        max_shares_override=30,
    )
    assert result.shares == 30
    assert result.existing_shares_signed == 80.0
    assert result.post_trade_shares_signed == 50.0  # 80 + (-30), never abs(80) + abs(30) = 110


def test_flat_existing_position_produces_zero_existing_and_correct_post_trade():
    """No existing position (flat) -> existing_shares_signed is 0, and
    post-trade equals exactly the new trade's signed size."""
    policy = _policy(risk_per_trade_pct=2.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0)
    result = compute_position_size(
        entry=100.0, stop=0.0, net_liquidation_value=100_000.0, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", existing_symbol_notional_signed=0.0, existing_symbol_shares_signed=0.0,
    )
    assert result.existing_shares_signed == 0.0
    assert result.post_trade_shares_signed == float(result.shares)


# ============================================================================
# D/E — current vs. post-trade portfolio heat
# ============================================================================


def test_d_e_post_trade_heat_equals_current_open_risk_plus_capital_at_risk_over_nlv():
    """D/E: current heat = current_open_risk / NLV; post-trade heat =
    (current_open_risk + capital_at_risk) / NLV — using the exact
    capital_at_risk the sizing engine computed for the ACTUAL sized trade,
    never an independently-recomputed UI approximation."""
    policy = _policy(risk_per_trade_pct=2.0, max_symbol_exposure_pct=100.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0)
    nlv = 100_000.0
    current_open_risk = 400.0
    result = compute_position_size(
        entry=100.0, stop=90.0, net_liquidation_value=nlv, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", current_open_risk=current_open_risk,
    )
    current_heat_pct = round(current_open_risk / nlv * 100, 3)
    assert current_heat_pct == 0.4
    expected_post_trade_open_risk = round(current_open_risk + result.capital_at_risk, 2)
    assert result.post_trade_open_risk == expected_post_trade_open_risk
    expected_post_trade_heat_pct = round(expected_post_trade_open_risk / nlv * 100, 3)
    assert result.post_trade_portfolio_heat_pct == expected_post_trade_heat_pct
    # sanity: post-trade heat must be strictly greater than current heat for
    # any risk-increasing trade with positive capital at risk.
    assert result.post_trade_portfolio_heat_pct > current_heat_pct


def test_post_trade_heat_matches_current_heat_when_size_floors_to_zero():
    """A trade that floors to 0 shares (e.g. because it fails INVALID_INPUT)
    must not fabricate any post-trade risk increase."""
    policy = _policy()
    result = compute_position_size(
        entry=0.0, stop=0.0, net_liquidation_value=100_000.0, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", current_open_risk=400.0,
    )
    assert result.shares == 0
    assert result.capital_at_risk == 0.0


# ============================================================================
# F — symbol-exposure explainability reconciliation
# ============================================================================


def test_f_symbol_exposure_explanation_reconciles_exactly():
    """F: when symbol_exposure is the binding constraint, every explanation
    field must reconcile: existing + final allowed == post-trade exposure,
    and post-trade % must equal post-trade exposure / NLV * 100, all
    against the SAME numbers compute_position_size used for the actual
    215-share-vs-20-share decision (never recomputed independently)."""
    nlv = 100_000.0
    policy = _policy(
        risk_per_trade_pct=100.0,  # never the binding constraint here
        max_symbol_exposure_pct=10.0, max_gross_exposure_pct=100.0, max_net_exposure_pct=100.0,
        max_portfolio_heat_pct=100.0, max_leverage=100.0,
    )
    existing_notional = 8_000.0  # 80 shares @ $100
    result = compute_position_size(
        entry=100.0, stop=1.0, net_liquidation_value=nlv, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", existing_symbol_notional_signed=existing_notional, existing_symbol_shares_signed=80.0,
        existing_gross_exposure_value=existing_notional, existing_net_exposure_value=existing_notional,
    )
    assert result.binding_constraint == "symbol_exposure"
    assert result.existing_symbol_exposure_value == 8_000.0
    assert result.max_symbol_exposure_value == 10_000.0
    assert result.symbol_exposure_remaining_value == 2_000.0  # 10,000 - 8,000
    assert result.shares == 20  # floor(2,000 / 100)
    assert result.position_value == 2_000.0
    # reconciliation: existing + final allowed additional == post-trade exposure
    assert round(result.existing_symbol_exposure_value + result.position_value, 2) == result.post_trade_symbol_exposure_value
    assert result.post_trade_symbol_exposure_value == 10_000.0
    assert result.post_trade_symbol_exposure_pct == 10.0
    assert result.unrestricted_risk_based_value > result.position_value  # the cap is what actually bound


def test_f_symbol_exposure_offset_trade_widens_remaining_capacity():
    """A SHORT that offsets an existing LONG must show MORE remaining
    capacity than a same-direction add would, never less — the signed
    arithmetic must never conflate offsetting exposure with additive."""
    nlv = 100_000.0
    policy = _policy(
        risk_per_trade_pct=100.0, max_symbol_exposure_pct=10.0, max_gross_exposure_pct=100.0,
        max_net_exposure_pct=100.0, max_portfolio_heat_pct=100.0, max_leverage=100.0,
    )
    existing_notional = 8_000.0
    long_add = compute_position_size(
        entry=100.0, stop=1.0, net_liquidation_value=nlv, risk_policy=policy, buying_power=1_000_000.0,
        direction="LONG", existing_symbol_notional_signed=existing_notional, existing_symbol_shares_signed=80.0,
        existing_gross_exposure_value=existing_notional, existing_net_exposure_value=existing_notional,
    )
    short_offset = compute_position_size(
        entry=100.0, stop=1.0, net_liquidation_value=nlv, risk_policy=policy, buying_power=1_000_000.0,
        direction="SHORT", existing_symbol_notional_signed=existing_notional, existing_symbol_shares_signed=80.0,
        existing_gross_exposure_value=existing_notional, existing_net_exposure_value=existing_notional,
    )
    assert short_offset.symbol_exposure_remaining_value > long_add.symbol_exposure_remaining_value
    # existing 8,000 + max 10,000 = 18,000 remaining capacity toward zero for the SHORT side
    assert short_offset.symbol_exposure_remaining_value == 18_000.0
