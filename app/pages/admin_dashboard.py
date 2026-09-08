import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from authorization import require_admin
from repository.admin_repository import get_dashboard_metrics

user, role = require_admin()

st.title("Admin Dashboard")
st.caption(f"Signed in as {user.email} ({role})")

try:
    metrics = get_dashboard_metrics()
except RuntimeError:
    st.error("Administrative backend is not configured.")
    st.stop()
except Exception:  # noqa: BLE001
    st.error("Unable to load admin metrics right now. Please try again.")
    st.stop()

col1, col2, col3 = st.columns(3)
col1.metric("Total Users", metrics["total_users"])
col2.metric("Active Users", metrics["active_users"])
col3.metric("Suspended Users", metrics["suspended_users"])

col4, col5, col6 = st.columns(3)
col4.metric("ADMIN Count", metrics["admin_count"])
col5.metric("SUPER_ADMIN Count", metrics["super_admin_count"])
col6.metric("Paper Trading Users", "Not implemented")
st.caption("Paper Trading Users / Live Trading Eligible Users: per-user eligibility isn't modeled yet — "
           "only platform-wide flags exist so far (see System Controls).")

st.divider()

left, right = st.columns(2)
with left:
    st.subheader("Recently Created Users")
    if metrics["recent_users"]:
        st.dataframe(metrics["recent_users"], use_container_width=True, hide_index=True)
    else:
        st.write("No users yet.")
with right:
    st.subheader("Recent Sign-In Activity")
    if metrics["recent_signins"]:
        st.dataframe(metrics["recent_signins"], use_container_width=True, hide_index=True)
    else:
        st.write("Not implemented")

st.divider()
st.subheader("System Status")
st.write("Supabase (user client): configured")
st.write("Administrative backend (service-role): configured")
st.write("Market data provider: see Market Reader page")
