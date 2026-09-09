"""Pure session-state synchronization helpers — no Streamlit/auth imports,
so these are directly unit-testable with a plain dict standing in for
st.session_state (which satisfies the same __contains__/__getitem__/
__setitem__ protocol used here).

WHY THIS MODULE EXISTS (Phase 3 acct_open_positions KeyError regression):
Streamlit automatically removes a widget's session-state entry whenever
that widget is NOT instantiated during a script run (this is documented,
intentional Streamlit behavior — it prevents stale widget state from a
removed widget lingering forever). app/pages/trade_planner.py's Account
State tab uses several acct_* keys as BOTH (a) the canonical "current raw
manual account value" needed by _current_account_state()/effective account
math on every rerun, AND (b) the key= of a st.number_input that is only
instantiated when positions are NOT authoritative (the "manual fallback"
branch — see the Round J disabled-mirror-widget architecture). The moment
positions ARE authoritative for even one rerun, that widget doesn't render,
and Streamlit garbage-collects its key at the end of that run. The prior
"atomic init" fix (Round L) only guarantees a key gets populated ONCE per
session (gated by a one-time marker) — it does nothing to restore a key
Streamlit later removes via its own widget lifecycle. That's the exact
mechanism behind "we fixed acct_open_risk missing, now acct_open_positions
is missing" — a structurally identical flaw recurring on a sibling field,
not a regression of the original fix.

The fix: sync_account_state_keys must run UNCONDITIONALLY on every single
rerun (not gated by a one-time marker), before any other code reads an
acct_* key, so a key Streamlit removed on the PREVIOUS run is always
restored before this run's code can observe it missing. It never defaults
to zero — it restores the last known real value (from the persisted/default
snapshot, or from whatever the user most recently typed into the widget
when it was live), so risk figures are never silently understated.
"""
from __future__ import annotations


def sync_account_state_keys(state, fields: tuple[str, ...], snapshot_key: str = "_acct_raw_snapshot") -> None:
    """The single unconditional backfill step. `state` must already contain
    `snapshot_key` (a dict of field -> last-known-value, seeded once from
    the persisted/default AccountRiskState at session init — see
    app/pages/trade_planner.py's _ensure_account_state_initialized).

    For every field: if its acct_<field> key currently exists in `state`
    (the widget rendered this run, or a prior run's value survived), the
    snapshot is refreshed from it — so a later restore uses the freshest
    known value, never a stale original default. If the key is ABSENT
    (Streamlit garbage-collected it because its widget didn't render last
    run), it is restored from the snapshot. After this call, every
    acct_<field> key in `fields` is guaranteed present in `state` — this is
    the testable contract every direct st.session_state["acct_*"] access in
    trade_planner.py relies on (see tests/test_account_state_key_sync.py
    and tests/test_trade_planner_state_contract.py).
    """
    snapshot = state[snapshot_key]
    for field in fields:
        key = f"acct_{field}"
        if key in state:
            snapshot[field] = state[key]
        else:
            state[key] = snapshot[field]
