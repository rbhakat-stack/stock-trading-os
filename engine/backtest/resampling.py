"""Deterministic, session-aligned OHLCV resampling (Phase 5.0 §5).

Canonical Phase 5 v1 granularity is 5-minute regular-trading-hours (RTH)
bars — this module builds 15-minute/1-hour/daily bars FROM that canonical
source rather than any of them being independently ingested, so higher
timeframes can never silently drift from the 5-minute data they're derived
from (see the Phase 5 design report's §6 rationale).

PRECONDITION: `df` must already contain only regular-trading-hours bars
(09:30-16:00 America/New_York) for the input timeframe — this module does
not filter extended-hours bars itself; RTH-only filtering happens at
ingestion time (engine/backtest/ingestion.py), consistent with every
existing Phase 2 module's RTH assumption (engine/market_state/opening_range.py,
time_of_day.py).

Bucket boundaries are anchored to each trading SESSION's own market open in
America/New_York (engine/backtest/calendar.py) — never a naive UTC-hour
alignment, which would misplace every intraday bucket for a US-equity
session by whatever the current UTC offset happens to be.
"""
from __future__ import annotations

import pandas as pd

from .calendar import MARKET_TZ, session_bounds

_TARGET_TIMEFRAME_MINUTES = {"15min": 15, "1hour": 60}
SUPPORTED_TARGET_TIMEFRAMES = tuple(_TARGET_TIMEFRAME_MINUTES) + ("1day",)

_OHLCV_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Resamples a canonical 5-minute RTH OHLCV frame to a coarser
    `target_timeframe` ("15min" | "1hour" | "1day"). open=first, high=max,
    low=min, close=last, volume=sum.

    A session's FINAL bucket may be a genuine partial bucket (e.g. RTH is
    6.5 hours = 390 minutes, which divides evenly into 15-minute buckets but
    NOT into 60-minute ones — the last "hourly" bar of a session covers only
    the final 30 minutes). It is kept as-is, computed only from the bars
    that actually exist — never padded to a full bucket and never dropped,
    since either would misrepresent real market data.

    Raises ValueError for an unsupported target_timeframe. Returns an empty,
    correctly-shaped frame for an empty input (never crashes on empty data).
    """
    if target_timeframe not in SUPPORTED_TARGET_TIMEFRAMES:
        raise ValueError(f"unsupported target_timeframe: {target_timeframe!r}")
    if df.empty:
        return df.copy()

    local = df.tz_convert(MARKET_TZ) if df.index.tz is not None else df.tz_localize(MARKET_TZ)
    original_tz = df.index.tz

    if target_timeframe == "1day":
        out = _resample_daily(local)
    else:
        out = _resample_intraday(local, _TARGET_TIMEFRAME_MINUTES[target_timeframe])

    out.index.name = "ts"
    return out.tz_convert(original_tz) if original_tz is not None else out.tz_localize(None)


def _resample_daily(local: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for session_date, day_df in local.groupby(local.index.date):
        bounds = session_bounds(session_date)
        ts = bounds.open_at if bounds is not None else day_df.index[0]
        rows.append({
            "ts": ts,
            "open": float(day_df["open"].iloc[0]),
            "high": float(day_df["high"].max()),
            "low": float(day_df["low"].min()),
            "close": float(day_df["close"].iloc[-1]),
            "volume": int(day_df["volume"].sum()),
        })
    return pd.DataFrame(rows).set_index("ts").sort_index()


def _resample_intraday(local: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    out_frames = []
    for session_date, day_df in local.groupby(local.index.date):
        bounds = session_bounds(session_date)
        # Anchor each session's buckets at ITS OWN market open — never a
        # fixed clock time blindly reused across a DST transition. Once a
        # timestamp is localized to America/New_York, 09:30 local is always
        # 09:30 local; the underlying UTC offset difference across DST is
        # handled correctly by zoneinfo/pandas automatically.
        origin = bounds.open_at if bounds is not None else day_df.index[0]
        resampled = (
            day_df.resample(f"{target_minutes}min", origin=origin, label="left", closed="left")
            .agg(_OHLCV_AGG)
            .dropna(subset=["open"])  # drop buckets with no source bars in that window
        )
        resampled["volume"] = resampled["volume"].astype("int64")
        out_frames.append(resampled)
    return pd.concat(out_frames).sort_index() if out_frames else local.iloc[0:0]
