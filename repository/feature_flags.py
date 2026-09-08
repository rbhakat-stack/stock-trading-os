"""Read-only feature-flag access via the ordinary (RLS-scoped) client — flags are
public reference data to any authenticated user, same pattern as the `roles`
lookup table. Writes are privileged and live in admin_repository.py instead.
"""
from __future__ import annotations

from supabase import Client

KNOWN_FLAGS = (
    "paper_trading_enabled",
    "live_decision_support_enabled",
    "live_execution_enabled",
    "maintenance_mode",
)


def get_feature_flags(client: Client) -> dict[str, bool]:
    res = client.table("feature_flags").select("key, enabled").execute()
    rows = res.data or []
    flags = {row["key"]: bool(row["enabled"]) for row in rows}
    # Fail closed for anything missing rather than assuming disabled-is-safe for
    # every flag — callers that care about a specific flag should check it's present.
    return flags


def is_maintenance_mode_active(client: Client) -> bool:
    return get_feature_flags(client).get("maintenance_mode", False)
