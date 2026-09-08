import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from authorization import require_super_admin
from repository.admin_repository import AdminActionError, list_audit_events

user, role = require_super_admin()

st.title("Audit Log")
st.caption(f"Signed in as {user.email} ({role})")
st.caption(
    "SUPER_ADMIN only. ADMIN has no audit-log access in this phase — the safer default, since "
    "audit records exist specifically to keep administrative action independently reviewable."
)

try:
    events = list_audit_events(role, limit=200)
except AdminActionError as e:
    st.error(str(e))
    st.stop()
except RuntimeError:
    st.error("Administrative backend is not configured.")
    st.stop()
except Exception:  # noqa: BLE001
    st.error("Unable to load the audit log right now. Please try again.")
    st.stop()

if not events:
    st.info("No administrative actions recorded yet.")
else:
    rows = [
        {
            "Time": e["created_at"],
            "Admin": e["admin_user_id"],
            "Action": e["action"],
            "Target User": e.get("target_user_id") or "—",
            "Resource": e.get("resource_type") or "—",
            "Before": e.get("before_state"),
            "After": e.get("after_state"),
            "Reason": e.get("reason") or "—",
        }
        for e in events
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
