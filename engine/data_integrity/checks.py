"""Data-integrity gate (see §57 / §29 failure-mode table): trade-candidate
generation must never proceed on stale, missing, or malformed data. Every issue
found here is either a WARNING (surfaced, but analysis proceeds) or a FAILURE
(analysis must stop — `has_failure()` is the caller's fail-closed check).

Gap detection is intentionally a WARNING, not a FAILURE, in Phase 1: overnight
and weekend gaps are expected and this module is not yet calendar-aware. A
calendar-aware version arrives once `trading_calendar_sessions` exists
(TRADING_OS_DESIGN.md §14a) — this is a known, documented Phase 1 simplification.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


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
        staleness = now - df.index[-1]
        if staleness > expected_delta * max_staleness_multiple:
            issues.append(DataQualityIssue("STALE_DATA", f"Latest bar is {staleness} old", "FAILURE"))

    return issues


def has_failure(issues: list[DataQualityIssue]) -> bool:
    return any(i.severity == "FAILURE" for i in issues)
