"""Static regression guards for the Phase 3 Positions-save-lifecycle report.

app/pages/trade_planner.py is a Streamlit page script (it calls st.title(),
require_authenticated(), etc. at module scope) and cannot be imported or
executed outside a live Streamlit+auth context — see app/state_sync.py for
where the actually-testable logic was extracted to. These tests instead
scan the page's SOURCE TEXT to enforce structural invariants that a live
Streamlit run can't easily be asserted against in this test suite:

  1. every acct_* session-state key the page directly subscripts
     (st.session_state["acct_X"]) or binds as a widget key= corresponds to
     a field in _ACCT_FIELDS (a typo'd new key would silently NOT be
     covered by sync_account_state_keys's unconditional backfill);
  2. sync_account_state_keys(...) is called before st.tabs(...) — i.e.
     before Positions/Risk Policy/Account State/Trade Plan/Decision History
     can possibly read an acct_* key, independent of tab visitation order;
  3. the failed-Positions-save exception handler never reassigns
     positions_editor_seed or pops the "positions_editor" widget key — the
     exact mechanism that produced the false "No positions recorded"
     message and the acct_open_positions KeyError when a save failed with
     six valid rows still in the editor.
"""
from __future__ import annotations

import re
from pathlib import Path

_SOURCE_PATH = Path(__file__).resolve().parent.parent / "app" / "pages" / "trade_planner.py"
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")

_ACCT_FIELDS = (
    "source", "net_liquidation_value", "cash", "buying_power", "realized_pnl_today", "unrealized_pnl",
    "daily_start_equity", "weekly_start_equity", "open_risk", "open_positions", "trades_today",
    "consecutive_losses", "long_exposure_notional", "short_exposure_notional",
)


def test_source_file_exists():
    assert _SOURCE_PATH.is_file()


def test_every_direct_acct_session_state_subscript_is_a_known_field():
    # Matches st.session_state["acct_X"] / st.session_state['acct_X'] and
    # key="acct_X" / key='acct_X' occurrences.
    found = set(re.findall(r'st\.session_state\[["\']acct_(\w+)["\']\]', _SOURCE))
    found |= set(re.findall(r'key=["\']acct_(\w+)["\']', _SOURCE))
    assert found, "expected to find at least one acct_* reference in trade_planner.py"
    unknown = found - set(_ACCT_FIELDS)
    assert not unknown, f"acct_* keys referenced in trade_planner.py but missing from _ACCT_FIELDS: {unknown}"


def test_sync_account_state_keys_runs_before_tabs_are_created():
    sync_call = _SOURCE.index("sync_account_state_keys(st.session_state, _ACCT_FIELDS)")
    tabs_call = _SOURCE.index("st.tabs(")
    assert sync_call < tabs_call, (
        "sync_account_state_keys must run before st.tabs(...) so every acct_* key is guaranteed to exist "
        "before ANY tab's body (Positions, Risk Policy, Account State, Trade Plan, Decision History) can "
        "possibly read one — independent of which tab the user visits first."
    )


def test_sync_account_state_keys_runs_before_positions_save_and_analyze():
    sync_call = _SOURCE.index("sync_account_state_keys(st.session_state, _ACCT_FIELDS)")
    save_button = _SOURCE.index('st.button("Save Positions"')
    analyze_button_marker = _SOURCE.index('analyze = st.button("Analyze"')
    assert sync_call < save_button
    assert sync_call < analyze_button_marker


def test_failed_positions_save_never_resets_the_live_editor():
    # Isolate the exception handler's body: from the "Failed to save
    # positions" log call through to the st.error(...) that reports the
    # failure to the user (the next occurrence of that exact call after it).
    fail_log_idx = _SOURCE.index('logger.exception("Failed to save positions for user %s", user.id)')
    error_call_idx = _SOURCE.index(
        '"Unable to save positions. No changes were confirmed. Your unsaved editor values are "', fail_log_idx,
    )
    handler_body = _SOURCE[fail_log_idx:error_call_idx]

    assert 'st.session_state["positions_editor_seed"] =' not in handler_body, (
        "a failed Save must never reassign positions_editor_seed — doing so replaces the user's unsaved, "
        "still-visible editor rows with the (possibly empty) reloaded DB state, which is exactly what "
        "produced the false 'No positions recorded' message despite six populated rows still on screen."
    )
    assert '.pop("positions_editor"' not in handler_body, (
        "a failed Save must never pop the data_editor's own widget key — doing so forces the grid to "
        "re-seed from positions_editor_seed on the next rerun, discarding the user's in-progress edits."
    )


def test_successful_positions_save_still_resets_the_editor_to_persisted_state():
    # The success path is intentionally different from the failure path —
    # confirm this test suite isn't accidentally asserting away that
    # legitimate behavior (§19: preserve Save/resave/delete semantics).
    save_flash_true_idx = _SOURCE.index('st.session_state["positions_save_flash"] = {"ok": True')
    preceding = _SOURCE[:save_flash_true_idx]
    last_seed_reassignment = preceding.rfind('st.session_state["positions_editor_seed"] =')
    last_pop = preceding.rfind('.pop("positions_editor"')
    assert last_seed_reassignment != -1
    assert last_pop != -1
    # Both must appear AFTER the successful upsert/delete calls and BEFORE the flash+rerun, i.e. reasonably
    # close to (not e.g. hundreds of lines before) the success flash — a loose but effective placement check.
    assert save_flash_true_idx - last_seed_reassignment < 500
    assert save_flash_true_idx - last_pop < 500


def test_positions_save_registers_every_symbol_before_upserting_positions():
    # account_positions.symbol has a foreign key to symbols(symbol) — every
    # symbol must be registered via md_repo.upsert_symbol BEFORE
    # pos_repo.upsert_positions is called, or a brand-new ticker typed
    # directly into the grid fails the whole batched save.
    upsert_symbol_idx = _SOURCE.index("md_repo.upsert_symbol(client, _sym, name=None, exchange=None)")
    upsert_positions_idx = _SOURCE.index("pos_repo.upsert_positions(client, user.id, strict_positions)")
    assert upsert_symbol_idx < upsert_positions_idx
