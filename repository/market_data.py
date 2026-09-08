"""Repository functions for shared reference market data (symbols/bars/swings/
market-state events) — see TRADING_OS_DESIGN.md §21.

PHASE 1 NOTE: these are called with the caller's authenticated client (RLS
`authenticated`-role write policies), not a service-role key — see the comment
in supabase/migrations/0001_phase1_schema.sql for why, and when to change it.
"""
from __future__ import annotations

import pandas as pd
from supabase import Client

from engine.market_state.types import MarketStateEvent, SwingPoint


def upsert_symbol(client: Client, symbol: str, name: str | None, exchange: str | None, asset_type: str = "EQUITY") -> None:
    client.table("symbols").upsert(
        {"symbol": symbol, "name": name, "exchange": exchange, "asset_type": asset_type}
    ).execute()


def upsert_bars(client: Client, symbol: str, timeframe: str, df: pd.DataFrame, provider: str, batch_size: int = 500) -> None:
    rows = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": ts.isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "provider": provider,
        }
        for ts, row in df.iterrows()
    ]
    for i in range(0, len(rows), batch_size):
        client.table("bars").upsert(rows[i : i + batch_size], on_conflict="symbol,timeframe,ts").execute()


def fetch_bars(client: Client, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
    res = (
        client.table("bars")
        .select("ts,open,high,low,close,volume")
        .eq("symbol", symbol)
        .eq("timeframe", timeframe)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").sort_index()


def upsert_swing_points(client: Client, symbol: str, timeframe: str, points: list[SwingPoint], algorithm_version: str) -> None:
    if not points:
        return
    rows = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": p.ts.isoformat(),
            "price": p.price,
            "swing_type": p.swing_type.value,
            "significance": p.significance.value,
            "score": p.score,
            "algorithm_version": algorithm_version,
            "evidence": p.evidence,
        }
        for p in points
    ]
    client.table("swing_points").upsert(rows, on_conflict="symbol,timeframe,ts,swing_type").execute()


def upsert_market_state_events(client: Client, symbol: str, timeframe: str, events: list[MarketStateEvent], algorithm_version: str) -> None:
    if not events:
        return
    rows = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": e.ts.isoformat(),
            "state": e.state.value,
            "structure_label": e.structure_label.value,
            "evidence": e.evidence,
            "algorithm_version": algorithm_version,
        }
        for e in events
    ]
    client.table("market_state_events").insert(rows).execute()
