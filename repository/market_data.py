"""Repository functions for shared reference market data (symbols/bars/swings/
market-state events) — see TRADING_OS_DESIGN.md §21.

PHASE 1 NOTE: these are called with the caller's authenticated client (RLS
`authenticated`-role write policies), not a service-role key — see the comment
in supabase/migrations/0001_phase1_schema.sql for why, and when to change it.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from supabase import Client

from engine.market_state.breakouts import BreakoutEvaluation
from engine.market_state.opening_range import OpeningRangeEvaluation
from engine.market_state.support_resistance import EnrichedZone
from engine.market_state.types import MarketStateEvent, SwingPoint, SwingSignificance


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


def _is_preferred_swing_point(candidate: SwingPoint, current: SwingPoint) -> bool:
    """True if `candidate` should replace `current` as the canonical point for
    their shared (ts, swing_type) key. Precedence, in order:
      1. MAJOR beats MINOR
      2. higher significance score beats lower score
      3. full tie -> keep whichever was seen first (stable order) — `current`
         wins, so this function returns False.
    """
    if candidate.significance != current.significance:
        return candidate.significance == SwingSignificance.MAJOR
    if candidate.score != current.score:
        return candidate.score > current.score
    return False


def dedupe_swing_rows(points: list[SwingPoint]) -> list[SwingPoint]:
    """Guarantees at most one SwingPoint per (ts, swing_type) — the effective
    conflict key `upsert_swing_points` writes under (symbol/timeframe are
    constant for a single call). Pure, deterministic, no Supabase dependency —
    see _is_preferred_swing_point for the precedence rules. This is
    defense-in-depth: the upstream root cause (a bug in
    engine.market_state.structure.label_structure) is fixed separately, but a
    future regression anywhere upstream should not be able to reach Postgres
    as an unhandled ON CONFLICT error again.
    """
    best: dict[tuple, SwingPoint] = {}
    key_order: list[tuple] = []
    for p in points:
        key = (p.ts, p.swing_type)
        if key not in best:
            best[key] = p
            key_order.append(key)
        elif _is_preferred_swing_point(p, best[key]):
            best[key] = p
    return [best[k] for k in key_order]


def upsert_swing_points(client: Client, symbol: str, timeframe: str, points: list[SwingPoint], algorithm_version: str) -> None:
    if not points:
        return
    points = dedupe_swing_rows(points)
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


def upsert_market_state_events(
    client: Client,
    symbol: str,
    timeframe: str,
    events: list[MarketStateEvent],
    algorithm_version: str,
    extra_evidence_by_ts: dict | None = None,
) -> None:
    """`extra_evidence_by_ts` (optional) maps an event's `ts` to additional
    fields merged into its `evidence` jsonb before insert — this is how Phase 2
    BOS/CHOCH strength scoring reuses this column rather than needing a new one
    (see migration 0003's persistence-philosophy comment)."""
    if not events:
        return
    extra_evidence_by_ts = extra_evidence_by_ts or {}
    rows = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": e.ts.isoformat(),
            "state": e.state.value,
            "structure_label": e.structure_label.value,
            "evidence": {**e.evidence, **extra_evidence_by_ts.get(e.ts, {})},
            "algorithm_version": algorithm_version,
        }
        for e in events
    ]
    client.table("market_state_events").insert(rows).execute()


def upsert_sr_zones(client: Client, symbol: str, timeframe: str, zones: list[EnrichedZone]) -> None:
    if not zones:
        return
    rows = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "zone_type": z.zone_type,
            "upper_boundary": z.upper_boundary,
            "lower_boundary": z.lower_boundary,
            "strength_score": z.strength_score,
            "touch_count": z.touch_count,
            "rejection_count": z.rejection_count,
            "break_count": z.break_count,
            "retest_count": z.retest_count,
            "last_touch_time": z.last_touch_time.isoformat() if z.last_touch_time else None,
            "role_reversal_history": [
                {"ts": ev.ts.isoformat(), "from_type": ev.from_type, "to_type": ev.to_type}
                for ev in z.role_reversal_history
            ],
        }
        for z in zones
    ]
    client.table("sr_zones").insert(rows).execute()


def upsert_breakout_event(
    client: Client,
    symbol: str,
    timeframe: str,
    source: str,
    breakout: BreakoutEvaluation,
    algorithm_version: str,
) -> None:
    """`source` is one of 'BOS', 'SR_ZONE', 'OPENING_RANGE' — see migration 0003."""
    client.table("breakout_events").insert(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": breakout.ts.isoformat(),
            "source": source,
            "level": breakout.level,
            "direction": breakout.direction,
            "state": breakout.state.value,
            "retest_state": breakout.retest.value,
            "follow_through_state": breakout.follow_through.value,
            "evidence": breakout.evidence,
            "algorithm_version": algorithm_version,
        }
    ).execute()


def upsert_opening_range_event(
    client: Client,
    symbol: str,
    timeframe: str,
    session_date: date,
    opening_range: OpeningRangeEvaluation,
    algorithm_version: str,
) -> None:
    client.table("opening_range_events").upsert(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "session_date": session_date.isoformat(),
            "window_minutes": opening_range.window_minutes,
            "orh": opening_range.orh,
            "orl": opening_range.orl,
            "midpoint": opening_range.midpoint,
            "width": opening_range.width,
            "width_atr": opening_range.width_atr,
            "opening_volume": opening_range.opening_volume,
            "status": opening_range.status,
            "evidence": opening_range.evidence,
            "algorithm_version": algorithm_version,
        },
        on_conflict="symbol,timeframe,session_date,window_minutes",
    ).execute()
