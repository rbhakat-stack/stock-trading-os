"""All privileged administrative reads/writes, centralized.

Nothing in app/pages/admin_*.py talks to Supabase directly — every call goes
through here, using the service-role client (repository/supabase_admin_client.py).

This module does NOT perform authorization checks of its own accord for
*reads* (callers must have already passed authorization.require_admin() before
importing/calling anything here) — but every *mutating* function independently
re-verifies the caller's role against its own guard rules before writing
anything, per TRADING_OS_DESIGN.md §25's check-order and this task's explicit
defense-in-depth requirement. Never trust a role value passed in from a page
without also re-deriving the target's current state fresh from the database.

No Streamlit imports here (kept consistent with engine/ and the rest of
repository/): this module is plain Python or Streamlit-agnostic and could be
reused by a future background worker.
"""
from __future__ import annotations

from typing import Any

from supabase import Client

from repository.feature_flags import KNOWN_FLAGS
from repository.supabase_admin_client import new_admin_client

_ROLE_RANK = ("USER", "ADMIN", "SUPER_ADMIN")
_ROLE_NAME_TO_ID = {"SUPER_ADMIN": 1, "ADMIN": 2, "USER": 3}


class AdminActionError(Exception):
    """Raised when a privileged action is rejected by a server-side guard.
    The message is always safe to show directly to the (already-authorized)
    admin who triggered it — it never contains raw exception/internal detail."""


def _get_role_for_user(client: Client, user_id: str) -> str:
    res = client.table("user_roles").select("role_id, roles(name)").eq("user_id", user_id).execute()
    names = {row["roles"]["name"] for row in (res.data or []) if row.get("roles")}
    for candidate in reversed(_ROLE_RANK):
        if candidate in names:
            return candidate
    return "USER"


def _get_role_map(client: Client) -> dict[str, str]:
    res = client.table("user_roles").select("user_id, role_id, roles(name)").execute()
    by_user: dict[str, set[str]] = {}
    for row in res.data or []:
        name = (row.get("roles") or {}).get("name")
        if name:
            by_user.setdefault(row["user_id"], set()).add(name)
    role_map: dict[str, str] = {}
    for user_id, names in by_user.items():
        for candidate in reversed(_ROLE_RANK):
            if candidate in names:
                role_map[user_id] = candidate
                break
    return role_map


def _get_profile_map(client: Client) -> dict[str, dict]:
    res = client.table("profiles").select("id, display_name, status, timezone, base_currency, created_at").execute()
    return {row["id"]: row for row in (res.data or [])}


def _write_audit_event(
    client: Client,
    *,
    admin_user_id: str,
    action: str,
    target_user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    reason: str | None = None,
) -> None:
    client.table("admin_audit_events").insert(
        {
            "admin_user_id": admin_user_id,
            "target_user_id": target_user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "before_state": before_state,
            "after_state": after_state,
            "reason": reason,
        }
    ).execute()


# ===================== Reads =====================


def list_users(search: str | None = None, page: int = 1, per_page: int = 1000) -> list[dict[str, Any]]:
    client = new_admin_client()
    users = client.auth.admin.list_users(page=page, per_page=per_page)
    role_map = _get_role_map(client)
    profile_map = _get_profile_map(client)

    records = []
    for u in users:
        profile = profile_map.get(u.id, {})
        records.append(
            {
                "id": u.id,
                "email": u.email,
                "display_name": profile.get("display_name"),
                "role": role_map.get(u.id, "USER"),
                "status": profile.get("status", "ACTIVE"),
                "created_at": u.created_at,
                "last_sign_in_at": u.last_sign_in_at,
                "email_confirmed_at": u.email_confirmed_at,
                "mfa_enabled": bool(u.factors),
            }
        )

    if search:
        needle = search.strip().lower()
        records = [
            r
            for r in records
            if needle in (r["email"] or "").lower() or needle in (r["display_name"] or "").lower()
        ]
    return records


def get_user_detail(target_user_id: str) -> dict[str, Any]:
    client = new_admin_client()
    user_res = client.auth.admin.get_user_by_id(target_user_id)
    u = user_res.user

    profile_res = client.table("profiles").select("*").eq("id", target_user_id).single().execute()
    profile = profile_res.data or {}

    return {
        "id": u.id,
        "email": u.email,
        "display_name": profile.get("display_name"),
        "role": _get_role_for_user(client, target_user_id),
        "status": profile.get("status", "ACTIVE"),
        "created_at": u.created_at,
        "last_sign_in_at": u.last_sign_in_at,
        "email_confirmed_at": u.email_confirmed_at,
        "mfa_enabled": bool(u.factors),
        "timezone": profile.get("timezone"),
        "base_currency": profile.get("base_currency"),
    }


def get_dashboard_metrics() -> dict[str, Any]:
    client = new_admin_client()
    users = client.auth.admin.list_users(page=1, per_page=1000)
    role_map = _get_role_map(client)
    profile_map = _get_profile_map(client)

    total = len(users)
    suspended = sum(1 for u in users if profile_map.get(u.id, {}).get("status") == "SUSPENDED")

    recent_users = sorted(users, key=lambda u: u.created_at, reverse=True)[:10]
    recent_signins = sorted(
        (u for u in users if u.last_sign_in_at), key=lambda u: u.last_sign_in_at, reverse=True
    )[:10]

    return {
        "total_users": total,
        "active_users": total - suspended,
        "suspended_users": suspended,
        "admin_count": sum(1 for r in role_map.values() if r == "ADMIN"),
        "super_admin_count": sum(1 for r in role_map.values() if r == "SUPER_ADMIN"),
        "recent_users": [{"email": u.email, "created_at": u.created_at} for u in recent_users],
        "recent_signins": [{"email": u.email, "last_sign_in_at": u.last_sign_in_at} for u in recent_signins],
    }


def list_audit_events(admin_role: str, limit: int = 200) -> list[dict[str, Any]]:
    if admin_role != "SUPER_ADMIN":
        raise AdminActionError("Only SUPER_ADMIN can view the audit log.")
    client = new_admin_client()
    res = client.table("admin_audit_events").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data or []


def record_admin_view_user(admin_user_id: str, target_user_id: str) -> None:
    client = new_admin_client()
    _write_audit_event(
        client,
        admin_user_id=admin_user_id,
        action="ADMIN_ACCESS_USER_DETAILS",
        target_user_id=target_user_id,
        resource_type="auth.users",
        resource_id=target_user_id,
    )


# ===================== Writes (SUPER_ADMIN only — see module docstring) =====================


def change_user_role(
    admin_user_id: str, admin_role: str, target_user_id: str, new_role_name: str, reason: str | None = None
) -> None:
    if admin_role != "SUPER_ADMIN":
        raise AdminActionError("Only SUPER_ADMIN can change roles.")
    if new_role_name not in ("USER", "ADMIN"):
        # Blocks ADMIN -> SUPER_ADMIN / USER -> SUPER_ADMIN unconditionally, not just in the UI.
        raise AdminActionError("Role changes through this console are limited to USER and ADMIN.")
    if target_user_id == admin_user_id:
        raise AdminActionError("You cannot change your own role.")

    client = new_admin_client()
    current_role = _get_role_for_user(client, target_user_id)
    if current_role == "SUPER_ADMIN":
        raise AdminActionError("SUPER_ADMIN accounts cannot be modified through the Admin Console.")
    if current_role == new_role_name:
        raise AdminActionError(f"User already has role {new_role_name}.")

    # A role change replaces the row rather than accumulating multiple role rows
    # per user — the rest of the system assumes exactly one current role.
    client.table("user_roles").delete().eq("user_id", target_user_id).execute()
    client.table("user_roles").insert(
        {"user_id": target_user_id, "role_id": _ROLE_NAME_TO_ID[new_role_name], "granted_by": admin_user_id}
    ).execute()

    _write_audit_event(
        client,
        admin_user_id=admin_user_id,
        action="ROLE_CHANGED",
        target_user_id=target_user_id,
        resource_type="user_roles",
        before_state={"role": current_role},
        after_state={"role": new_role_name},
        reason=reason,
    )


def set_user_status(
    admin_user_id: str, admin_role: str, target_user_id: str, new_status: str, reason: str | None = None
) -> None:
    if admin_role != "SUPER_ADMIN":
        raise AdminActionError("Only SUPER_ADMIN can suspend or reactivate accounts.")
    if new_status not in ("ACTIVE", "SUSPENDED"):
        raise AdminActionError("Invalid status.")
    if target_user_id == admin_user_id:
        raise AdminActionError("You cannot change your own account status.")

    client = new_admin_client()
    current_role = _get_role_for_user(client, target_user_id)
    if current_role == "SUPER_ADMIN":
        raise AdminActionError("SUPER_ADMIN accounts cannot be modified through the Admin Console.")

    current = client.table("profiles").select("status").eq("id", target_user_id).single().execute()
    before_status = (current.data or {}).get("status", "ACTIVE")
    if before_status == new_status:
        raise AdminActionError(f"User already has status {new_status}.")

    client.table("profiles").update({"status": new_status}).eq("id", target_user_id).execute()

    action = "USER_SUSPENDED" if new_status == "SUSPENDED" else "USER_REACTIVATED"
    _write_audit_event(
        client,
        admin_user_id=admin_user_id,
        action=action,
        target_user_id=target_user_id,
        resource_type="profiles",
        before_state={"status": before_status},
        after_state={"status": new_status},
        reason=reason,
    )


def set_feature_flag(
    admin_user_id: str, admin_role: str, key: str, enabled: bool, reason: str | None = None
) -> None:
    if admin_role != "SUPER_ADMIN":
        raise AdminActionError("Only SUPER_ADMIN can change platform-wide settings.")
    if key == "live_execution_enabled":
        # Hard-locked regardless of caller/role — live trading has no implementation
        # yet to gate; see TRADING_OS_DESIGN.md §22.
        raise AdminActionError("Live execution is not implemented yet and cannot be enabled through the console.")
    if key not in KNOWN_FLAGS:
        raise AdminActionError("Unknown feature flag.")

    client = new_admin_client()
    current = client.table("feature_flags").select("enabled").eq("key", key).single().execute()
    before = bool((current.data or {}).get("enabled", False))
    if before == enabled:
        raise AdminActionError(f"{key} is already {'enabled' if enabled else 'disabled'}.")

    client.table("feature_flags").update({"enabled": enabled, "updated_by": admin_user_id}).eq("key", key).execute()

    _write_audit_event(
        client,
        admin_user_id=admin_user_id,
        action="FEATURE_FLAG_CHANGED",
        resource_type="feature_flags",
        resource_id=key,
        before_state={"enabled": before},
        after_state={"enabled": enabled},
        reason=reason,
    )
