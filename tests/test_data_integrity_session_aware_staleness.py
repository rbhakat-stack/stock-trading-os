"""Application acceptance hardening — session-aware STALE_DATA freshness.

Root cause: engine.data_integrity.checks.check_bars's staleness check used
to compare raw wall-clock `now` against the latest bar with a flat
timeframe-based tolerance, with no awareness of U.S. equity exchange
session state — so refreshing Market Reader after the 16:00 ET close (bars
genuinely current through the close) reported a false STALE_DATA failure
merely because several wall-clock hours had elapsed since 16:00.

Fix: `_staleness_reference_timestamp` (reused via `check_bars`'s existing
`now=` parameter — no new parameter, no weakened default) compares the
latest bar against the close of the most recently completed trading
session (via engine.backtest.calendar.session_bounds) whenever `now` falls
outside an active regular session, or unconditionally for daily bars (a
daily bar for a still-open session doesn't exist yet). During an active
intraday session, the comparison is still the live wall clock — the
original protection is completely unchanged there.

All timestamps below are hand-constructed in real America/New_York session
time, never the actual current date/time.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.data_integrity.checks import check_bars, has_failure


def _bars(n: int, start="2024-01-02 09:30", freq="5min", tz="America/New_York") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    df = pd.DataFrame(
        {"open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n, "close": [100.0] * n,
         "volume": [1000] * n},
        index=idx,
    )
    return df.tz_convert("UTC")


def _et(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York")


def _is_stale(df, timeframe_minutes, now):
    issues = check_bars(df, timeframe_minutes=timeframe_minutes, now=now)
    return any(i.code == "STALE_DATA" for i in issues)


# ---------------------------------------------------------------------------
# §7 — 5-minute timeframe
# ---------------------------------------------------------------------------


def test_5min_market_open_latest_expected_bar_present_passes():
    # 2024-01-02 (Tue) is a normal trading day. Bars up through 10:55, now=11:00.
    df = _bars(18, start="2024-01-02 09:30")  # last bar 09:30 + 17*5min = 10:55 ET
    now = _et("2024-01-02 11:00")
    assert not _is_stale(df, 5, now)


def test_5min_market_open_latest_bar_much_too_old_is_stale():
    df = _bars(18, start="2024-01-02 09:30")  # last bar 10:55 ET
    now = _et("2024-01-02 13:00")  # still mid-session, but 2h05m since the last bar
    assert _is_stale(df, 5, now)


def test_5min_just_after_close_closing_bar_present_passes():
    df = _bars(78, start="2024-01-02 09:30")  # last bar 15:55 ET (the final 5-min RTH bar)
    now = _et("2024-01-02 16:05")  # market just closed
    assert not _is_stale(df, 5, now)


def test_5min_several_hours_after_close_closing_bar_present_passes():
    df = _bars(78, start="2024-01-02 09:30")  # last bar 15:55 ET
    now = _et("2024-01-02 21:30")  # hours after close — the original bug scenario
    assert not _is_stale(df, 5, now)


def test_5min_before_next_open_previous_close_present_passes():
    df = _bars(78, start="2024-01-02 09:30")  # Tuesday's close, last bar 15:55 ET
    now = _et("2024-01-03 08:00")  # Wednesday, before today's 09:30 open
    assert not _is_stale(df, 5, now)


def test_5min_weekend_friday_close_present_passes():
    df = _bars(78, start="2024-01-05 09:30")  # Friday 2024-01-05
    now = _et("2024-01-06 15:00")  # Saturday afternoon
    assert not _is_stale(df, 5, now)
    now2 = _et("2024-01-07 12:00")  # Sunday too
    assert not _is_stale(df, 5, now2)


def test_5min_holiday_previous_session_close_present_passes():
    # 2024-01-01 is New Year's Day (NYSE holiday). Last real session before it: 2023-12-29 (Fri).
    df = _bars(78, start="2023-12-29 09:30")
    now = _et("2024-01-01 12:00")  # the holiday itself
    assert not _is_stale(df, 5, now)


def test_5min_newer_session_should_exist_but_only_older_data_present_is_stale():
    # Data only through Monday 2024-01-08's close; "now" is Wednesday 2024-01-10
    # mid-session — Tuesday's (and today's) bars are missing entirely.
    df = _bars(78, start="2024-01-08 09:30")  # Monday close, last bar 15:55 ET
    now = _et("2024-01-10 11:00")  # Wednesday, mid-session
    assert _is_stale(df, 5, now)


def test_5min_before_open_but_newer_completed_session_missing_is_stale():
    # now is Wednesday before open; the most recently COMPLETED session is
    # Tuesday, but only Monday's data is present — Tuesday's session came
    # and went with no data at all.
    df = _bars(78, start="2024-01-08 09:30")  # Monday close
    now = _et("2024-01-10 08:00")  # Wednesday, before open
    assert _is_stale(df, 5, now)


# ---------------------------------------------------------------------------
# §7 — other intraday timeframes
# ---------------------------------------------------------------------------


def test_15min_after_close_passes():
    df = _bars(26, start="2024-01-02 09:30", freq="15min")  # 09:30 + 25*15min = 15:45; last full 15-min bar of the day
    now = _et("2024-01-02 20:00")
    assert not _is_stale(df, 15, now)


def test_15min_market_open_too_old_is_stale():
    df = _bars(5, start="2024-01-02 09:30", freq="15min")  # last bar 10:30 ET
    now = _et("2024-01-02 12:00")  # mid-session, over an hour since the last bar
    assert _is_stale(df, 15, now)


def test_1hour_after_close_passes():
    df = _bars(7, start="2024-01-02 09:30", freq="1h")  # 09:30..15:30 ET
    now = _et("2024-01-03 07:00")  # next morning, before open
    assert not _is_stale(df, 60, now)


def test_1hour_market_open_too_old_is_stale():
    df = _bars(2, start="2024-01-02 09:30", freq="1h")  # last bar 10:30 ET
    now = _et("2024-01-02 15:00")  # still mid-session, far too old
    assert _is_stale(df, 60, now)


# ---------------------------------------------------------------------------
# §5 — daily timeframe: never "stale minutes after close", and a daily bar
# for a still-open session is never expected mid-session either.
# ---------------------------------------------------------------------------


def _daily_bars(n, start="2024-01-02") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n, "close": [100.0] * n, "volume": [1000] * n},
        index=idx,
    )


def test_daily_minutes_after_close_does_not_go_stale():
    # Latest daily bar dated "today" (2024-01-05, a Friday); now is shortly
    # after today's close.
    df = _daily_bars(4, start="2024-01-02")  # Tue, Wed, Thu, Fri
    now = _et("2024-01-05 16:10")
    assert not _is_stale(df, 1440, now)


def test_daily_mid_session_does_not_expect_todays_bar_yet():
    # now is DURING today's (Monday's) active session; the latest daily bar
    # is Friday's (the last actually-completed trading day) — must NOT be
    # stale, since today's own daily bar isn't finalized until today's close.
    idx = pd.date_range("2024-01-02", periods=4, freq="1D", tz="UTC")  # Tue, Wed, Thu, Fri (trading-day series)
    df = pd.DataFrame(
        {"open": [100.0] * 4, "high": [100.0] * 4, "low": [100.0] * 4, "close": [100.0] * 4, "volume": [1000] * 4},
        index=idx,
    )
    now = _et("2024-01-08 11:00")  # Monday, mid-session
    assert not _is_stale(df, 1440, now)


def test_daily_missing_a_completed_session_is_stale():
    df = _daily_bars(2, start="2024-01-02")  # only through Wed 2024-01-03
    now = _et("2024-01-10 11:00")  # a week later, mid-session — many sessions missing
    assert _is_stale(df, 1440, now)
