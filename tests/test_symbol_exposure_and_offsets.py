"""Deterministic acceptance tests for the per-symbol position model (§2-5 of
the final Phase 3 hardening audit): signed same-symbol exposure, boundary
cases, and LONG/SHORT offset arithmetic.

BOUNDARY SEMANTICS: exactly-at-limit is ALLOWED for symbol exposure (same
"<=" floor-budget precedent as portfolio heat / net exposure / leverage).

CLASSIFICATION SEMANTICS (engine/risk/positions.py::classify_trade_risk):
RISK_REDUCING only when the trade strictly shrinks this symbol's position
magnitude (including landing exactly at flat) — same-direction adds, exact
flips, and cross-zero overshoots into a LARGER opposite position are all
RISK_INCREASING. Deliberately conservative: ambiguous cases classify as
increasing, never reducing.
"""
from __future__ import annotations

from dataclasses import replace

from engine.risk.policy import DEFAULT_RISK_POLICY
from engine.risk.positions import RISK_INCREASING, RISK_REDUCING, classify_trade_risk, max_reducing_shares
from engine.risk.sizing import compute_position_size

NLV = 100_000.0
# max_symbol_exposure_pct=10% -> $10000 cap. Everything else loosened (within
# platform ceilings) so symbol_exposure is unambiguously the constraint under test.
_POLICY = replace(
    DEFAULT_RISK_POLICY, risk_per_trade_pct=5.0, max_symbol_exposure_pct=10.0, max_gross_exposure_pct=1000.0,
    max_portfolio_heat_pct=20.0, max_leverage=4.0, max_net_exposure_pct=1000.0,
)


def _report(label, **fields):
    print(f"[{label}] " + " ".join(f"{k}={v}" for k, v in fields.items()))


def _size(existing_symbol_notional_signed, existing_gross=None, direction="LONG", entry=100.0, stop=99.0):
    if existing_gross is None:
        existing_gross = abs(existing_symbol_notional_signed)
    return compute_position_size(
        entry=entry, stop=stop, net_liquidation_value=NLV, risk_policy=_POLICY, buying_power=NLV,
        direction=direction, existing_symbol_notional_signed=existing_symbol_notional_signed,
        existing_gross_exposure_value=existing_gross,
    )


# ============================================================================
# 4. SYMBOL EXPOSURE BOUNDARY TESTS
# ============================================================================


def test_a_existing_8000_remaining_2000_allows_20_shares():
    r = _size(existing_symbol_notional_signed=8_000.0)
    _report("A", existing=8000, shares=r.shares, binding=r.binding_constraint)
    assert r.evidence["candidates_shares"]["symbol_exposure"] == 20.0
    assert r.shares == 20
    assert r.binding_constraint == "symbol_exposure"


def test_b_existing_10000_at_limit_blocks_additional_shares():
    r = _size(existing_symbol_notional_signed=10_000.0)
    _report("B", existing=10000, shares=r.shares, binding=r.binding_constraint)
    assert r.shares == 0
    assert r.binding_constraint == "symbol_exposure"


def test_c_existing_9999_one_more_share_would_exceed():
    r = _size(existing_symbol_notional_signed=9_999.0)
    _report("C", existing=9999, shares=r.shares)
    assert r.shares == 0
    assert r.binding_constraint == "symbol_exposure"


def test_d_existing_9900_one_share_lands_exactly_at_limit_allowed():
    r = _size(existing_symbol_notional_signed=9_900.0)
    _report("D", existing=9900, shares=r.shares, position_value=r.position_value)
    assert r.shares == 1
    assert 9_900.0 + r.position_value == 10_000.0  # exactly at the ceiling
    assert r.binding_constraint == "symbol_exposure"


def test_e_analyzing_aapl_is_unaffected_by_spy_exposure():
    # SPY=9000 existing, AAPL=2000 existing (gross=11000). Analyzing AAPL must
    # use ONLY AAPL's own $2000 for the symbol_exposure constraint — SPY's
    # $9000 must never leak into AAPL's per-symbol cap.
    r = _size(existing_symbol_notional_signed=2_000.0, existing_gross=11_000.0)
    _report("E (AAPL)", spy_existing=9000, aapl_existing=2000, shares=r.shares, symbol_budget=r.evidence["candidates_shares"]["symbol_exposure"])
    assert r.evidence["candidates_shares"]["symbol_exposure"] == 80.0  # (10000-2000)/100, NOT affected by SPY's 9000
    assert r.shares == 80
    assert r.binding_constraint == "symbol_exposure"


# ============================================================================
# 5. LONG/SHORT OFFSET TESTS
# ============================================================================


def test_existing_long_plus_proposed_long_is_risk_increasing():
    assert classify_trade_risk(80, "LONG", 30) == RISK_INCREASING
    r = _size(existing_symbol_notional_signed=8_000.0)
    # Adding to an existing long shrinks remaining symbol-exposure budget in the usual way.
    assert r.evidence["candidates_shares"]["symbol_exposure"] == 20.0


def test_existing_short_plus_proposed_short_is_risk_increasing():
    assert classify_trade_risk(-80, "SHORT", 30) == RISK_INCREASING
    r = _size(existing_symbol_notional_signed=-8_000.0, direction="SHORT", stop=101.0)
    assert r.evidence["candidates_shares"]["symbol_exposure"] == 20.0


def test_existing_long_plus_proposed_short_smaller_is_risk_reducing():
    # existing +80, propose SHORT 30 -> post-trade +50 -- exposure DECREASES.
    assert classify_trade_risk(80, "SHORT", 30) == RISK_REDUCING
    assert max_reducing_shares(80, "SHORT") == 80.0
    r = _size(existing_symbol_notional_signed=8_000.0, direction="SHORT", stop=101.0)
    # Signed arithmetic gives MORE budget (not less) since a SHORT here shrinks abs notional.
    _report("LONG+80 propose SHORT", symbol_budget=r.evidence["candidates_shares"]["symbol_exposure"])
    assert r.evidence["candidates_shares"]["symbol_exposure"] > 100.0  # far more than the naive "add" would allow


def test_existing_long_plus_proposed_short_larger_crosses_zero_is_risk_increasing():
    # existing +80, propose SHORT 200 -> post-trade -120 -- exposure INCREASES (now short 120 vs long 80).
    assert classify_trade_risk(80, "SHORT", 200) == RISK_INCREASING


def test_existing_long_plus_proposed_short_exact_close_is_risk_reducing():
    # existing +80, propose SHORT 80 -> post-trade exactly 0 -- a full close.
    assert classify_trade_risk(80, "SHORT", 80) == RISK_REDUCING


def test_existing_short_plus_proposed_long_smaller_is_risk_reducing():
    # existing -80, propose LONG 30 -> post-trade -50 -- exposure DECREASES.
    assert classify_trade_risk(-80, "LONG", 30) == RISK_REDUCING
    assert max_reducing_shares(-80, "LONG") == 80.0
    r = _size(existing_symbol_notional_signed=-8_000.0, direction="LONG")
    _report("SHORT-80 propose LONG", symbol_budget=r.evidence["candidates_shares"]["symbol_exposure"])
    assert r.evidence["candidates_shares"]["symbol_exposure"] > 100.0


def test_existing_short_plus_proposed_long_larger_crosses_zero_is_risk_increasing():
    # existing -80, propose LONG 200 -> post-trade +120 -- exposure INCREASES.
    assert classify_trade_risk(-80, "LONG", 200) == RISK_INCREASING


def test_existing_short_plus_proposed_long_exact_close_is_risk_reducing():
    assert classify_trade_risk(-80, "LONG", 80) == RISK_REDUCING


def test_max_reducing_shares_is_zero_for_same_direction():
    assert max_reducing_shares(80, "LONG") == 0.0
    assert max_reducing_shares(-80, "SHORT") == 0.0


def test_max_reducing_shares_is_zero_when_flat():
    assert max_reducing_shares(0, "LONG") == 0.0
    assert max_reducing_shares(0, "SHORT") == 0.0


def test_classify_trade_risk_opening_new_position_is_increasing():
    assert classify_trade_risk(0, "LONG", 50) == RISK_INCREASING
    assert classify_trade_risk(0, "SHORT", 50) == RISK_INCREASING
