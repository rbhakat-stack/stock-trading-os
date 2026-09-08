"""Repository functions for user-owned watchlists (RLS owner-scoped)."""
from __future__ import annotations

from supabase import Client


def get_or_create_default_watchlist(client: Client, user_id: str) -> dict:
    res = client.table("watchlists").select("*").eq("user_id", user_id).order("created_at").limit(1).execute()
    if res.data:
        return res.data[0]
    created = client.table("watchlists").insert({"user_id": user_id, "name": "My Watchlist"}).execute()
    return created.data[0]


def list_watchlist_items(client: Client, watchlist_id) -> list[str]:
    res = client.table("watchlist_items").select("symbol").eq("watchlist_id", watchlist_id).execute()
    return [row["symbol"] for row in (res.data or [])]


def add_watchlist_item(client: Client, watchlist_id, symbol: str) -> None:
    client.table("watchlist_items").upsert(
        {"watchlist_id": watchlist_id, "symbol": symbol.upper()}, on_conflict="watchlist_id,symbol"
    ).execute()


def remove_watchlist_item(client: Client, watchlist_id, symbol: str) -> None:
    client.table("watchlist_items").delete().eq("watchlist_id", watchlist_id).eq("symbol", symbol.upper()).execute()
