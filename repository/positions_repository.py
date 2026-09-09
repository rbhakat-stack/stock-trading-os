"""Repository functions for owner-scoped manual/simulated positions
(account_positions) — see supabase/migrations/0006_phase3_positions_and_final_hardening.sql
and engine/risk/positions.py for the signed-quantity model this persists.

Uses the caller's authenticated (RLS-scoped) client, same as every other
Phase 3 risk table — never the service-role admin client.
"""
from __future__ import annotations

from datetime import datetime

from supabase import Client

from engine.risk.positions import AccountPosition


def list_positions(client: Client, user_id: str) -> list[AccountPosition]:
    res = client.table("account_positions").select("*").eq("user_id", user_id).order("symbol").execute()
    rows = res.data or []
    return [
        AccountPosition(
            symbol=row["symbol"], quantity_signed=row["quantity_signed"], reference_price=row["reference_price"],
            average_price=row.get("average_price"), planned_stop_price=row.get("planned_stop_price"),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else datetime.utcnow(),
        )
        for row in rows
    ]


def _position_row(user_id: str, position: AccountPosition) -> dict:
    return {
        "user_id": user_id, "symbol": position.symbol, "quantity_signed": position.quantity_signed,
        "reference_price": position.reference_price, "average_price": position.average_price,
        "planned_stop_price": position.planned_stop_price,
    }


def upsert_position(client: Client, user_id: str, position: AccountPosition) -> None:
    if position.quantity_signed == 0:
        # A flattened position isn't a position any more — remove the row
        # instead of persisting a meaningless quantity_signed=0 record.
        delete_position(client, user_id, position.symbol)
        return
    client.table("account_positions").upsert(_position_row(user_id, position), on_conflict="user_id,symbol").execute()


def upsert_positions(client: Client, user_id: str, positions: list[AccountPosition]) -> None:
    """Batch variant of upsert_position — sends every row in ONE request, so
    Postgres applies them as a single INSERT...ON CONFLICT statement (all-or-
    nothing) instead of one independent HTTP call per row. Positions with
    quantity_signed==0 are skipped here (callers should route those through
    delete_positions instead — a zero-quantity row is not a position)."""
    rows = [_position_row(user_id, p) for p in positions if p.quantity_signed != 0]
    if not rows:
        return
    client.table("account_positions").upsert(rows, on_conflict="user_id,symbol").execute()


def delete_position(client: Client, user_id: str, symbol: str) -> None:
    client.table("account_positions").delete().eq("user_id", user_id).eq("symbol", symbol).execute()


def delete_positions(client: Client, user_id: str, symbols: list[str]) -> None:
    """Batch variant of delete_position — one request for every symbol removed."""
    if not symbols:
        return
    client.table("account_positions").delete().eq("user_id", user_id).in_("symbol", symbols).execute()
