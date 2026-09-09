"""Regression tests for the Phase 3 acct_open_positions KeyError — see
app/state_sync.py's module docstring for the full root-cause mechanism:
Streamlit silently removes a widget's session-state key whenever that
widget isn't instantiated on a given rerun, and the prior one-time "atomic
init" guard (Round L) cannot detect or repair that removal.

A plain dict stands in for st.session_state here — it satisfies the same
__contains__/__getitem__/__setitem__ protocol sync_account_state_keys uses,
so this exercises the EXACT mechanism without needing a live Streamlit run.
"""
from app.state_sync import sync_account_state_keys

_FIELDS = ("open_positions", "open_risk", "long_exposure_notional", "short_exposure_notional", "net_liquidation_value")


def _snapshot(**overrides):
    base = {
        "open_positions": 0, "open_risk": 0.0, "long_exposure_notional": 0.0,
        "short_exposure_notional": 0.0, "net_liquidation_value": 100_000.0,
    }
    base.update(overrides)
    return base


def test_first_call_populates_every_key_from_snapshot():
    state = {"_acct_raw_snapshot": _snapshot(open_positions=3)}
    sync_account_state_keys(state, _FIELDS)
    assert state["acct_open_positions"] == 3
    assert all(f"acct_{f}" in state for f in _FIELDS)


def test_restores_a_key_streamlit_garbage_collected():
    # Simulates the exact reported crash: acct_open_positions existed on a
    # prior run (positions were authoritative, so its widget didn't render
    # and Streamlit removed the key at the end of that run) — the very next
    # call must restore it, not leave it missing.
    state = {"_acct_raw_snapshot": _snapshot(open_positions=6)}
    sync_account_state_keys(state, _FIELDS)
    del state["acct_open_positions"]  # Streamlit's own widget-key GC
    assert "acct_open_positions" not in state
    sync_account_state_keys(state, _FIELDS)
    assert state["acct_open_positions"] == 6  # restored from the snapshot, never defaulted to 0


def test_restores_multiple_missing_keys_simultaneously():
    # acct_open_risk, acct_long_exposure_notional, acct_short_exposure_notional
    # all share the identical conditional-widget-key flaw — must all recover.
    state = {"_acct_raw_snapshot": _snapshot(open_risk=2400.0, long_exposure_notional=153_000.0, short_exposure_notional=0.0)}
    sync_account_state_keys(state, _FIELDS)
    for f in ("open_risk", "long_exposure_notional", "short_exposure_notional"):
        del state[f"acct_{f}"]
    sync_account_state_keys(state, _FIELDS)
    assert state["acct_open_risk"] == 2400.0
    assert state["acct_long_exposure_notional"] == 153_000.0
    assert state["acct_short_exposure_notional"] == 0.0


def test_never_defaults_a_missing_key_to_zero():
    # §4 of the report: a missing key must restore the last KNOWN real
    # value, never silently substitute 0 (which could understate risk).
    state = {"_acct_raw_snapshot": _snapshot(open_positions=6, open_risk=3400.0)}
    sync_account_state_keys(state, _FIELDS)
    del state["acct_open_positions"]
    del state["acct_open_risk"]
    sync_account_state_keys(state, _FIELDS)
    assert state["acct_open_positions"] == 6
    assert state["acct_open_risk"] == 3400.0


def test_live_widget_edit_updates_the_snapshot_before_a_later_gc_cycle():
    # If the widget WAS rendered (key present) and the user changed it, the
    # snapshot must track that new value — so a LATER gc-then-restore cycle
    # recovers the user's latest edit, not a stale original default.
    state = {"_acct_raw_snapshot": _snapshot(open_positions=0)}
    sync_account_state_keys(state, _FIELDS)
    state["acct_open_positions"] = 4  # user typed a new manual fallback value
    sync_account_state_keys(state, _FIELDS)  # widget key still present -> snapshot refreshes from it
    del state["acct_open_positions"]  # positions later become authoritative -> Streamlit GC's the key
    sync_account_state_keys(state, _FIELDS)
    assert state["acct_open_positions"] == 4  # NOT the stale original 0


def test_idempotent_no_op_when_everything_already_present():
    state = {"_acct_raw_snapshot": _snapshot(open_positions=6)}
    sync_account_state_keys(state, _FIELDS)
    before = dict(state)
    sync_account_state_keys(state, _FIELDS)
    assert state == before


def test_contract_every_field_key_present_after_call():
    state = {"_acct_raw_snapshot": _snapshot()}
    sync_account_state_keys(state, _FIELDS)
    assert all(f"acct_{f}" in state for f in _FIELDS)
