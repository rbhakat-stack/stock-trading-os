"""Volume participation classification and RVOL (§13-14).

Core rule: PRICE ACTION FIRST, VOLUME SECOND — this module never infers
direction from volume, only participation level. RVOL never claims a value
when there isn't enough history to support one; it says so explicitly instead
of quietly falling back to a misleadingly precise number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

VERY_LOW_MAX = 0.5
LOW_MAX = 0.8
NORMAL_MAX = 1.2
ELEVATED_MAX = 1.8
HIGH_MAX = 3.0

MIN_BARS_FOR_ROLLING_RVOL = 20
MIN_DAYS_FOR_TIME_OF_DAY_RVOL = 3
TIME_OF_DAY_TOLERANCE_MINUTES = 2


class VolumeLevel(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class RvolResult:
    value: float | None  # None if insufficient data — never a fabricated number
    method: str  # "TIME_OF_DAY_BASELINE" | "ROLLING_20_BAR" | "INSUFFICIENT_DATA"
    baseline: float | None
    evidence: dict = field(default_factory=dict)


def average_volume(df: pd.DataFrame, window: int) -> pd.Series:
    return df["volume"].rolling(window=window, min_periods=window).mean()


def classify_volume_level(current: float, baseline: float | None) -> str:
    """Returns a VolumeLevel value, or 'INSUFFICIENT_DATA' if no baseline exists."""
    if baseline is None or baseline <= 0:
        return "INSUFFICIENT_DATA"
    ratio = current / baseline
    if ratio < VERY_LOW_MAX:
        return VolumeLevel.VERY_LOW.value
    if ratio < LOW_MAX:
        return VolumeLevel.LOW.value
    if ratio < NORMAL_MAX:
        return VolumeLevel.NORMAL.value
    if ratio < ELEVATED_MAX:
        return VolumeLevel.ELEVATED.value
    if ratio < HIGH_MAX:
        return VolumeLevel.HIGH.value
    return VolumeLevel.EXTREME.value


def compute_rvol(
    df: pd.DataFrame,
    bar_index: int,
    time_of_day_tolerance_minutes: int = TIME_OF_DAY_TOLERANCE_MINUTES,
) -> RvolResult:
    """RVOL = current bar volume / an appropriate historical baseline.

    Prefers a time-of-day-adjusted baseline (same clock time across at least
    MIN_DAYS_FOR_TIME_OF_DAY_RVOL prior distinct trading days); falls back to a
    rolling 20-bar average when there isn't enough multi-day history; returns
    INSUFFICIENT_DATA (never a guess) when neither is supported.
    """
    current_vol = float(df["volume"].iloc[bar_index])
    ts = df.index[bar_index]
    target_minutes = ts.hour * 60 + ts.minute

    same_time_vols: list[float] = []
    seen_dates: set = set()
    for i in range(bar_index):
        row_ts = df.index[i]
        if row_ts.date() == ts.date():
            continue
        row_minutes = row_ts.hour * 60 + row_ts.minute
        if abs(row_minutes - target_minutes) <= time_of_day_tolerance_minutes:
            same_time_vols.append(float(df["volume"].iloc[i]))
            seen_dates.add(row_ts.date())

    if len(seen_dates) >= MIN_DAYS_FOR_TIME_OF_DAY_RVOL and same_time_vols:
        baseline = sum(same_time_vols) / len(same_time_vols)
        method = "TIME_OF_DAY_BASELINE"
        evidence = {"denominator_bars": len(same_time_vols), "trading_days_used": len(seen_dates)}
    elif bar_index >= MIN_BARS_FOR_ROLLING_RVOL:
        window_vols = df["volume"].iloc[max(0, bar_index - MIN_BARS_FOR_ROLLING_RVOL) : bar_index]
        baseline = float(window_vols.mean())
        method = "ROLLING_20_BAR"
        evidence = {"denominator_bars": MIN_BARS_FOR_ROLLING_RVOL}
    else:
        return RvolResult(
            value=None,
            method="INSUFFICIENT_DATA",
            baseline=None,
            evidence={
                "reason": (
                    f"fewer than {MIN_BARS_FOR_ROLLING_RVOL} prior bars and fewer than "
                    f"{MIN_DAYS_FOR_TIME_OF_DAY_RVOL} prior trading days at this time-of-day"
                )
            },
        )

    if baseline <= 0:
        return RvolResult(value=None, method="INSUFFICIENT_DATA", baseline=baseline, evidence={"reason": "baseline volume is zero"})

    return RvolResult(value=round(current_vol / baseline, 3), method=method, baseline=round(baseline, 1), evidence=evidence)
