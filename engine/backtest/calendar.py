"""Minimal NYSE/Nasdaq U.S. equity trading-calendar (Phase 5.0 §6).

Distinguishes "market closed" (weekend/holiday) from "market open but data
genuinely missing" — the replay/outcome engines (Phase 5.1+, not built yet)
need this distinction so an ordinary weekend is never mistaken for a
data-quality gap. Implemented in pure Python (no third-party calendar
package) per the explicit instruction not to install unverified third-party
software: NYSE's holiday schedule follows well-documented, fixed
federal-holiday observance rules plus a standard Easter-Sunday algorithm —
none of that needs vendor data to compute correctly.

KNOWN, DOCUMENTED LIMITATION (§6: "otherwise fail/document clearly rather
than fabricating a full session"): early-close ("half day") sessions
(typically the day after Thanksgiving, and sometimes July 3rd/Dec 24th) are
NOT modeled. Every trading day is treated as a full 09:30-16:00
America/New_York session. Modeling early closes correctly requires a
verified, published exchange calendar, which is not available here without
adding a new dependency — `SessionBounds.is_full_session_confirmed` is
`False` on every result specifically so a caller can never mistake this for
a verified close time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0 .. Sunday=6. n is 1-indexed (1st, 2nd, 3rd, ...)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset)
    d += timedelta(weeks=n - 1)
    return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher) — standard,
    well-known, fully deterministic; no external dependency needed."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = ((h + m - 7 * n + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """NYSE weekend-observance rule: a holiday falling on Saturday is
    observed the preceding Friday; on Sunday, the following Monday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    """The fixed NYSE holiday schedule for one calendar year, with
    weekend-observance shifting already applied. Juneteenth is only an NYSE
    holiday from 2022 onward, matching actual exchange history. Also
    includes December's shifted New Year's-Day observance for `year + 1`
    when Jan 1 of the following year is a Saturday (observed the preceding
    Friday, which falls in `year`'s own December) — the one case where a
    holiday's OBSERVED date crosses a calendar-year boundary from its
    nominal date.
    """
    holidays = {
        _observed(date(year, 1, 1)),                    # New Year's Day
        _nth_weekday_of_month(year, 1, 0, 3),            # MLK Day — 3rd Monday Jan
        _nth_weekday_of_month(year, 2, 0, 3),            # Washington's Birthday — 3rd Monday Feb
        _easter_sunday(year) - timedelta(days=2),        # Good Friday
        _last_weekday_of_month(year, 5, 0),              # Memorial Day — last Monday May
        _observed(date(year, 7, 4)),                     # Independence Day
        _nth_weekday_of_month(year, 9, 0, 1),             # Labor Day — 1st Monday Sep
        _nth_weekday_of_month(year, 11, 3, 4),            # Thanksgiving — 4th Thursday Nov
        _observed(date(year, 12, 25)),                    # Christmas
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))        # Juneteenth
    next_new_year_observed = _observed(date(year + 1, 1, 1))
    if next_new_year_observed.year == year:
        holidays.add(next_new_year_observed)
    return holidays


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


@dataclass(frozen=True)
class SessionBounds:
    session_date: date
    open_at: datetime               # tz-aware, MARKET_TZ
    close_at: datetime              # tz-aware, MARKET_TZ
    is_full_session_confirmed: bool  # always False — half-days are not modeled; see module docstring


def session_bounds(d: date) -> SessionBounds | None:
    """None if `d` is not a trading day at all (weekend/holiday) — telling
    that apart from "a real trading day with no data" is the caller's job,
    never this function's."""
    if not is_trading_day(d):
        return None
    return SessionBounds(
        session_date=d,
        open_at=datetime.combine(d, SESSION_OPEN, tzinfo=MARKET_TZ),
        close_at=datetime.combine(d, SESSION_CLOSE, tzinfo=MARKET_TZ),
        is_full_session_confirmed=False,
    )


def classify_missing_bar(ts: datetime) -> str:
    """For a timestamp where no bar was found: distinguishes MARKET_CLOSED
    (weekend/holiday/outside RTH — expected, not a data-quality issue) from
    MISSING_DURING_SESSION (a real gap that ingestion/replay should treat as
    a data-quality issue, mirroring engine/data_integrity/checks.py's
    existing GAP_DETECTED pattern)."""
    local = ts.astimezone(MARKET_TZ)
    bounds = session_bounds(local.date())
    if bounds is None:
        return "MARKET_CLOSED"
    if not (bounds.open_at <= local < bounds.close_at):
        return "MARKET_CLOSED"
    return "MISSING_DURING_SESSION"
