"""Opening range engine (§17).

Session-open assumption: 09:30 America/New_York (US equities). There is no
calendar-aware session source yet (Phase 1 deferred `trading_calendar_sessions`
per TRADING_OS_DESIGN.md §14a) — this is a documented default, not a
calendar-aware determination, and isn't holiday-aware. Configurable via
`session_open_time`.

Reuses breakouts.classify_breakout for ABOVE_ORH/BELOW_ORL follow-through
(OPENING_RANGE_BREAKOUT / _FAKEOUT / _BREAKOUT_THAT_LATER_FAILED etc.) rather
than a separate implementation — an opening-range break is just a breakout
whose level happens to be ORH/ORL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from enum import Enum

import pandas as pd

from .breakouts import BreakoutEvaluation, classify_breakout
from .time_of_day import MARKET_TZ

DEFAULT_SESSION_OPEN = time(9, 30)
SUPPORTED_WINDOW_MINUTES = (5, 15, 30, 60)


class OpeningRangeStatus(str, Enum):
    INSIDE_OPENING_RANGE = "INSIDE_OPENING_RANGE"
    ABOVE_ORH = "ABOVE_ORH"
    BELOW_ORL = "BELOW_ORL"


@dataclass(frozen=True)
class OpeningRangeEvaluation:
    session_date: str | None
    window_minutes: int
    orh: float | None
    orl: float | None
    midpoint: float | None
    width: float | None
    width_atr: float | None
    opening_volume: int | None  # a share/contract count, not a float — see the `bigint` column it persists to
    status: str  # OpeningRangeStatus value, a BreakoutState value, or "INSUFFICIENT_DATA"
    breakout: BreakoutEvaluation | None
    evidence: dict = field(default_factory=dict)


def _atr_at_end(atr_series: pd.Series) -> float:
    val = atr_series.iloc[-1]
    if pd.isna(val) or val <= 0:
        fallback = atr_series.dropna().mean()
        return float(fallback) if fallback and fallback > 0 else 1.0
    return float(val)


def compute_opening_range(
    df: pd.DataFrame,
    atr_series: pd.Series,
    window_minutes: int = 30,
    session_open_time: time = DEFAULT_SESSION_OPEN,
) -> OpeningRangeEvaluation:
    if window_minutes not in SUPPORTED_WINDOW_MINUTES:
        raise ValueError(f"window_minutes must be one of {SUPPORTED_WINDOW_MINUTES}")

    local_index = df.index.tz_convert(MARKET_TZ)
    dates = local_index.date
    latest_date = dates[-1]
    day_mask = dates == latest_date

    session_start_local = pd.Timestamp.combine(latest_date, session_open_time).tz_localize(MARKET_TZ)
    session_end_local = session_start_local + pd.Timedelta(minutes=window_minutes)

    or_mask = day_mask & (local_index >= session_start_local) & (local_index < session_end_local)
    or_df = df.loc[or_mask]

    if or_df.empty:
        return OpeningRangeEvaluation(
            session_date=str(latest_date), window_minutes=window_minutes, orh=None, orl=None, midpoint=None,
            width=None, width_atr=None, opening_volume=None, status="INSUFFICIENT_DATA", breakout=None,
            evidence={"reason": "no bars found within the opening-range window for the latest session"},
        )

    orh = round(float(or_df["high"].max()), 4)
    orl = round(float(or_df["low"].min()), 4)
    midpoint = (orh + orl) / 2
    width = orh - orl
    atr_val = _atr_at_end(atr_series)
    width_atr = width / atr_val
    opening_volume = int(or_df["volume"].sum())

    after_or_mask = day_mask & (local_index >= session_end_local)
    after_df = df.loc[after_or_mask]

    breakout: BreakoutEvaluation | None = None
    if after_df.empty:
        status = OpeningRangeStatus.INSIDE_OPENING_RANGE.value
    else:
        last_close = float(after_df["close"].iloc[-1])
        if orl <= last_close <= orh:
            status = OpeningRangeStatus.INSIDE_OPENING_RANGE.value
        else:
            direction = "UP" if last_close > orh else "DOWN"
            level = orh if direction == "UP" else orl
            break_bar_index = None
            for idx in after_df.index:
                pos = df.index.get_loc(idx)
                c = float(df["close"].iloc[pos])
                if (direction == "UP" and c > level) or (direction == "DOWN" and c < level):
                    break_bar_index = pos
                    break
            if break_bar_index is not None:
                breakout = classify_breakout(df, level, direction, break_bar_index, atr_series)
                status = breakout.state.value
            else:
                status = OpeningRangeStatus.ABOVE_ORH.value if direction == "UP" else OpeningRangeStatus.BELOW_ORL.value

    return OpeningRangeEvaluation(
        session_date=str(latest_date),
        window_minutes=window_minutes,
        orh=round(orh, 4),
        orl=round(orl, 4),
        midpoint=round(midpoint, 4),
        width=round(width, 4),
        width_atr=round(width_atr, 3),
        opening_volume=opening_volume,
        status=status,
        breakout=breakout,
        evidence={"opening_range_bars": len(or_df), "session_open_time": str(session_open_time)},
    )
