from datetime import time

import pandas as pd
import pytest

from engine.features.volatility import atr
from engine.market_state.opening_range import compute_opening_range

# 2024-01-15 is standard time (no DST) in America/New_York: UTC-5, so 09:30 ET = 14:30 UTC.
SESSION_START_UTC = pd.Timestamp("2024-01-15 14:30", tz="UTC")


def _build_df(closes, start=SESSION_START_UTC, freq="5min"):
    idx = pd.date_range(start=start, periods=len(closes), freq=freq)
    df = pd.DataFrame(
        {"open": closes, "high": [c + 0.1 for c in closes], "low": [c - 0.1 for c in closes],
         "close": closes, "volume": [50_000] * len(closes)},
        index=idx,
    )
    df.index.name = "ts"
    return df


def test_inside_opening_range_when_price_stays_within_bounds():
    # 6 bars = 09:30-10:00 (the 30-min opening window), then bars that stay within it.
    or_closes = [100.0, 100.5, 99.8, 100.2, 100.1, 100.3]
    after_closes = [100.2, 100.1, 100.0]
    df = _build_df(or_closes + after_closes)
    atr_series = atr(df, period=5)

    result = compute_opening_range(df, atr_series, window_minutes=30)
    assert result.orh == 100.6  # 100.5 + 0.1 high wick
    assert result.orl == 99.7  # 99.8 - 0.1 low wick
    assert result.status == "INSIDE_OPENING_RANGE"
    assert result.breakout is None


def test_opening_range_breakout_reuses_breakout_classification():
    or_closes = [100.0, 100.1, 99.9, 100.0, 100.1, 100.0]
    # Sustained close above ORH for many bars -> should resolve as a genuine breakout state.
    after_closes = [100.8, 101.0, 101.2, 101.4, 101.6, 101.8, 102.0]
    df = _build_df(or_closes + after_closes)
    atr_series = atr(df, period=5)

    result = compute_opening_range(df, atr_series, window_minutes=30)
    assert result.status in ("SUCCESSFUL_BREAKOUT", "SUCCESSFUL_BREAKOUT_RETEST", "BREAKOUT_ATTEMPT")
    assert result.breakout is not None
    assert result.breakout.direction == "UP"
    assert result.breakout.level == pytest.approx(result.orh)


def test_insufficient_data_when_no_bars_in_the_opening_window():
    # All bars start well after the opening window has already closed.
    late_start = SESSION_START_UTC + pd.Timedelta(hours=2)
    df = _build_df([100.0, 100.1, 100.2], start=late_start)
    atr_series = atr(df, period=5)

    result = compute_opening_range(df, atr_series, window_minutes=30)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.orh is None


def test_premarket_and_postmarket_bars_do_not_contaminate_the_opening_range():
    # F: extended-hours bars on the SAME trading day carry extreme prices that
    # would corrupt ORH/ORL if the session-window filter leaked — premarket
    # (well before 09:30 ET) is deliberately far below any window price, and
    # postmarket (well after the window closes) is deliberately far above.
    premarket = pd.DataFrame(
        {"open": 50.0, "high": 50.5, "low": 49.5, "close": 50.0, "volume": 10_000},
        index=pd.date_range(SESSION_START_UTC - pd.Timedelta(hours=5), periods=5, freq="5min"),
    )
    opening_window = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 50_000},
        index=pd.date_range(SESSION_START_UTC, periods=6, freq="5min"),  # 09:30-10:00 ET
    )
    postmarket = pd.DataFrame(
        {"open": 500.0, "high": 500.5, "low": 499.5, "close": 500.0, "volume": 10_000},
        index=pd.date_range(SESSION_START_UTC + pd.Timedelta(hours=6, minutes=35), periods=5, freq="5min"),
    )
    df = pd.concat([premarket, opening_window, postmarket]).sort_index()
    df.index.name = "ts"

    atr_series = atr(df, period=5)
    result = compute_opening_range(df, atr_series, window_minutes=30)

    assert result.orh == 100.5
    assert result.orl == 99.5
    assert result.orh < 500.0  # postmarket's extreme high never leaked in
    assert result.orl > 50.0  # premarket's extreme low never leaked in
    assert result.opening_volume == 50_000 * 6  # only the window's own volume


def test_opening_volume_is_an_integer_not_a_float():
    # Regression test: opening_range_events.opening_volume is a `bigint` column.
    # This previously computed opening_volume as a Python float (float(series.sum())),
    # which serializes to JSON as e.g. "1832485.0" — Postgres's bigint parser
    # rejects that exact text form even though the value is a whole number,
    # producing: APIError 22P02 "invalid input syntax for type bigint: '1832485.0'".
    # Reproduced against the real database before this fix; volume is a share
    # count and must be an int end to end.
    or_closes = [100.0, 100.5, 99.8, 100.2, 100.1, 100.3]
    df = _build_df(or_closes)
    atr_series = atr(df, period=5)

    result = compute_opening_range(df, atr_series, window_minutes=30)
    assert isinstance(result.opening_volume, int)
    assert not isinstance(result.opening_volume, bool)  # bool is an int subclass; guard against a stray True/False
    assert result.opening_volume == 50_000 * 6


def test_unsupported_window_minutes_rejected():
    import pytest

    from engine.market_state.opening_range import compute_opening_range as cor

    df = _build_df([100.0] * 6)
    atr_series = atr(df, period=5)
    with pytest.raises(ValueError):
        cor(df, atr_series, window_minutes=45)
