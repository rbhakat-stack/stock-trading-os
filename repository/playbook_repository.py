"""Repository functions for Phase 4 playbook metadata persistence (§23) —
supabase/migrations/0007_phase4_playbooks.sql. Uses the caller's
authenticated (RLS-scoped) client, same as every other Phase 3/4 table.

Gracefully degrades if migration 0007 has not been applied yet (the
`playbook_configs` table doesn't exist): callers get an empty override map
back rather than a crash, and engine/playbooks/registry.py's code-defined
`enabled=True` defaults remain authoritative — see
app/pages/trade_planner.py / app/pages/playbooks.py for how this is used.
"""
from __future__ import annotations

import logging

from supabase import Client

logger = logging.getLogger("trading_os.playbook_repository")


def get_playbook_overrides(client: Client) -> dict[str, dict]:
    """Returns {playbook_id: {"enabled": bool, "priority": int}} for every
    row currently persisted. Empty dict (not an exception) if the table
    doesn't exist yet or the read fails for any reason — callers must treat
    a missing override as "use the code-defined default," never as
    "disabled.\""""
    try:
        res = client.table("playbook_configs").select("playbook_id,enabled,priority").execute()
    except Exception:  # noqa: BLE001 - migration 0007 may not be applied yet; never block the page
        logger.exception("Failed to load playbook_configs overrides (migration 0007 may not be applied yet)")
        return {}
    return {row["playbook_id"]: {"enabled": row["enabled"], "priority": row["priority"]} for row in (res.data or [])}


def set_playbook_enabled(client: Client, playbook_id: str, enabled: bool, changed_by: str) -> None:
    """SUPER_ADMIN-only in practice — enforced by RLS on playbook_configs
    itself (see the migration), not just by the caller checking
    authorization.require_super_admin() first. Also writes one audit row
    (§42) — only for this kind of admin change, never for ordinary reads."""
    old = get_playbook_overrides(client).get(playbook_id, {})
    client.table("playbook_configs").upsert(
        {"playbook_id": playbook_id, "enabled": enabled, "updated_by": changed_by}, on_conflict="playbook_id",
    ).execute()
    client.table("playbook_config_audit_log").insert({
        "playbook_id": playbook_id, "changed_by": changed_by, "field_changed": "enabled",
        "old_value": str(old.get("enabled")), "new_value": str(enabled),
    }).execute()


def list_audit_log(client: Client, limit: int = 50) -> list[dict]:
    try:
        res = (
            client.table("playbook_config_audit_log").select("*").order("changed_at", desc=True).limit(limit).execute()
        )
    except Exception:  # noqa: BLE001 - migration 0007 may not be applied yet
        logger.exception("Failed to load playbook_config_audit_log")
        return []
    return res.data or []
