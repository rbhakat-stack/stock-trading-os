"""Time-of-day context segmentation (§18).

Context only — never implies a directional expectation for any segment.
Assumes US-equity regular trading hours (09:30-16:00 America/New_York); this
mirrors the same documented assumption opening_range.py makes, since there is
no calendar-aware session source yet (see TRADING_OS_DESIGN.md §14a).
"""
from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")


class TimeOfDaySegment(str, Enum):
    PREMARKET = "PREMARKET"
    OPENING = "OPENING"  # 09:30-10:30
    LATE_MORNING = "LATE_MORNING"  # 10:30-12:00
    MIDDAY = "MIDDAY"  # 12:00-14:00
    AFTERNOON_TRANSITION = "AFTERNOON_TRANSITION"  # 14:00-15:00
    CLOSING = "CLOSING"  # 15:00-16:00
    POSTMARKET = "POSTMARKET"


_SEGMENT_BOUNDS = [
    (time(9, 30), time(10, 30), TimeOfDaySegment.OPENING),
    (time(10, 30), time(12, 0), TimeOfDaySegment.LATE_MORNING),
    (time(12, 0), time(14, 0), TimeOfDaySegment.MIDDAY),
    (time(14, 0), time(15, 0), TimeOfDaySegment.AFTERNOON_TRANSITION),
    (time(15, 0), time(16, 0), TimeOfDaySegment.CLOSING),
]


def classify_time_of_day(ts: datetime) -> str:
    local = ts.astimezone(MARKET_TZ)
    t = local.time()
    if t < time(9, 30):
        return TimeOfDaySegment.PREMARKET.value
    if t >= time(16, 0):
        return TimeOfDaySegment.POSTMARKET.value
    for start, end, segment in _SEGMENT_BOUNDS:
        if start <= t < end:
            return segment.value
    return TimeOfDaySegment.POSTMARKET.value  # defensive; unreachable given the checks above
