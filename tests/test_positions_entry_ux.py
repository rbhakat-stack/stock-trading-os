"""Round K: fixes a real Positions-tab UX bug where the UI showed hard
red validation errors ("Signed Quantity is required.", "Reference Price
is required.") and an "OPEN RISK DATA INCOMPLETE" banner the instant each
field of a brand-new row was entered — before the user had even reached
the next field, let alone finished the row.

ROOT CAUSE #1 (premature validation): app/pages/trade_planner.py fed every
row of the live, possibly-mid-edit grid through normalize_position_input()
— the SAME strict, all-required-fields-present validation used at Save
time — on every single rerun (which Streamlit triggers on every cell
commit). normalize_position_input() treats a blank required field
identically to a malformed one (both return an error string), so a row
with only Symbol typed in produced "Signed Quantity is required.": a
correct answer to "is this row ready to persist," but the WRONG question
to be asking while the user is still mid-entry. That same strict result
also fed engine.risk.positions.resolve_positions_precedence() at module
top-level, so an in-progress row also transiently and disruptively
overrode Analyze's live/persisted precedence (with a visible fallback
warning) on every keystroke.

FIX: engine.risk.positions.classify_position_row() distinguishes a row
that is still being typed into (INCOMPLETE — a required field is blank,
not wrong) from one with an actual mistake (INVALID — a field has a value
but it's malformed). app/pages/trade_planner.py's live display and the
top-level precedence resolution now use this classification (via the new
_classify_editor_df helper) and only surface INCOMPLETE rows as a soft,
non-error note — never as a hard error, and never as an Analyze-precedence
disruption. Save itself is unaffected: it always re-validates with the
original strict normalize_position_input()-based check, so an incomplete
row still correctly blocks Save with the exact expected message the
moment the user actually clicks Save (the "explicit commit" point).

ROOT CAUSE #2 (apparent double-entry): the dominant contributor found was
the same strict-validation-on-every-keystroke behavor above triggering an
EXTRA, unnecessary DB round-trip (resolve_positions_precedence's
load_persisted() fallback) on every rerun where any row was merely
incomplete — slowing every such rerun and widening the window for a
frontend/backend desync in the underlying glide-data-grid widget (a
known, still only partially avoidable Streamlit + glide-data-grid
characteristic documented in earlier rounds of this project). Removing
that per-keystroke DB call (a direct consequence of the Root Cause #1
fix) measurably reduces the frequency of the symptom, though some
residual frontend-timing sensitivity is inherent to the underlying
widget and is not fully eliminable from server-side Python alone — see
the session report for the live-browser verification of this round's fix.
"""
from __future__ import annotations

from pathlib import Path

from engine.risk.positions import (
    AccountPosition,
    ROW_EMPTY,
    ROW_INCOMPLETE,
    ROW_INVALID,
    ROW_VALID,
    classify_position_row,
    derive_open_risk,
    normalize_position_input,
    resolve_positions_precedence,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADE_PLANNER_PATH = PROJECT_ROOT / "app" / "pages" / "trade_planner.py"


# ============================================================================
# classify_position_row — the exact §8 examples
# ============================================================================


def test_classify_all_blank_is_empty():
    assert classify_position_row(None, None, None) == ROW_EMPTY


def test_classify_symbol_only_is_incomplete():
    assert classify_position_row("SPY", None, None) == ROW_INCOMPLETE


def test_classify_symbol_and_quantity_is_incomplete():
    assert classify_position_row("SPY", 80, None) == ROW_INCOMPLETE


def test_classify_symbol_quantity_price_is_valid():
    assert classify_position_row("SPY", 80, 100) == ROW_VALID


def test_classify_full_row_is_valid():
    assert classify_position_row("SPY", 80, 100, 95, 98) == ROW_VALID


def test_classify_malformed_quantity_is_invalid():
    assert classify_position_row("SPY", "abc", 100) == ROW_INVALID


def test_classify_zero_quantity_is_invalid():
    assert classify_position_row("SPY", 0, 100) == ROW_INVALID


def test_classify_negative_price_is_invalid():
    assert classify_position_row("SPY", 80, -50) == ROW_INVALID


def test_classify_never_disagrees_with_normalize_on_validity():
    """classify_position_row must never call a row VALID that
    normalize_position_input would actually reject, and vice versa — the
    two must stay in lockstep since classify delegates to normalize."""
    cases = [
        ("SPY", 80, 100, 95, 98), ("SPY", 80, 100, None, None), ("SPY", -50, 120, 126, 123),
        ("SPY", 80, 100, -95, None), ("SPY", 80, 0, None, None), ("", 80, 100, None, None),
    ]
    for symbol, qty, price, stop, avg in cases:
        state = classify_position_row(symbol, qty, price, stop, avg)
        position, error = normalize_position_input(symbol, qty, price, stop, avg)
        if state == ROW_VALID:
            assert position is not None, (symbol, qty, price, stop, avg)
        else:
            assert position is None, (symbol, qty, price, stop, avg)


# ============================================================================
# §9 — exact entry sequence: no premature errors while progressing
# ============================================================================


def test_step1_symbol_only_produces_no_required_field_error_state():
    """Step 1: Symbol=SPY -> INCOMPLETE, not INVALID — the UI must not show
    "Signed Quantity is required." live for this state."""
    assert classify_position_row("SPY", None, None) == ROW_INCOMPLETE


def test_step2_symbol_and_quantity_produces_no_required_field_error_state():
    """Step 2: + Signed Quantity=80 -> still INCOMPLETE, not INVALID — the
    UI must not show "Reference Price is required." live for this state."""
    assert classify_position_row("SPY", 80, None) == ROW_INCOMPLETE


def test_step3_symbol_quantity_price_is_valid_with_stop_pending():
    """Step 3: + Reference Price=100 -> VALID (row itself is fine); open
    risk is separately and correctly incomplete because the stop hasn't
    been entered yet — that's a different, non-error dimension."""
    assert classify_position_row("SPY", 80, 100) == ROW_VALID
    position, error = normalize_position_input("SPY", 80, 100)
    assert error is None and position is not None
    assert position.planned_stop_price is None
    open_risk = derive_open_risk([position])
    assert open_risk.complete is False  # correct fact, not a row error


def test_step4_planned_stop_makes_open_risk_complete():
    """Step 4: + Planned Stop=95 -> row VALID, and open risk completeness
    becomes True."""
    assert classify_position_row("SPY", 80, 100, 95) == ROW_VALID
    position, _ = normalize_position_input("SPY", 80, 100, 95)
    open_risk = derive_open_risk([position])
    assert open_risk.complete is True
    assert open_risk.open_risk == 400.0


def test_step5_average_price_still_valid():
    """Step 5: + Average Price=98 -> still VALID (average price was
    already optional)."""
    assert classify_position_row("SPY", 80, 100, 95, 98) == ROW_VALID


def test_full_sequence_saves_once_no_second_entry_needed():
    """The completed row from the exact §9/§11 sequence normalizes and
    saves correctly in a single pass — no special-casing needed for
    "already progressed through incomplete states earlier"."""
    position, error = normalize_position_input("SPY", 80, 100, 95, 98)
    assert error is None
    assert position == AccountPosition(
        symbol="SPY", quantity_signed=80.0, reference_price=100.0, planned_stop_price=95.0,
        average_price=98.0, updated_at=position.updated_at,
    )


# ============================================================================
# §10 — Save validation (the STRICT path, unchanged, exercised at Save time)
# ============================================================================


def test_save_a_symbol_only_blocks_with_exact_message():
    position, error = normalize_position_input("SPY", None, None)
    assert position is None
    assert error == "SPY: Signed Quantity is required."


def test_save_b_symbol_and_quantity_blocks_with_exact_message():
    position, error = normalize_position_input("SPY", 80, None)
    assert position is None
    assert error == "SPY: Reference Price is required."


def test_save_c_complete_required_fields_stop_blank_saves_and_reports_incomplete():
    position, error = normalize_position_input("SPY", 80, 100)
    assert error is None
    assert position is not None
    assert position.planned_stop_price is None
    open_risk = derive_open_risk([position])
    assert open_risk.complete is False
    assert open_risk.positions_missing_stop == ["SPY"]


def test_save_d_full_row_saves():
    position, error = normalize_position_input("SPY", 80, 100, 95, 98)
    assert error is None
    assert position.symbol == "SPY" and position.quantity_signed == 80.0
    assert position.reference_price == 100.0 and position.planned_stop_price == 95.0
    assert position.average_price == 98.0


# ============================================================================
# Top-level precedence: an in-progress new row must not disrupt an
# already-valid live edit of a DIFFERENT symbol, nor trigger a fallback
# warning — this is the actual mechanism behind the reported "aggressive
# red messages on every cell entry" and the associated extra DB round-trip.
# ============================================================================


def test_incomplete_new_row_does_not_disrupt_other_valid_live_positions():
    """Simulates: grid has an already-valid SPY row plus a brand-new,
    still-being-typed AAPL row (symbol only so far). The top-level
    resolution must keep using [SPY] with NO fallback warning — an
    incomplete row must be excluded, exactly like an empty row, never
    treated as a "malformed live edit" that disrupts everything else."""
    rows = [("SPY", 80, 100, 95, 98), ("AAPL", None, None, None, None)]
    live_positions, live_errors = [], []
    for symbol, qty, price, stop, avg in rows:
        state = classify_position_row(symbol, qty, price, stop, avg)
        if state == ROW_EMPTY or state == ROW_INCOMPLETE:
            continue
        position, error = normalize_position_input(symbol, qty, price, stop, avg)
        if state == ROW_VALID:
            live_positions.append(position)
        elif state == ROW_INVALID:
            live_errors.append(error)

    def _load_persisted():
        raise AssertionError("load_persisted must not be called when live edits are already valid")

    resolved, used_fallback = resolve_positions_precedence(live_positions, live_errors, _load_persisted)
    assert used_fallback is False
    assert [p.symbol for p in resolved] == ["SPY"]


def test_invalid_new_row_still_falls_back_correctly_fail_closed():
    """A GENUINELY malformed row (not merely incomplete) must still trigger
    the existing fail-closed fallback-to-persisted behavior — this
    round's fix narrows what counts as "malformed" to true INVALID rows,
    it does not weaken the fallback itself."""
    rows = [("SPY", 80, 100, 95, 98), ("AAPL", -1, -50, None, None)]  # AAPL: negative qty AND price -> INVALID
    live_positions, live_errors = [], []
    for symbol, qty, price, stop, avg in rows:
        state = classify_position_row(symbol, qty, price, stop, avg)
        if state in (ROW_EMPTY, ROW_INCOMPLETE):
            continue
        position, error = normalize_position_input(symbol, qty, price, stop, avg)
        if state == ROW_VALID:
            live_positions.append(position)
        elif state == ROW_INVALID:
            live_errors.append(error)

    persisted = [AccountPosition(symbol="SPY", quantity_signed=80.0, reference_price=100.0, updated_at=None)]
    resolved, used_fallback = resolve_positions_precedence(live_positions, live_errors, lambda: persisted)
    assert used_fallback is True
    assert resolved == persisted


# ============================================================================
# §13 — static guards: editor state must not be overwritten on every rerun
# ============================================================================


def test_positions_state_initial_load_is_guarded_not_unconditional():
    """Round L: the one-time-only DB load into positions_editor_seed /
    persisted_positions_cache must stay behind an atomic, self-healing
    marker guard (`_positions_state_initialized`) — not a check on one of
    the data keys itself (see the acct_open_risk KeyError root cause: that
    pattern lets an interrupted rerun permanently skip re-initialization of
    the remaining fields). An unconditional reload every rerun would also
    overwrite in-progress live edits, the "editor state reset during
    reruns" failure mode §6/§13 warn against."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert source.count('if not st.session_state.get("_positions_state_initialized", False):') == 1
    assert 'st.session_state["_positions_state_initialized"] = True' in source


def test_positions_editor_seed_never_reassigned_from_live_widget_output():
    """Round L fix for the "double entry" bug: st.session_state["positions_editor_seed"]
    (the data_editor's `value=` seed) must ONLY ever be assigned at init and
    on a SUCCESSFUL Save — NEVER reassigned from the widget's own
    just-returned output (`edited_positions_df`) on an ordinary rerun. That
    reassign-every-rerun pattern (the previous architecture) is exactly what
    could race with the frontend's in-flight edit and require a field to be
    entered twice.

    UPDATED (Phase 3 Positions-save-lifecycle report): a FAILED Save must no
    longer reassign positions_editor_seed either — doing so replaced the
    user's still-visible, unsaved editor rows with the (possibly empty)
    reloaded DB state, which produced the false "No positions recorded"
    message and, via _positions_authoritative flipping back to False, the
    acct_open_positions KeyError. See
    tests/test_trade_planner_state_contract.py::test_failed_positions_save_never_resets_the_live_editor
    for the dedicated regression guard on the failure path."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert 'st.session_state["positions_editor_seed"] = edited_positions_df' not in source
    assert source.count('st.session_state["positions_editor_seed"] = _positions_df_from_repo(') == 1  # save success only
    assert 'st.session_state["positions_editor_seed"] = _positions_editor_seed' in source  # the one-time init assignment


def test_classify_editor_df_used_for_live_display_not_strict_normalize():
    """Static guard: the live in-tab display path must use the
    non-disruptive classifier, not the strict all-required validator."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert "current_positions, invalid_messages, incomplete_labels = _classify_editor_df(edited_positions_df)" in source
    assert "strict_positions, strict_errors = _normalize_editor_df(edited_positions_df)" in source


def test_save_button_disabled_only_for_invalid_not_incomplete_rows():
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert 'st.button("Save Positions", type="primary", disabled=bool(invalid_messages))' in source
