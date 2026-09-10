"""Phase 4 playbook framework — formal, versioned, machine-readable setup
definitions layered ON TOP OF the existing Phase 2 market intelligence and
Phase 3 risk/trade planner. See engine/playbooks/engine.py for the entry
point (`evaluate_all_playbooks`).

ARCHITECTURAL BOUNDARY (do not blur this):
  - engine/playbooks/  answers "what opportunities exist and how strong are
    they structurally?" — it NEVER touches risk policy, account state,
    position sizing, or the kill switch.
  - engine/risk/ + engine/trade/decision.py remain the SOLE authority for
    "can the account safely take this trade, and at what size?" — nothing
    here duplicates or bypasses that.

Phase 3 compatibility: this package is purely ADDITIVE. engine/trade/setups.py,
engine/trade/candidate.py, engine/trade/decision.py, and engine/trade/planner.py
are UNCHANGED — the six existing setups' evaluators in this package call
those exact same matcher functions internally, so the underlying
TradeCandidate objects (and therefore every existing Phase 3 test) are
byte-for-byte unaffected. See tests/test_playbook_backward_compat.py.
"""
