"""Centralized, server-side authorization checks.

Every privileged page or action must call one of the require_* functions here
— never re-implement a role/status check inline in a page. Role and account
status always come from a fresh Supabase query (never trusted from
st.session_state or from anything a frontend widget claims), because hiding a
navigation item is a UX nicety, not a security boundary: a user who types an
admin URL directly must hit the same check a page render would have hit.

Fail-closed policy: if we cannot positively confirm a role or an ACTIVE status
(network error, missing row, unexpected response shape), access is denied —
never assumed permitted. Maintenance-mode is the one deliberate exception,
since failing that closed would turn a transient read error into a full
platform outage for everyone, including the admins who'd need to fix it.
"""
from __future__ import annotations

import logging

import streamlit as st

from auth import get_authed_client, require_login
from repository.feature_flags import is_maintenance_mode_active

logger = logging.getLogger("trading_os.authorization")

ROLE_RANK = ("USER", "ADMIN", "SUPER_ADMIN")  # low -> high privilege


def get_current_user_role(user_id: str) -> str | None:
    """Fresh lookup — never cached — via the caller's own RLS-scoped session
    (existing policy already lets a user read their own user_roles rows)."""
    try:
        client = get_authed_client()
        res = client.table("user_roles").select("role_id, roles(name)").eq("user_id", user_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Role lookup failed for %s: %s", user_id, exc)
        return None

    rows = res.data or []
    names = {row["roles"]["name"] for row in rows if row.get("roles")}
    for candidate in reversed(ROLE_RANK):
        if candidate in names:
            return candidate
    return None


def get_current_account_status(user_id: str) -> str | None:
    """Returns 'ACTIVE' / 'SUSPENDED', or None if the status could not be
    verified — callers must treat None as "deny", never as "assume ACTIVE"."""
    try:
        client = get_authed_client()
        res = client.table("profiles").select("status").eq("id", user_id).single().execute()
        return res.data.get("status")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Status lookup failed for %s: %s", user_id, exc)
        return None


def _maintenance_mode_active() -> bool:
    try:
        client = get_authed_client()
        return is_maintenance_mode_active(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Maintenance-mode lookup failed, assuming inactive: %s", exc)
        return False


def has_role(user_id: str, *allowed_roles: str) -> bool:
    return get_current_user_role(user_id) in allowed_roles


def require_authenticated():
    """Stronger than auth.require_login(): also enforces account suspension and
    maintenance mode. Every protected workspace page should call this, not the
    bare login check — see TRADING_OS_DESIGN.md §15."""
    user = require_login()

    status = get_current_account_status(user.id)
    if status != "ACTIVE":
        logger.info("Blocked access for %s: account status = %r", user.id, status)
        st.error("Your application access is currently suspended.")
        st.stop()

    if _maintenance_mode_active():
        role = get_current_user_role(user.id)
        if role not in ("ADMIN", "SUPER_ADMIN"):
            st.warning("The application is currently in maintenance mode. Please check back shortly.")
            st.stop()

    return user


def require_admin():
    """Returns (user, role). Denies USER and anyone whose role can't be verified."""
    user = require_authenticated()
    role = get_current_user_role(user.id)
    if role not in ("ADMIN", "SUPER_ADMIN"):
        st.error("Access denied. This page requires administrator privileges.")
        st.stop()
    return user, role


def require_super_admin():
    """Returns (user, role). Only SUPER_ADMIN continues."""
    user = require_authenticated()
    role = get_current_user_role(user.id)
    if role != "SUPER_ADMIN":
        st.error("Access denied. This page requires SUPER_ADMIN privileges.")
        st.stop()
    return user, role
