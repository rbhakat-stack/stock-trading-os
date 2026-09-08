import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from authorization import require_admin, require_super_admin
from repository.admin_repository import (
    AdminActionError,
    change_user_role,
    get_user_detail,
    record_admin_view_user,
    set_user_status,
)

user, role = require_admin()

st.title("User Detail")

target_user_id = st.session_state.get("admin_selected_user_id")
if not target_user_id:
    st.info("No user selected.")
    st.page_link("pages/admin_users.py", label="Go to Users →", icon=":material/group:")
    st.stop()

try:
    detail = get_user_detail(target_user_id)
except RuntimeError:
    st.error("Administrative backend is not configured.")
    st.stop()
except Exception:  # noqa: BLE001
    st.error("User could not be found.")
    st.stop()

# Record this admin's access to the target's detail screen once per session per
# target, not on every widget-triggered rerun of this same page view (§19).
viewed_key = "admin_audited_user_views"
st.session_state.setdefault(viewed_key, set())
if target_user_id not in st.session_state[viewed_key]:
    try:
        record_admin_view_user(user.id, target_user_id)
        st.session_state[viewed_key].add(target_user_id)
    except Exception:  # noqa: BLE001
        pass  # never block viewing a user because the audit write failed

st.caption(f"Signed in as {user.email} ({role})")

col1, col2 = st.columns(2)
with col1:
    st.write("**User ID**", detail["id"])
    st.write("**Display Name**", detail["display_name"] or "—")
    st.write("**Email**", detail["email"])
    st.write("**Role**", detail["role"])
    st.write("**Application Status**", detail["status"])
with col2:
    st.write("**Created**", detail["created_at"])
    st.write("**Last Sign-In**", detail["last_sign_in_at"] or "Never")
    st.write("**Email Confirmed**", "Yes" if detail["email_confirmed_at"] else "No")
    st.write("**MFA**", "Enabled" if detail["mfa_enabled"] else "Not enabled")
    st.write("**Timezone**", detail["timezone"] or "—")
    st.write("**Base Currency**", detail["base_currency"] or "—")

st.caption(
    "Portfolio Count, Broker Connections, Paper/Live Trading State, Risk Policy: Not implemented yet "
    "(these arrive with later phases)."
)

if detail["role"] == "SUPER_ADMIN":
    st.divider()
    st.info("This account is SUPER_ADMIN. It cannot be modified through the Admin Console.")
    st.stop()

st.divider()
st.subheader("Role")

if role != "SUPER_ADMIN":
    st.caption("Only SUPER_ADMIN can change roles.")
else:
    other_role = "ADMIN" if detail["role"] == "USER" else "USER"
    with st.form("role_change_form"):
        st.write(f"Current Role: **{detail['role']}**")
        new_role = st.selectbox("New Role", options=[detail["role"], other_role], index=1)
        reason = st.text_input("Reason (optional)", key="role_change_reason")
        confirmed = st.checkbox(f"I confirm changing this user's role to {new_role}.")
        submitted = st.form_submit_button("Confirm Role Change")
    if submitted:
        if not confirmed:
            st.error("Check the confirmation box to proceed.")
        elif target_user_id == user.id:
            st.error("You cannot change your own role.")
        else:
            try:
                _, fresh_role = require_super_admin()  # re-verify immediately before the write
                change_user_role(user.id, fresh_role, target_user_id, new_role, reason or None)
                st.success(f"Role changed to {new_role}.")
                st.rerun()
            except AdminActionError as e:
                st.error(str(e))
            except Exception:  # noqa: BLE001
                st.error("Unable to update role. Please try again.")

st.divider()
st.subheader("Application Access")

if role != "SUPER_ADMIN":
    st.caption("Only SUPER_ADMIN can suspend or reactivate accounts.")
else:
    target_status = "ACTIVE" if detail["status"] == "SUSPENDED" else "SUSPENDED"
    action_label = "Reactivate User" if target_status == "ACTIVE" else "Suspend User"
    with st.form("status_change_form"):
        st.write(f"Current Status: **{detail['status']}**")
        reason2 = st.text_input("Reason", key="status_change_reason")
        confirmed2 = st.checkbox(f"I confirm setting this user's status to {target_status}.")
        submitted2 = st.form_submit_button(action_label)
    if submitted2:
        if not confirmed2:
            st.error("Check the confirmation box to proceed.")
        elif target_status == "SUSPENDED" and not reason2.strip():
            st.error("A reason is required to suspend a user.")
        elif target_user_id == user.id:
            st.error("You cannot change your own account status.")
        else:
            try:
                _, fresh_role = require_super_admin()
                set_user_status(user.id, fresh_role, target_user_id, target_status, reason2 or None)
                st.success(f"Status changed to {target_status}.")
                st.rerun()
            except AdminActionError as e:
                st.error(str(e))
            except Exception:  # noqa: BLE001
                st.error("Unable to update status. Please try again.")

st.divider()
st.subheader("Sessions")
st.caption(
    "Session revocation not implemented: the installed Supabase Python client's admin API "
    "(supabase-py 2.31 / supabase_auth's SyncGoTrueAdminAPI) exposes sign_out(jwt, scope), which "
    "requires the session's own access token — there is no supported method to invalidate an "
    "arbitrary user's existing sessions by user ID alone in this version. Suspending the account "
    "above blocks further application access immediately; it does not retroactively invalidate an "
    "already-issued, still-valid Supabase JWT until it naturally expires."
)
