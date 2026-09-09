from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from engine.market_state.time_of_day import classify_time_of_day

ET = ZoneInfo("America/New_York")


def _et(year, month, day, hour, minute):
    """Builds a genuinely ET-local wall-clock time (DST-aware via zoneinfo,
    never a hardcoded offset) and converts it to UTC — proving the round trip
    through classify_time_of_day's internal UTC->ET conversion reproduces the
    same wall-clock time regardless of standard/daylight time."""
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


# 2024-01-15 is standard time (UTC-5); 2024-07-15 is daylight time (UTC-4).
STANDARD_DATE = (2024, 1, 15)
DAYLIGHT_DATE = (2024, 7, 15)


def test_premarket_before_open():
    assert classify_time_of_day(_et(*STANDARD_DATE, 8, 0)) == "PREMARKET"


def test_late_morning_segment():
    assert classify_time_of_day(_et(*STANDARD_DATE, 11, 0)) == "LATE_MORNING"


def test_midday_segment():
    assert classify_time_of_day(_et(*STANDARD_DATE, 13, 0)) == "MIDDAY"


def test_afternoon_transition_segment():
    assert classify_time_of_day(_et(*STANDARD_DATE, 14, 30)) == "AFTERNOON_TRANSITION"


def test_postmarket_after_close():
    assert classify_time_of_day(_et(*STANDARD_DATE, 17, 0)) == "POSTMARKET"


# ---- Exact boundaries requested: 09:29/09:30, 09:59/10:00, 15:59/16:00 ET ----


def test_0929_et_is_premarket():
    assert classify_time_of_day(_et(*STANDARD_DATE, 9, 29)) == "PREMARKET"


def test_0930_et_is_opening():
    assert classify_time_of_day(_et(*STANDARD_DATE, 9, 30)) == "OPENING"


def test_0959_et_is_opening():
    assert classify_time_of_day(_et(*STANDARD_DATE, 9, 59)) == "OPENING"


def test_1000_et_is_still_opening():
    # The OPENING/price-discovery segment spans 09:30-10:30 ET (per the original
    # spec), not 09:30-10:00 — that 30-minute figure is the unrelated Opening
    # Range *default window* (engine.market_state.opening_range), a separate
    # concept that happens to share the word "opening." 10:00 ET is still
    # within the OPENING time-of-day segment; the real boundary is 10:30.
    assert classify_time_of_day(_et(*STANDARD_DATE, 10, 0)) == "OPENING"


def test_1029_et_is_opening():
    assert classify_time_of_day(_et(*STANDARD_DATE, 10, 29)) == "OPENING"


def test_1030_et_is_late_morning():
    assert classify_time_of_day(_et(*STANDARD_DATE, 10, 30)) == "LATE_MORNING"


def test_1559_et_is_closing():
    assert classify_time_of_day(_et(*STANDARD_DATE, 15, 59)) == "CLOSING"


def test_1600_et_is_postmarket():
    assert classify_time_of_day(_et(*STANDARD_DATE, 16, 0)) == "POSTMARKET"


# ---- DST: the same ET wall-clock boundary must classify identically on both
# sides of the DST transition — proving conversion is tz-aware (zoneinfo),
# never a hard-coded UTC offset ----


def test_0930_et_is_opening_in_standard_time():
    assert classify_time_of_day(_et(*STANDARD_DATE, 9, 30)) == "OPENING"


def test_0930_et_is_opening_in_daylight_time():
    assert classify_time_of_day(_et(*DAYLIGHT_DATE, 9, 30)) == "OPENING"


def test_1600_et_is_postmarket_in_both_standard_and_daylight_time():
    assert classify_time_of_day(_et(*STANDARD_DATE, 16, 0)) == "POSTMARKET"
    assert classify_time_of_day(_et(*DAYLIGHT_DATE, 16, 0)) == "POSTMARKET"


def test_standard_and_daylight_utc_offsets_actually_differ():
    # Sanity check that this test fixture is really exercising two different
    # UTC offsets, not accidentally testing the same instant twice.
    standard_utc = _et(*STANDARD_DATE, 9, 30)
    daylight_utc = _et(*DAYLIGHT_DATE, 9, 30)
    assert standard_utc.hour != daylight_utc.hour
