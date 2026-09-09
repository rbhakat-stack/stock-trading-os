from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Trading OS", layout="wide", page_icon=":material/candlestick_chart:")

# IMPORTANT: st.navigation(...) must always be reached on every run, even when
# config is missing. Streamlit auto-discovers app/pages/*.py as a classic
# multipage sidebar unless st.navigation() explicitly overrides it — an early
# st.stop() before that call would leave the classic sidebar active, letting a
# user click straight into Market Reader/Dashboard and hit a raw crash instead
# of the setup message below.
from config import get_app_url  # app/ is already on sys.path — this is the entry script's own directory

config_ok = bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_ANON_KEY"))
if config_ok:
    try:
        get_app_url()
    except RuntimeError:
        config_ok = False

if "user" not in st.session_state:
    st.session_state["user"] = None

setup_page = st.Page("pages/setup_required.py", title="Setup Required", icon=":material/settings:")
account_page = st.Page("pages/account.py", title="Account", icon=":material/person:")
dashboard_page = st.Page("pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True)
market_reader_page = st.Page("pages/market_reader.py", title="Market Reader", icon=":material/candlestick_chart:")
trade_planner_page = st.Page("pages/trade_planner.py", title="Trade Planner", icon=":material/rule:")
admin_dashboard_page = st.Page("pages/admin_dashboard.py", title="Admin Dashboard", icon=":material/shield_person:")
admin_users_page = st.Page("pages/admin_users.py", title="Users", icon=":material/group:")
admin_user_detail_page = st.Page("pages/admin_user_detail.py", title="User Detail", icon=":material/person_search:")
admin_audit_log_page = st.Page("pages/admin_audit_log.py", title="Audit Log", icon=":material/history:")
admin_system_controls_page = st.Page(
    "pages/admin_system_controls.py", title="System Controls", icon=":material/tune:"
)

if not config_ok:
    nav = st.navigation([setup_page])
elif st.session_state.get("user"):
    pages = {"Account": [account_page], "Workspace": [dashboard_page, market_reader_page, trade_planner_page]}

    # Nav visibility is UX only — every admin page independently re-checks
    # authorization itself (see app/authorization.py), so a mistake or staleness
    # here can only ever hide/show a menu item, never grant real access.
    try:
        from authorization import get_current_user_role

        role = get_current_user_role(st.session_state["user"].id)
    except Exception:  # noqa: BLE001 - nav visibility must never crash the whole app
        role = None

    if role in ("ADMIN", "SUPER_ADMIN"):
        pages["Administration"] = [
            admin_dashboard_page,
            admin_users_page,
            admin_user_detail_page,
            admin_audit_log_page,
            admin_system_controls_page,
        ]

    nav = st.navigation(pages)
else:
    nav = st.navigation([account_page])

nav.run()
