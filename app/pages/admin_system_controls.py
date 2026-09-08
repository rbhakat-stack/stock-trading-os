import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from auth import get_authed_client
from authorization import require_admin, require_super_admin
from repository.admin_repository import AdminActionError, set_feature_flag
from repository.feature_flags import get_feature_flags

user, role = require_admin()

st.title("System Controls")
st.caption(f"Signed in as {user.email} ({role})")
if role != "SUPER_ADMIN":
    st.caption("You have view-only access. Only SUPER_ADMIN can change these settings.")

try:
    flags = get_feature_flags(get_authed_client())
except Exception:  # noqa: BLE001
    st.error("Unable to load system settings right now. Please try again.")
    st.stop()


def _toggle(key: str, label: str, help_text: str, locked: bool = False) -> None:
    current = flags.get(key, False)
    if locked:
        st.checkbox(label, value=False, disabled=True, help=help_text + " — locked OFF, not implemented yet.")
        return

    disabled = role != "SUPER_ADMIN"
    new_value = st.checkbox(label, value=current, disabled=disabled, help=help_text, key=f"flag_{key}")
    if disabled or new_value == current:
        return

    try:
        _, fresh_role = require_super_admin()  # re-verify immediately before the write
        set_feature_flag(user.id, fresh_role, key, new_value)
        st.success(f"{label} set to {'enabled' if new_value else 'disabled'}.")
        st.rerun()
    except AdminActionError as e:
        st.error(str(e))
    except Exception:  # noqa: BLE001
        st.error("Unable to update this setting. Please try again.")


_toggle("maintenance_mode", "Maintenance Mode", "Blocks non-admin access to Dashboard and Market Reader.")
_toggle("paper_trading_enabled", "Paper Trading Enabled", "Platform-wide default for paper-trading eligibility.")
_toggle(
    "live_decision_support_enabled",
    "Live Decision Support Enabled",
    "Platform-wide default for live decision-support eligibility.",
)
_toggle(
    "live_execution_enabled",
    "Live Execution Enabled",
    "No broker execution exists yet in this codebase.",
    locked=True,
)
