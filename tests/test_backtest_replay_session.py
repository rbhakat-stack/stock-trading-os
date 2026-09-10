"""Phase 5.1 §16-K/L — session/timezone/DST handling and fail-closed
half-day behavior."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_rth_days

from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode
from engine.backtest.contracts import PlaybookPin
from engine.backtest.replay import check_session_integrity, exclude_flagged_sessions, run_replay

_PINS = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"),)


def _real_provenance(df, adjustment=AdjustmentStatus.SPLIT_ADJUSTED):
    return DataProvenance(
        provider="alpaca", data_type=DataType.REAL_MARKET_DATA, adjustment_status=adjustment,
        symbol="SPY", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


# ---- K: session / timezone / DST ----


def test_normal_full_session_has_no_integrity_issue():
    df = make_rth_days(3)
    issues = check_session_integrity(df, base_timeframe_minutes=5)
    assert issues == []


def test_dst_spring_forward_boundary_sessions_are_both_normal():
    # 2024-03-08 (Fri, EST) and 2024-03-11 (Mon, EDT) bracket the transition.
    idx1 = pd.date_range("2024-03-08 09:30", periods=78, freq="5min", tz="America/New_York")
    idx2 = pd.date_range("2024-03-11 09:30", periods=78, freq="5min", tz="America/New_York")
    rng = np.random.default_rng(1)

    def _bars(idx):
        n = len(idx)
        close = 100 + rng.normal(0, 0.1, n).cumsum()
        return pd.DataFrame(
            {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": [1000] * n},
            index=idx,
        )

    df = pd.concat([_bars(idx1), _bars(idx2)]).tz_convert("UTC")
    issues = check_session_integrity(df, base_timeframe_minutes=5)
    assert issues == []  # neither side of the DST transition is flagged as anomalous


def test_replay_runs_cleanly_across_a_dst_boundary():
    idx1 = pd.date_range("2024-03-08 09:30", periods=78, freq="5min", tz="America/New_York")
    idx2 = pd.date_range("2024-03-11 09:30", periods=78, freq="5min", tz="America/New_York")
    rng = np.random.default_rng(1)

    def _bars(idx):
        n = len(idx)
        close = 100 + rng.normal(0.02, 0.1, n).cumsum()
        openp = np.roll(close, 1); openp[0] = close[0]
        high = np.maximum(openp, close) + 0.3
        low = np.minimum(openp, close) - 0.3
        return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": [1500] * n}, index=idx)

    df = pd.concat([_bars(idx1), _bars(idx2)]).tz_convert("UTC")
    prov = DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="SPY", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )
    result = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="dst-test",
        higher_timeframes=("1hour",), warmup_bars=20, enforce_session_integrity=False,
        allow_unverified_adjustment=True,
    )
    assert result.bars_evaluated > 0  # completed without error across the transition


# ---- L: half-day fail-closed behavior ----


def test_early_close_session_is_flagged_as_an_integrity_issue():
    # A session that ends at 13:00 instead of 16:00 (e.g. a genuine half day,
    # OR indistinguishably, severe missing end-of-day data) — either way,
    # must be flagged, never silently treated as a normal full session.
    normal_day = make_rth_days(1, start="2024-01-02")
    half_idx = pd.date_range("2024-01-03 09:30", periods=42, freq="5min", tz="America/New_York")  # ends 13:00
    n = len(half_idx)
    half_day = pd.DataFrame(
        {"open": [100] * n, "high": [101] * n, "low": [99] * n, "close": [100] * n, "volume": [1000] * n},
        index=half_idx,
    ).tz_convert("UTC")
    df = pd.concat([normal_day, half_day])

    issues = check_session_integrity(df, base_timeframe_minutes=5)
    assert len(issues) == 1
    assert issues[0].session_date == date(2024, 1, 3)
    assert issues[0].minutes_before_scheduled_close >= 150  # roughly 3 hours early


def test_flagged_session_is_excluded_not_fabricated():
    normal_day = make_rth_days(1, start="2024-01-02")
    half_idx = pd.date_range("2024-01-03 09:30", periods=42, freq="5min", tz="America/New_York")
    n = len(half_idx)
    half_day = pd.DataFrame(
        {"open": [100] * n, "high": [101] * n, "low": [99] * n, "close": [100] * n, "volume": [1000] * n},
        index=half_idx,
    ).tz_convert("UTC")
    df = pd.concat([normal_day, half_day])

    issues = check_session_integrity(df, base_timeframe_minutes=5)
    excluded = exclude_flagged_sessions(df, issues)
    remaining_dates = set(excluded.tz_convert("America/New_York").index.date)
    assert date(2024, 1, 3) not in remaining_dates  # excluded entirely
    assert date(2024, 1, 2) in remaining_dates       # the normal day is untouched
    assert len(excluded) == len(normal_day)          # exactly the half-day's bars are gone, nothing fabricated


def test_run_replay_excludes_flagged_sessions_by_default_for_real_data():
    normal_day = make_rth_days(1, start="2024-01-02")
    half_idx = pd.date_range("2024-01-03 09:30", periods=42, freq="5min", tz="America/New_York")
    n = len(half_idx)
    rng = np.random.default_rng(3)
    close = 100 + rng.normal(0, 0.1, n).cumsum()
    half_day = pd.DataFrame(
        {"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": [1000] * n},
        index=half_idx,
    ).tz_convert("UTC")
    df = pd.concat([normal_day, half_day])

    result = run_replay(
        df, "SPY", "5min", _real_provenance(df), RunMode.PRODUCTION, _PINS, run_id="half-day-test",
        warmup_bars=20, enforce_session_integrity=True,
    )
    assert len(result.excluded_sessions) == 1
    assert result.excluded_sessions[0].session_date == date(2024, 1, 3)
    # Every recorded evaluation timestamp must come from the CLEAN day only.
    for e in result.evaluations:
        assert e.timestamp.tz_convert("America/New_York").date() == date(2024, 1, 2)


def test_session_integrity_check_is_skipped_for_synthetic_data():
    # SyntheticProvider-shaped data (continuous, non-RTH) would otherwise be
    # flagged as "every session is anomalous" — the check must not even run
    # for synthetic data, since it isn't session-shaped to begin with.
    idx = pd.date_range("2024-01-02", periods=500, freq="5min", tz="UTC")  # continuous, not RTH-bounded
    n = len(idx)
    rng = np.random.default_rng(4)
    close = 100 + rng.normal(0, 0.1, n).cumsum()
    df = pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": [1000] * n}, index=idx)
    prov = DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="SPY", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )
    result = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="synthetic-skip-test",
        warmup_bars=20, enforce_session_integrity=True, allow_unverified_adjustment=True,
    )
    assert result.excluded_sessions == ()  # never flagged, since REAL_MARKET_DATA-only check was skipped


def test_calendar_is_never_fabricated_full_close_for_a_flagged_session():
    # Documentation-level proof that half-days are genuinely not modeled —
    # confirms the underlying calendar contract this whole mechanism relies on.
    from engine.backtest.calendar import session_bounds

    bounds = session_bounds(date(2024, 11, 29))  # day after Thanksgiving 2024 — a REAL historical half day
    assert bounds is not None  # it IS a trading day
    assert bounds.is_full_session_confirmed is False  # but its close time is NOT verified/fabricated
    assert bounds.close_at.hour == 16  # the (unconfirmed, possibly wrong) default — exactly why §8's check exists
