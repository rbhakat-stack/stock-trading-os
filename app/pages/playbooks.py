"""Playbooks page (§20-22 of the Phase 4 report) — a read-for-everyone,
edit-for-SUPER_ADMIN-only inspection view over engine/playbooks/registry.py.

Never exposes raw Python expressions — every rule (prerequisite, trigger,
disqualifier, entry/stop/target/management rule) is already a plain-English
string on PlaybookDefinition, and that's exactly what's rendered here.

Enable/disable (§22) is metadata-only (repository/playbook_repository.py) —
it can never change what a playbook's rules mean, only whether the engine
evaluates it at all. Gracefully degrades to "showing code-defined defaults
only" if migration 0007 hasn't been applied yet (get_playbook_overrides
already handles that — see its docstring).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from auth import get_authed_client
from authorization import get_current_user_role, require_authenticated
from engine.playbooks.registry import all_definitions
from repository.playbook_repository import get_playbook_overrides, set_playbook_enabled

user = require_authenticated()
client = get_authed_client()
role = get_current_user_role(user.id)

st.title("Playbooks")
st.caption(
    "Every setup the engine can evaluate, as a formal, versioned playbook: prerequisites, trigger, "
    "disqualifiers, entry/stop/target logic, and quality components. This is the RULE definition — "
    "not a live signal for any specific symbol. See the Trade Planner's PRIMARY PLAYBOOK section for that."
)
if role != "SUPER_ADMIN":
    st.caption("You have view-only access. Only SUPER_ADMIN can enable or disable a playbook.")

try:
    overrides = get_playbook_overrides(client)
except Exception:  # noqa: BLE001 - a metadata-load failure must never block viewing the (code-defined) definitions
    overrides = {}

definitions = sorted(all_definitions(), key=lambda d: (not d.implementable, d.family, d.playbook_id))


def _effective_enabled(playbook_id: str, code_default: bool) -> bool:
    override = overrides.get(playbook_id)
    return override["enabled"] if override is not None else code_default


for d in definitions:
    effective_enabled = _effective_enabled(d.playbook_id, d.enabled)

    if not d.implementable:
        status_label = "NOT IMPLEMENTABLE YET"
    elif effective_enabled:
        status_label = "ENABLED"
    else:
        status_label = "DISABLED"

    header = f"{d.name} — {d.direction} · {d.family.replace('_', ' ')} · v{d.version} · {status_label}"
    with st.expander(header):
        st.write(f"**Playbook ID:** `{d.playbook_id}`")
        st.write(f"**Version:** {d.version}")
        st.write(f"**Family:** {d.family.replace('_', ' ')}")
        st.write(f"**Direction:** {d.direction}")
        st.write(f"**Supported timeframes:** {', '.join(d.supported_timeframes) or '—'}")
        st.write(f"**Supported regimes:** {', '.join(d.supported_regimes) or '—'}")
        st.write(f"**Statistical validation:** {d.statistical_validation_status}")

        if not d.implementable:
            st.warning(f"**NOT IMPLEMENTABLE YET:** {d.not_implementable_reason}")
            continue

        st.write(f"**Short description:** {d.short_description}")
        st.write(f"**What it is:** {d.plain_english_description}")
        st.write(f"**When it works:** {d.when_it_works}")
        st.write(f"**What can go wrong:** {d.what_can_go_wrong}")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**PREREQUISITES** (must exist before consideration)")
            for r in d.prerequisites:
                st.write(f"- {r}")
            st.markdown("**TRIGGER** (moves FORMING → TRIGGERED)")
            for r in d.trigger_rules:
                st.write(f"- {r}")
            st.markdown("**CONFIRMATION**")
            for r in d.confirmation_rules:
                st.write(f"- {r}")
            st.markdown("**DISQUALIFIERS** (hard rejection)")
            for r in d.disqualifiers:
                st.write(f"- {r}")
        with col2:
            st.markdown("**ENTRY**")
            st.caption(f"Entry type: {d.entry_type}")
            for r in d.entry_rules:
                st.write(f"- {r}")
            st.markdown("**STOP / INVALIDATION**")
            for r in d.stop_rules:
                st.write(f"- {r}")
            st.markdown("**TARGETS**")
            for r in d.target_rules:
                st.write(f"- {r}")
            st.markdown("**MANAGEMENT GUIDANCE**")
            for r in d.management_rules:
                st.write(f"- {r}")

        st.markdown("**QUALITY COMPONENTS**")
        st.write(", ".join(f"{c.label} ({c.max_points} pts)" for c in d.quality_components) or "—")

        ecol1, ecol2, ecol3 = st.columns(3)
        with ecol1:
            st.markdown("**Required evidence**")
            st.write(", ".join(d.required_evidence) or "—")
        with ecol2:
            st.markdown("**Optional evidence**")
            st.write(", ".join(d.optional_evidence) or "—")
        with ecol3:
            st.markdown("**Contra evidence**")
            st.write(", ".join(d.contra_evidence) or "—")

        st.caption(d.manual_review_notes)

        if role == "SUPER_ADMIN":
            new_enabled = st.checkbox(
                "Enabled", value=effective_enabled, key=f"pb_enabled_{d.playbook_id}",
                help="Disabling stops the engine from evaluating this playbook at all. Rule logic itself is never editable here.",
            )
            if new_enabled != effective_enabled:
                try:
                    set_playbook_enabled(client, d.playbook_id, new_enabled, user.id)
                    st.success(f"{d.name} set to {'enabled' if new_enabled else 'disabled'}.")
                    st.rerun()
                except Exception:  # noqa: BLE001
                    st.error("Unable to update this playbook right now. Please retry (migration 0007 may not be applied yet).")
