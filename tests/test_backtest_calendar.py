"""Phase 5.0 §6 — minimal NYSE trading calendar: market-closed vs.
missing-during-session distinction, weekend/holiday handling."""
from datetime import date, datetime, timezone

from engine.backtest.calendar import classify_missing_bar, is_trading_day, nyse_holidays, session_bounds


def test_known_2024_nyse_holidays_match_the_published_schedule():
    # Exact published 2024 NYSE holiday schedule (10 dates).
    expected = {
        date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
        date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
        date(2024, 11, 28), date(2024, 12, 25),
    }
    assert nyse_holidays(2024) == expected


def test_juneteenth_only_observed_from_2022_onward():
    # 2021-06-19 was itself a Saturday (already excluded as a weekend, not
    # by holiday logic) — the real regression check is that NO Juneteenth
    # date (nominal or weekend-shifted) is added to the pre-2022 set at all.
    holidays_2021 = nyse_holidays(2021)
    assert date(2021, 6, 18) not in holidays_2021
    assert date(2021, 6, 19) not in holidays_2021
    assert date(2021, 6, 21) not in holidays_2021
    assert date(2022, 6, 20) in nyse_holidays(2022)  # 2022-06-19 was a Sunday -> observed Monday


def test_weekend_is_not_a_trading_day():
    assert not is_trading_day(date(2024, 1, 6))  # Saturday
    assert not is_trading_day(date(2024, 1, 7))  # Sunday


def test_ordinary_weekday_is_a_trading_day():
    assert is_trading_day(date(2024, 1, 2))  # Tuesday, no holiday


def test_new_year_2022_shifts_into_previous_december():
    # 2022-01-01 was a Saturday -> observed Friday 2021-12-31.
    assert date(2021, 12, 31) in nyse_holidays(2021)
    assert not is_trading_day(date(2021, 12, 31))


def test_session_bounds_none_for_non_trading_day():
    assert session_bounds(date(2024, 1, 1)) is None       # holiday
    assert session_bounds(date(2024, 1, 6)) is None        # Saturday


def test_session_bounds_are_correct_local_open_close():
    bounds = session_bounds(date(2024, 1, 2))
    assert bounds is not None
    assert bounds.open_at.hour == 9 and bounds.open_at.minute == 30
    assert bounds.close_at.hour == 16 and bounds.close_at.minute == 0
    assert bounds.is_full_session_confirmed is False  # half-days not modeled — documented, not fabricated


def test_classify_missing_bar_weekend_is_market_closed():
    ts = datetime(2024, 1, 6, 15, 0, tzinfo=timezone.utc)  # a Saturday
    assert classify_missing_bar(ts) == "MARKET_CLOSED"


def test_classify_missing_bar_holiday_is_market_closed():
    ts = datetime(2024, 7, 4, 15, 0, tzinfo=timezone.utc)
    assert classify_missing_bar(ts) == "MARKET_CLOSED"


def test_classify_missing_bar_outside_rth_is_market_closed():
    # 2024-01-02 03:00 UTC = 2024-01-01 22:00 EST the PRIOR trading day's evening — well outside RTH.
    ts = datetime(2024, 1, 3, 3, 0, tzinfo=timezone.utc)
    assert classify_missing_bar(ts) == "MARKET_CLOSED"


def test_classify_missing_bar_during_session_is_missing_during_session():
    # 2024-01-02 is a Tuesday, 15:00 UTC = 10:00 EST — inside RTH.
    ts = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
    assert classify_missing_bar(ts) == "MISSING_DURING_SESSION"
