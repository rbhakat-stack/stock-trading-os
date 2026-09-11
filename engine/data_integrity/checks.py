"""Data-integrity gate (see §57 / §29 failure-mode table): trade-candidate
generation must never proceed on stale, missing, or malformed data. Every issue
found here is either a WARNING (surfaced, but analysis proceeds) or a FAILURE
(analysis must stop — `has_failure()` is the caller's fail-closed check).

Gap detection is intentionally a WARNING, not a FAILURE, in Phase 1: overnight
and weekend gaps are expected and this module is not yet calendar-aware. A
calendar-aware version arrives once `trading_calendar_sessions` exists
(TRADING_OS_DESIGN.md §14a) — this is a known, documented Phase 1 simplification.

Application acceptance hardening — STALE_DATA is EXCHANGE-SESSION AWARE
(see `_staleness_reference_timestamp`): staleness is measured against the
most recent moment bars are actually EXPECTED to exist, not raw wall-clock
time. During an active regular session, that's `now` itself (unchanged, full
protection). Once the session has closed — or before it opens, or on a
weekend/holiday — it's the close of the most recently completed session, so
a live wall clock ticking for hours after 16:00 ET never manufactures a
false STALE_DATA failure. A bar from an OLDER session than the one that
should currently be current still fails closed, unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

from engine.backtest.calendar import MARKET_TZ, session_bounds

_ONE_DAY_MINUTES = 1440


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str  # "WARNING" | "FAILURE"


def check_bars(
    df: pd.DataFrame,
    timeframe_minutes: int,
    max_staleness_multiple: float = 3.0,
    now: pd.Timestamp | None = None,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []

    if df.empty:
        return [DataQualityIssue("NO_DATA", "No bars available", "FAILURE")]

    if df.index.duplicated().any():
        issues.append(DataQualityIssue("DUPLICATE_TIMESTAMPS", "Duplicate bar timestamps found", "FAILURE"))

    if not df.index.is_monotonic_increasing:
        issues.append(DataQualityIssue("UNSORTED", "Bars are not sorted ascending by time", "FAILURE"))

    price_cols = df[["open", "high", "low", "close"]]
    if price_cols.isna().any().any():
        issues.append(DataQualityIssue("MISSING_PRICE", "NaN price values present", "FAILURE"))

    if (df["high"] < df["low"]).any():
        issues.append(DataQualityIssue("INVALID_HIGH_LOW", "High < Low on at least one bar", "FAILURE"))

    if (price_cols <= 0).any().any():
        issues.append(DataQualityIssue("NON_POSITIVE_PRICE", "Non-positive price value present", "FAILURE"))

    expected_delta = pd.Timedelta(minutes=timeframe_minutes)
    gaps = df.index.to_series().diff().dropna()
    if not gaps.empty and (gaps > expected_delta * 1.5).any():
        issues.append(DataQualityIssue("GAP_DETECTED", "One or more bar gaps exceed 1.5x the expected interval", "WARNING"))

    if now is not None:
        reference = _staleness_reference_timestamp(now, timeframe_minutes)
        staleness = reference - df.index[-1]
        if staleness > expected_delta * max_staleness_multiple:
            issues.append(DataQualityIssue("STALE_DATA", f"Latest bar is {staleness} old", "FAILURE"))

    return issues


def _staleness_reference_timestamp(now: pd.Timestamp, timeframe_minutes: int) -> pd.Timestamp:
    """The timestamp bars are actually expected to be current AS OF, given
    U.S. equity exchange session state:

    - Intraday timeframes DURING an active regular session: `now` itself —
      staleness is still measured live, full protection unchanged.
    - Otherwise (after close, before open, weekend, holiday, or ANY time
      for a daily timeframe — a daily bar for a still-open session doesn't
      exist yet): the close of the most recently completed trading session
      at or before `now`.

    Reuses `engine.backtest.calendar.session_bounds` — the same trading-
    calendar abstraction Phase 5.1/5.1P/5.2 already rely on — never a
    second calendar implementation.
    """
    local_now = now.tz_convert(MARKET_TZ) if now.tzinfo is not None else now.tz_localize(MARKET_TZ)
    today = local_now.date()
    bounds = session_bounds(today)
    is_daily = timeframe_minutes >= _ONE_DAY_MINUTES

    if not is_daily and bounds is not None and bounds.open_at <= local_now < bounds.close_at:
        return local_now  # active session — live wall-clock reference (same instant as `now`, tz-normalized)

    candidate = today if (bounds is not None and local_now >= bounds.close_at) else today - timedelta(days=1)
    while session_bounds(candidate) is None:
        candidate -= timedelta(days=1)
    return session_bounds(candidate).close_at


def has_failure(issues: list[DataQualityIssue]) -> bool:
    return any(i.severity == "FAILURE" for i in issues)
