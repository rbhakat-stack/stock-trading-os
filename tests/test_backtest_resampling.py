"""Phase 5.0 §5 — deterministic, session-aligned OHLCV resampling."""
import pandas as pd
import pytest

from engine.backtest.resampling import resample_ohlcv


def _rth_session(day: str, periods: int = 78, freq: str = "5min") -> pd.DataFrame:
    """One full RTH session's worth of 5-minute bars (09:30 local start)."""
    idx = pd.date_range(f"{day} 09:30", periods=periods, freq=freq, tz="America/New_York")
    n = len(idx)
    df = pd.DataFrame(
        {
            "open": range(100, 100 + n), "high": [x + 1 for x in range(100, 100 + n)],
            "low": [x - 1 for x in range(100, 100 + n)], "close": range(100, 100 + n),
            "volume": [1_000] * n,
        },
        index=idx,
    )
    df.index.name = "ts"
    return df.tz_convert("UTC")


def test_unsupported_target_timeframe_raises():
    with pytest.raises(ValueError):
        resample_ohlcv(_rth_session("2024-01-02"), "3min")


def test_empty_input_returns_empty_output():
    empty = _rth_session("2024-01-02").iloc[0:0]
    out = resample_ohlcv(empty, "15min")
    assert out.empty


def test_15min_divides_full_rth_session_evenly():
    df = _rth_session("2024-01-02")  # 78 5-min bars = exactly 390 minutes
    out = resample_ohlcv(df, "15min")
    assert len(out) == 26  # 390 / 15, no partial bucket


def test_1hour_produces_a_partial_final_bucket():
    df = _rth_session("2024-01-02")  # 390 minutes = 6 full hours + 30 minutes
    out = resample_ohlcv(df, "1hour")
    assert len(out) == 7
    # The final bucket's volume is only half of a full hour's worth (6 bars,
    # not 12) — proving it was computed from the real remaining bars, never
    # padded to a full bucket.
    full_bucket_volume = out["volume"].iloc[0]
    final_bucket_volume = out["volume"].iloc[-1]
    assert final_bucket_volume == full_bucket_volume / 2


def test_ohlcv_aggregation_rules_are_correct():
    df = _rth_session("2024-01-02")
    out = resample_ohlcv(df, "1hour")
    first_bucket = out.iloc[0]
    # First hour = first 12 five-minute bars (opens 100..111).
    assert first_bucket["open"] == df["open"].iloc[0]
    assert first_bucket["high"] == df["high"].iloc[0:12].max()
    assert first_bucket["low"] == df["low"].iloc[0:12].min()
    assert first_bucket["close"] == df["close"].iloc[11]
    assert first_bucket["volume"] == df["volume"].iloc[0:12].sum()


def test_daily_resample_aggregates_the_whole_session_into_one_bar():
    df = _rth_session("2024-01-02")
    out = resample_ohlcv(df, "1day")
    assert len(out) == 1
    assert out["open"].iloc[0] == df["open"].iloc[0]
    assert out["close"].iloc[0] == df["close"].iloc[-1]
    assert out["high"].iloc[0] == df["high"].max()
    assert out["low"].iloc[0] == df["low"].min()
    assert out["volume"].iloc[0] == df["volume"].sum()


def test_session_open_bucket_starts_exactly_at_market_open():
    df = _rth_session("2024-01-02")
    out = resample_ohlcv(df, "15min")
    first_ts_local = out.index[0].tz_convert("America/New_York")
    assert (first_ts_local.hour, first_ts_local.minute) == (9, 30)


def test_session_close_final_bucket_ends_by_session_close():
    df = _rth_session("2024-01-02")
    out = resample_ohlcv(df, "1hour")
    last_ts_local = out.index[-1].tz_convert("America/New_York")
    # Final bucket starts at 15:30 local (last full+partial hour before 16:00 close).
    assert (last_ts_local.hour, last_ts_local.minute) == (15, 30)


def test_multi_day_resample_keeps_sessions_independent():
    day1 = _rth_session("2024-01-02")
    day2 = _rth_session("2024-01-03")
    combined = pd.concat([day1, day2]).sort_index()
    out = resample_ohlcv(combined, "1hour")
    assert len(out) == 14  # 7 buckets/session x 2 sessions, no cross-session bleed
    # Each day's first bucket must independently start at 09:30 local, not
    # continue accumulating minutes from the previous day's final partial bucket.
    local_times = [ts.tz_convert("America/New_York") for ts in out.index]
    day_starts = [t for t in local_times if (t.hour, t.minute) == (9, 30)]
    assert len(day_starts) == 2


def test_dst_spring_forward_boundary_sessions_both_anchor_correctly():
    # 2024-03-08 (Fri, EST, UTC-5) and 2024-03-11 (Mon, EDT, UTC-4) bracket
    # the 2024-03-10 spring-forward transition.
    before = _rth_session("2024-03-08")
    after = _rth_session("2024-03-11")

    out_before = resample_ohlcv(before, "1hour")
    out_after = resample_ohlcv(after, "1hour")
    assert len(out_before) == 7
    assert len(out_after) == 7

    before_open_local = out_before.index[0].tz_convert("America/New_York")
    after_open_local = out_after.index[0].tz_convert("America/New_York")
    assert (before_open_local.hour, before_open_local.minute) == (9, 30)
    assert (after_open_local.hour, after_open_local.minute) == (9, 30)
    # The UTC offset itself must differ by exactly one hour across the transition.
    assert before_open_local.utcoffset() != after_open_local.utcoffset()


def test_output_index_matches_input_tz_awareness():
    df = _rth_session("2024-01-02")  # UTC-indexed, per _rth_session's own tz_convert
    out = resample_ohlcv(df, "15min")
    assert str(out.index.tz) == "UTC"
