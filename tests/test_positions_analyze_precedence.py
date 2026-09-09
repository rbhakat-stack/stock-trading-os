"""Regression tests for the Round I bug: app/pages/trade_planner.py's Trade
Plan Analyze path called a helper (`_positions_from_editor_df`) that no
longer existed after the Round H Save Positions refactor renamed it to
`_normalize_editor_df` and changed its return signature to a tuple — every
call site was updated except this one, causing
`NameError: name '_positions_from_editor_df' is not defined` on Analyze.

The fix factors the actual source-precedence DECISION (live unsaved editor
state vs. persisted DB state vs. empty) into
engine.risk.positions.resolve_positions_precedence — pure, pandas/Streamlit
-free, and exercised directly here — so this exact class of regression
(a page-level call site drifting out of sync with a renamed/moved helper)
cannot silently reappear without a test catching it: the precedence logic
itself has no dependency on the Streamlit page module at all.

These tests also cover the module's static self-consistency (no leftover
reference to a helper that doesn't exist) and the 8 acceptance scenarios
from the regression report.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from engine.risk.positions import AccountPosition, resolve_positions_precedence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADE_PLANNER_PATH = PROJECT_ROOT / "app" / "pages" / "trade_planner.py"


def _pos(symbol: str, qty: float, ref: float = 100.0) -> AccountPosition:
    return AccountPosition(symbol=symbol, quantity_signed=qty, reference_price=ref, updated_at=datetime.now(timezone.utc))


# ============================================================================
# Static regression guard: no dangling reference to a deleted/renamed helper.
# This is exactly the class of bug that shipped — a call site referencing a
# name that no longer has a def anywhere in the module.
# ============================================================================


def test_trade_planner_module_defines_every_name_it_calls_for_positions():
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    defined_names = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    # The exact name that regressed: must either not be called at all, or
    # (if ever reintroduced) must actually be defined somewhere in the file.
    assert "_positions_from_editor_df" not in called_names, (
        "trade_planner.py calls '_positions_from_editor_df', which does not exist as a def in this "
        "module — this is the exact Round I regression (NameError on Trade Plan Analyze)."
    )

    # General guard: every locally-defined-style helper actually called in
    # this module (leading underscore = private to this file) must resolve
    # to a real function defined in the file itself.
    local_style_calls = {n for n in called_names if n.startswith("_")}
    missing = local_style_calls - defined_names
    assert not missing, f"trade_planner.py calls undefined local helper(s): {sorted(missing)}"


def test_trade_planner_uses_one_canonical_positions_resolver_for_analyze():
    """Round J follow-up: the positions precedence decision is now computed
    ONCE at module top-level into `_resolved_positions` (reused by Account
    State's display AND Trade Plan's Analyze) rather than via a per-call
    `_resolve_positions_for_analyze()` helper that could be invoked from
    only one place — this guards against the display/Analyze values ever
    silently diverging again (the Round J bug's root cause)."""
    source = TRADE_PLANNER_PATH.read_text(encoding="utf-8")
    assert "def _resolve_positions_for_analyze(" not in source, (
        "the per-call resolver was replaced by a single top-level "
        "_resolved_positions computation — see resolve_positions_precedence("
    )
    assert source.count("resolve_positions_precedence(") == 1, (
        "positions precedence must be resolved exactly once per rerun, reused by every tab"
    )
    assert "live_positions = _resolved_positions" in source
    assert source.count("def _normalize_editor_df(") == 1
    assert source.count("def _positions_df_from_repo(") == 1


# ============================================================================
# resolve_positions_precedence — the actual decision logic, pure/unit-tested
# ============================================================================


def test_case1_no_live_state_at_all_uses_persisted():
    """Regression test 1 & 5 & 8: Analyze before ever visiting Positions tab,
    or after a hard reload/login — no live editor state exists — must use
    persisted positions, and must not raise."""
    persisted = [_pos("SPY", 80)]
    positions, used_fallback = resolve_positions_precedence([], [], lambda: persisted)
    assert positions == persisted
    assert used_fallback is False


def test_case2_live_state_matches_persisted_after_visiting_tab():
    """Regression test 2: after visiting Positions tab (editor now holds the
    same rows as what's persisted), Analyze should use that live state."""
    live = [_pos("SPY", 80)]
    positions, used_fallback = resolve_positions_precedence(live, [], lambda: (_ for _ in ()).throw(AssertionError("should not load persisted")))
    assert positions == live
    assert used_fallback is False


def test_case3_live_edit_before_save_takes_effect():
    """Regression test 3 & the live-editor-semantics requirement: editing a
    quantity but not clicking Save must still affect Analyze immediately."""
    live_edited = [_pos("SPY", 90)]  # changed from 80 -> 90, unsaved
    persisted_still_80 = [_pos("SPY", 80)]
    positions, used_fallback = resolve_positions_precedence(live_edited, [], lambda: persisted_still_80)
    assert positions == live_edited
    assert positions[0].quantity_signed == 90.0
    assert used_fallback is False


def test_case4_after_save_live_and_persisted_agree():
    """Regression test 4: after Save, the editor is reloaded from the DB, so
    live and persisted are the same — Analyze must still work and reflect
    the saved value."""
    saved = [_pos("SPY", 90)]
    positions, used_fallback = resolve_positions_precedence(saved, [], lambda: saved)
    assert positions == saved
    assert used_fallback is False


def test_case6_no_positions_anywhere_falls_through_to_empty_for_account_state_fallback():
    """Regression test 6: no live positions and nothing persisted -> empty
    list, so callers fall back to manual Account State fields (unchanged
    behavior — this function must not invent positions)."""
    positions, used_fallback = resolve_positions_precedence([], [], lambda: [])
    assert positions == []
    assert used_fallback is False


def test_case7_malformed_live_row_fails_closed_to_persisted():
    """Regression test 7: a malformed live row must NEVER be silently
    dropped and combined with the valid rows into a partial position list
    (that would understate exposure/open risk) — the whole live edit is
    discarded in favor of the last known-good persisted state, and the
    caller is told a fallback occurred so it can warn the user."""
    live_valid_subset = [_pos("SPY", 80)]  # one valid row alongside a malformed one
    live_errors = ["AAPL: Reference Price must be greater than 0."]
    persisted = [_pos("SPY", 80), _pos("AAPL", 100)]
    positions, used_fallback = resolve_positions_precedence(live_valid_subset, live_errors, lambda: persisted)
    assert positions == persisted  # NOT the partial live_valid_subset
    assert used_fallback is True


def test_case8_persisted_positions_exist_but_no_live_state_uses_persisted():
    """Regression test 8 (explicit restatement of case 1): persisted
    positions exist in the DB but positions_df session state is missing
    entirely (fresh session) -> persisted positions must be used, not an
    empty list."""
    persisted = [_pos("SPY", 80), _pos("NVDA", -50)]
    positions, used_fallback = resolve_positions_precedence([], [], lambda: persisted)
    assert positions == persisted
    assert used_fallback is False


def test_load_persisted_is_not_called_when_live_state_is_valid_and_nonempty():
    """load_persisted must be lazy — never called (and never hit the DB) when
    valid live positions already answer the question."""
    calls = []

    def _load():
        calls.append(1)
        return []

    live = [_pos("SPY", 80)]
    resolve_positions_precedence(live, [], _load)
    assert calls == []
