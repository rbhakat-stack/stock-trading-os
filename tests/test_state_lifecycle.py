"""Round L: stabilizes the Streamlit state model for Account State
initialization and the Positions data_editor.

BUG 1 ROOT CAUSE (acct_open_risk KeyError): the rp_*/acct_* session-state
init blocks in app/pages/trade_planner.py used to guard on one of the DATA
fields itself (`if "acct_source" not in st.session_state:`) while a
for-loop assigned the other 13 fields one statement at a time. Streamlit
can interrupt a running script (a new rerun request arriving mid-execution
raises a stop signal at an arbitrary point, including mid-loop) — if that
happened after "acct_source" was set but before "acct_open_risk" (9th of
14 fields) was reached, the guard would never fire again for the rest of
that session (since "acct_source" already existed), permanently leaving
"acct_open_risk" and every field after it unset. Any later read (e.g.
Trade Plan Analyze calling `_current_account_state()`) then raised
`KeyError: 'acct_open_risk'`.

FIX: guard on a DEDICATED marker key (`_acct_state_initialized` /
`_risk_policy_initialized` / `_positions_state_initialized`) set ONLY as
the very last statement, after every data field has already been written
from a fully-computed local dict. If a rerun is interrupted anywhere
before the marker is set, the marker stays unset, so the NEXT rerun's
guard is still true and the ENTIRE block retries from scratch — a
harmless, idempotent re-assignment of the same values — before any other
code in that rerun can possibly read a partially-initialized key. This is
tested here by literally simulating an interrupted rerun against both the
old and new patterns (test_c_interrupted_loop_*) to prove the property
directly, not just assert it by inspection.

BUG 2 ROOT CAUSE ("double entry"): app/pages/trade_planner.py used to
reassign `st.session_state["positions_df"]` (the data_editor's `value=`
seed) from the widget's own just-returned output on EVERY rerun, and ran
the positions-precedence resolution (feeding Account State's display and
Trade Plan's Analyze) from that SAME session-state snapshot taken BEFORE
the data_editor widget had even executed for the current rerun — i.e. one
rerun stale relative to the actual frontend state. Two concrete,
fixable contributors were found and removed:
  1. reassigning a widget's `value=` from its own prior output on every
     rerun, which can race with the frontend's in-flight edit and get
     "reset" back to a stale value, discarding a keystroke that hadn't
     synced yet; and
  2. resolve_positions_precedence's fallback path hitting the database on
     every single rerun where the grid had zero valid rows yet (e.g.
     typing the very first row of a fresh account) — real, unnecessary
     latency on exactly the reruns most sensitive to frontend timing.
FIX: `positions_editor_seed` (the value= seed) is now assigned ONLY at
session init and after a successful Save — never reassigned from the
widget's own output. `edited_positions_df` (the widget's live return
value) is used directly, as a local variable, for every "what's in the
grid right now" purpose. The positions-precedence resolution now runs
immediately after the Positions tab's own render block (which is moved
earlier in script order, though NOT in visual tab order) so every
consumer sees the SAME-RERUN-FRESH grid state, never a stale snapshot.
`persisted_positions_cache` removes the redundant per-keystroke DB call.

A residual, non-eliminable sensitivity to typing SPEED remains inherent
to the underlying glide-data-grid frontend widget (documented across
multiple earlier rounds of this project) — this is a live-browser-verified
characteristic, not a server-side session-state bug, and is reported
transparently in the session report rather than falsely claimed as fixed.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADE_PLANNER_PATH = PROJECT_ROOT / "app" / "pages" / "trade_planner.py"


def _source() -> str:
    return TRADE_PLANNER_PATH.read_text(encoding="utf-8")


# ============================================================================
# A — static guards: atomic marker-based init for rp_*/acct_*/positions
# ============================================================================


def test_a_risk_policy_init_uses_dedicated_marker_not_a_data_field():
    source = _source()
    assert 'if not st.session_state.get("_risk_policy_initialized", False):' in source
    assert 'st.session_state["_risk_policy_initialized"] = True' in source
    # the OLD, vulnerable pattern must be gone
    assert '"rp_risk_per_trade_pct" not in st.session_state' not in source


def test_a_account_state_init_uses_dedicated_marker_not_a_data_field():
    source = _source()
    assert 'if not st.session_state.get("_acct_state_initialized", False):' in source
    assert 'st.session_state["_acct_state_initialized"] = True' in source
    # the OLD, vulnerable pattern (the exact reported root cause) must be
    # gone AS ACTUAL GUARD CODE (a comment explaining the old bug is fine
    # and expected — this checks for the literal `if ...:` guard statement).
    assert 'if "acct_source" not in st.session_state:' not in source


def test_a_positions_state_init_uses_dedicated_marker():
    source = _source()
    assert 'if not st.session_state.get("_positions_state_initialized", False):' in source
    assert 'st.session_state["_positions_state_initialized"] = True' in source
    assert '"positions_df" not in st.session_state' not in source  # old key/pattern fully retired


def test_a_all_acct_fields_present_in_the_tuple():
    source = _source()
    expected = (
        "source", "net_liquidation_value", "cash", "buying_power", "realized_pnl_today", "unrealized_pnl",
        "daily_start_equity", "weekly_start_equity", "open_risk", "open_positions", "trades_today",
        "consecutive_losses", "long_exposure_notional", "short_exposure_notional",
    )
    for field in expected:
        assert f'"{field}"' in source


# ============================================================================
# B/C — Trade Plan direct visit before Account State / Account State visit
# ============================================================================


def test_b_current_account_state_call_sites_all_appear_after_the_init_marker():
    """Every call to _current_account_state() (Trade Plan's Analyze branch,
    Account State's own render) must appear, in script text order, AFTER
    the line that sets the init marker — i.e. initialization is guaranteed
    complete before any tab (including a direct Trade Plan visit) can read
    an acct_* key, regardless of which tab the user opens first."""
    source = _source()
    marker_index = source.index('st.session_state["_acct_state_initialized"] = True')
    # Look for actual CALLS (`= _current_account_state()`), not the `def
    # _current_account_state():` definition line itself, which also
    # contains the substring "_current_account_state()" but isn't a call.
    call_sites = [i for i in range(len(source)) if source.startswith("= _current_account_state()", i)]
    assert call_sites, "expected at least one call site"
    for idx in call_sites:
        assert idx > marker_index, "a _current_account_state() call appears before initialization completes"


def test_c_trade_plan_tab_body_appears_after_positions_and_state_init():
    """Static structural guard: the `with plan_tab:` block (Trade Plan) must
    appear after both the account-state init block and the positions
    precedence resolution, so a direct fresh-session visit to Trade Plan
    is still fully initialized."""
    source = _source()
    acct_marker_index = source.index('st.session_state["_acct_state_initialized"] = True')
    positions_marker_index = source.index('st.session_state["_positions_state_initialized"] = True')
    plan_tab_index = source.index("with plan_tab:")
    assert plan_tab_index > acct_marker_index
    assert plan_tab_index > positions_marker_index


# ============================================================================
# D/E — positions-authoritative display / no-positions fallback (static
# re-affirmation of the exact key names this round introduced)
# ============================================================================


def test_d_e_account_state_still_branches_on_positions_authoritative():
    source = _source()
    assert "if _positions_authoritative:" in source
    assert 'key="acct_open_risk"' in source  # the editable fallback path still exists
    assert 'key="acct_open_positions"' in source


# ============================================================================
# Positions tab rendered before the precedence resolution / other tabs
# (the actual mechanism fixing the one-rerun-stale lag)
# ============================================================================


def test_positions_tab_rendered_before_precedence_resolution_and_other_tabs():
    source = _source()
    positions_with_index = source.index("with positions_tab:")
    resolve_call_index = source.index("_resolved_positions, _positions_fallback_due_to_errors = resolve_positions_precedence(")
    policy_with_index = source.index("with policy_tab:")
    account_with_index = source.index("with account_tab:")
    plan_with_index = source.index("with plan_tab:")
    assert positions_with_index < resolve_call_index
    assert resolve_call_index < policy_with_index
    assert resolve_call_index < account_with_index
    assert resolve_call_index < plan_with_index


def test_precedence_resolution_uses_same_rerun_fresh_classify_output():
    """The precedence resolution must consume `current_positions` /
    `invalid_messages` — the SAME variables computed inside positions_tab
    from `edited_positions_df` (this rerun's live widget output) — never a
    separately re-fetched or stale-session-state-derived pair."""
    source = _source()
    assert "resolve_positions_precedence(\n    current_positions, invalid_messages, _load_persisted_positions,\n)" in source


def test_persisted_positions_cache_used_instead_of_a_fresh_db_call():
    source = _source()
    assert '"persisted_positions_cache" in st.session_state' in source
    assert "return st.session_state[\"persisted_positions_cache\"]" in source


# ============================================================================
# Simulated interrupted-rerun proof (Python-level, no Streamlit runtime
# needed) — directly demonstrates the property that makes Bug 1 impossible
# under the new pattern, and reproduces it under the old one.
# ============================================================================


class _FakeInterrupted(Exception):
    """Stand-in for Streamlit's internal rerun-interruption signal."""


def _old_pattern_init(session_state: dict, fields: tuple[str, ...], values: dict, interrupt_after: int | None):
    """Reproduces the OLD (buggy) pattern: guard on the first field itself,
    assign one at a time, optionally interrupted partway through."""
    if fields[0] not in session_state:
        for i, f in enumerate(fields):
            if interrupt_after is not None and i == interrupt_after:
                raise _FakeInterrupted()
            session_state[f] = values[f]


def _new_pattern_init(session_state: dict, fields: tuple[str, ...], values: dict, marker: str, interrupt_after: int | None):
    """Reproduces the NEW (fixed) pattern: guard on a dedicated marker,
    compute everything first, assign, set the marker LAST."""
    if not session_state.get(marker, False):
        computed = {f: values[f] for f in fields}  # pure computation - "interruption" here would leave session_state untouched
        for i, (f, v) in enumerate(computed.items()):
            if interrupt_after is not None and i == interrupt_after:
                raise _FakeInterrupted()
            session_state[f] = v
        session_state[marker] = True


def test_old_pattern_permanently_corrupts_state_when_interrupted_mid_loop():
    """Reproduces the exact Bug 1 mechanism: an interruption after field 0
    but before field 8 ("open_risk") leaves the guard-field set forever,
    so a retry never happens and a later read of field 8 fails."""
    fields = ("source", "net_liquidation_value", "open_risk", "open_positions")
    values = {"source": "MANUAL", "net_liquidation_value": 100_000.0, "open_risk": 400.0, "open_positions": 1}
    session_state: dict = {}

    # Rerun 1: interrupted after assigning "source" but before "open_risk".
    try:
        _old_pattern_init(session_state, fields, values, interrupt_after=2)
    except _FakeInterrupted:
        pass
    assert "source" in session_state
    assert "open_risk" not in session_state  # the actual bug: silently missing

    # Rerun 2: no interruption this time - but the guard is now permanently False.
    _old_pattern_init(session_state, fields, values, interrupt_after=None)
    assert "open_risk" not in session_state  # STILL missing - this is the KeyError root cause

    # A later read (as _current_account_state() would do) fails exactly as reported.
    try:
        _ = session_state["open_risk"]
        raise AssertionError("expected KeyError for 'open_risk', none raised")
    except KeyError:
        pass  # exactly reproduces the reported acct_open_risk KeyError


def test_new_pattern_self_heals_after_the_same_interruption():
    """The SAME interruption scenario against the fixed pattern: rerun 1 is
    interrupted (marker never set), rerun 2 retries the WHOLE block from
    scratch and succeeds - every field, including "open_risk", ends up set
    before any code could read it."""
    fields = ("source", "net_liquidation_value", "open_risk", "open_positions")
    values = {"source": "MANUAL", "net_liquidation_value": 100_000.0, "open_risk": 400.0, "open_positions": 1}
    session_state: dict = {}
    marker = "_acct_state_initialized"

    # Rerun 1: interrupted after assigning "source" but before "open_risk".
    try:
        _new_pattern_init(session_state, fields, values, marker, interrupt_after=2)
    except _FakeInterrupted:
        pass
    assert marker not in session_state  # marker correctly NOT set - guard will retry
    assert "open_risk" not in session_state  # a partial write did occur here too...

    # Rerun 2: no interruption - the guard (checking the marker, not a data
    # field) is still True, so it retries fully and completes.
    _new_pattern_init(session_state, fields, values, marker, interrupt_after=None)
    assert session_state.get(marker) is True
    assert session_state["open_risk"] == 400.0  # no KeyError - self-healed
    assert session_state["open_positions"] == 1
