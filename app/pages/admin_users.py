import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from authorization import require_admin
from repository.admin_repository import list_users

user, role = require_admin()

st.title("Users")
st.caption(f"Signed in as {user.email} ({role})")

search = st.text_input("Search by email or display name", key="admin_users_search")

try:
    users = list_users(search=search or None)
except RuntimeError:
    st.error("Administrative backend is not configured.")
    st.stop()
except Exception:  # noqa: BLE001
    st.error("Unable to load users right now. Please try again.")
    st.stop()

st.caption(f"{len(users)} user(s)" + (f" matching '{search}'" if search else ""))

table_rows = [
    {
        "Display Name": u["display_name"] or "—",
        "Email": u["email"],
        "Role": u["role"],
        "Status": u["status"],
        "Created": u["created_at"],
        "Last Sign-In": u["last_sign_in_at"] or "Never",
        "Email Confirmed": "Yes" if u["email_confirmed_at"] else "No",
        "MFA": "Enabled" if u["mfa_enabled"] else "Not enabled",
    }
    for u in users
]
st.dataframe(table_rows, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Open a user's detail")
if users:
    options = {f'{u["email"]} ({u["role"]})': u["id"] for u in users}
    chosen_label = st.selectbox("Select a user", options=list(options.keys()))
    if st.button("View Details"):
        st.session_state["admin_selected_user_id"] = options[chosen_label]
        st.switch_page("pages/admin_user_detail.py")
else:
    st.write("No users match this search.")
