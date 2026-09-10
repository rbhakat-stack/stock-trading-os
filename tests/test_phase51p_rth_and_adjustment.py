"""Phase 5.1P Part A — RTH normalization + split-adjustment + honest
provenance at the historical ingestion boundary.

Proves: Alpaca-shaped raw response (extended-hours-inclusive) -> historical
normalization (engine.backtest.calendar.filter_to_regular_trading_hours) ->
canonical Phase 5 bar series contains ONLY eligible RTH bars, and that
adjustment_status is stamped from what the caller explicitly declared, never
inferred from provider_name.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from engine.backtest.bar_source import AdjustmentStatus, DataType
from engine.backtest.calendar import MARKET_TZ, filter_to_regular_trading_hours
from engine.backtest.ingestion import ingest_symbol_range


def _extended_hours_day(date_str: str, seed: int = 1) -> pd.DataFrame:
    """One session's worth of 5-min bars from 04:00 to 20:00 America/New_York
    (pre-market + RTH + after-hours) — mirrors what real Alpaca historical
    bars actually contain (verified against the real API)."""
    idx = pd.date_range(f"{date_str} 04:00", f"{date_str} 19:55", freq="5min", tz=MARKET_TZ)
    rng = np.random.default_rng(seed)
    n = len(idx)
    close = 100 + rng.normal(0, 0.1, n).cumsum()
    openp = np.roll(close, 1)
    openp[0] = close[0]
    high = np.maximum(openp, close) + 0.05
    low = np.minimum(openp, close) - 0.05
    volume = rng.integers(1000, 5000, n)
    df = pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    return df.tz_convert("UTC")


class _FakeProviderWithAdjustment:
    """Test double whose get_ohlcv accepts `adjustment` (Alpaca/Synthetic
    Protocol shape) and returns extended-hours-inclusive real-session data."""

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.calls = 0
        self.last_adjustment = "UNSET"

    def get_ohlcv(self, symbol, timeframe, start, end, adjustment=None):
        self.calls += 1
        self.last_adjustment = adjustment
        return self._df


class _FakeClient:
    def __init__(self):
        self.captured = {}

    def table(self, name):
        return _FakeTable(self.captured, name)


class _FakeTable:
    def __init__(self, captured, name):
        self._captured = captured
        self._name = name

    def upsert(self, rows, on_conflict=None):
        self._captured.setdefault(self._name, []).append(rows)
        return self

    def execute(self):
        return None


_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
_END = datetime(2024, 1, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# §Part A-1 — filter_to_regular_trading_hours (the utility itself)
# ---------------------------------------------------------------------------


def test_full_session_with_extended_hours_filters_to_exactly_78_bars():
    df = _extended_hours_day("2024-01-02")  # a real Tuesday, not a holiday
    filtered = filter_to_regular_trading_hours(df)
    assert len(filtered) == 78


def test_filtered_bars_are_all_within_0930_1600_ny():
    df = _extended_hours_day("2024-01-02")
    filtered = filter_to_regular_trading_hours(df)
    local = filtered.index.tz_convert(MARKET_TZ)
    assert (local.time >= pd.Timestamp("09:30").time()).all()
    assert (local.time < pd.Timestamp("16:00").time()).all()


def test_extended_hours_bars_are_actually_dropped_not_kept():
    df = _extended_hours_day("2024-01-02")
    filtered = filter_to_regular_trading_hours(df)
    assert len(filtered) < len(df)  # pre/after-market bars existed and were removed
    dropped = df.index.difference(filtered.index)
    assert len(dropped) == len(df) - 78


def test_a_bar_on_a_market_holiday_is_dropped_entirely():
    # 2024-01-01 is New Year's Day (NYSE holiday) — session_bounds() returns
    # None for it, so every bar on it must be excluded, not just time-filtered.
    df = _extended_hours_day("2024-01-01")
    filtered = filter_to_regular_trading_hours(df)
    assert filtered.empty


def test_weekend_bars_are_dropped_entirely():
    df = _extended_hours_day("2024-01-06")  # a Saturday
    filtered = filter_to_regular_trading_hours(df)
    assert filtered.empty


def test_empty_input_returns_empty_without_error():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    empty.index = pd.DatetimeIndex([], tz="UTC")
    result = filter_to_regular_trading_hours(empty)
    assert result.empty


def test_multi_day_extended_hours_series_filters_each_day_independently():
    df = pd.concat([_extended_hours_day("2024-01-02", seed=1), _extended_hours_day("2024-01-03", seed=2)])
    filtered = filter_to_regular_trading_hours(df)
    assert len(filtered) == 78 * 2
    local = filtered.index.tz_convert(MARKET_TZ)
    for d in sorted(set(local.date)):
        assert (local.date == d).sum() == 78


# ---------------------------------------------------------------------------
# §Part A-1 — wired into ingest_symbol_range (the historical ingestion boundary)
# ---------------------------------------------------------------------------


def test_ingestion_strips_extended_hours_when_opted_in():
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "5min", _START, _END, filter_regular_trading_hours=True,
    )
    assert result.success is True
    assert result.bar_count == 78
    assert result.provenance.bar_count == 78


def test_ingestion_keeps_extended_hours_by_default_backward_compatible():
    # filter_regular_trading_hours defaults to False — pre-Phase-5.1P behavior
    # is exactly preserved for any caller that doesn't explicitly opt in.
    client = _FakeClient()
    raw_df = _extended_hours_day("2024-01-02")
    provider = _FakeProviderWithAdjustment(raw_df)
    result = ingest_symbol_range(client, provider, "alpaca", "AAPL", "5min", _START, _END)
    assert result.success is True
    assert result.bar_count == len(raw_df)  # nothing stripped


def test_ingestion_rth_filter_is_a_noop_for_daily_bars():
    client = _FakeClient()
    daily_df = _extended_hours_day("2024-01-02").iloc[[0]]  # pretend this is "a daily bar"
    provider = _FakeProviderWithAdjustment(daily_df)
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "1day", _START, _END, filter_regular_trading_hours=True,
    )
    assert result.bar_count == 1  # not stripped, since 1day is exempt regardless of the flag


def test_rth_filter_producing_an_empty_result_is_a_clean_success_not_a_crash():
    client = _FakeClient()
    # every bar is pre-market only -> filtered to nothing
    idx = pd.date_range("2024-01-02 04:00", "2024-01-02 09:00", freq="5min", tz=MARKET_TZ).tz_convert("UTC")
    n = len(idx)
    df = pd.DataFrame(
        {"open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n, "close": [1.0] * n, "volume": [100] * n}, index=idx,
    )
    provider = _FakeProviderWithAdjustment(df)
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "5min", _START, _END, filter_regular_trading_hours=True,
    )
    assert result.success is True
    assert result.bar_count == 0
    assert "bars" not in client.captured  # nothing to upsert


# ---------------------------------------------------------------------------
# §Part A-2 — adjustment pass-through + honest, non-inferred provenance
# ---------------------------------------------------------------------------


def test_adjustment_none_by_default_preserves_pre_phase51p_unknown_provenance():
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(client, provider, "alpaca", "AAPL", "5min", _START, _END)
    assert provider.last_adjustment is None  # never sent to the provider unless explicitly requested
    assert result.provenance.adjustment_status == AdjustmentStatus.UNKNOWN


def test_adjustment_split_is_explicitly_requested_and_stamped_split_adjusted():
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "5min", _START, _END, adjustment="split",
    )
    assert provider.last_adjustment == "split"
    assert result.provenance.adjustment_status == AdjustmentStatus.SPLIT_ADJUSTED


def test_adjustment_raw_is_stamped_raw_not_unknown():
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "5min", _START, _END, adjustment="raw",
    )
    assert result.provenance.adjustment_status == AdjustmentStatus.RAW


def test_provenance_is_never_inferred_from_provider_name_alone():
    # A provider literally named "alpaca" that was never asked for split
    # adjustment must NOT be stamped SPLIT_ADJUSTED just because of its name.
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(client, provider, "alpaca", "AAPL", "5min", _START, _END)
    assert result.provenance.adjustment_status != AdjustmentStatus.SPLIT_ADJUSTED
    assert result.provenance.data_type == DataType.REAL_MARKET_DATA  # name still drives DATA_TYPE (§3, unchanged)


def test_unrecognized_adjustment_value_stays_unknown_fail_closed():
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(
        client, provider, "alpaca", "AAPL", "5min", _START, _END, adjustment="dividend",
    )
    assert result.provenance.adjustment_status == AdjustmentStatus.UNKNOWN


def test_synthetic_ingestion_default_behavior_completely_unaffected():
    # Regression guard mirroring the original Phase 5.0 assertion — synthetic
    # ingestion with no new kwargs used must behave byte-identical to before.
    client = _FakeClient()
    provider = _FakeProviderWithAdjustment(_extended_hours_day("2024-01-02"))
    result = ingest_symbol_range(client, provider, "synthetic", "SPY", "5min", _START, _END)
    assert result.provenance.provider == "synthetic"
    assert result.provenance.data_type == DataType.SYNTHETIC_TEST_DATA
    assert result.provenance.adjustment_status == AdjustmentStatus.UNKNOWN


def test_legacy_four_arg_provider_still_works_when_adjustment_not_requested():
    """A provider whose get_ohlcv does NOT accept `adjustment` at all (the
    exact shape of every pre-Phase-5.1P test double) must keep working
    exactly as before when the caller never passes adjustment=."""

    class _LegacyProvider:
        def get_ohlcv(self, symbol, timeframe, start, end):
            return _extended_hours_day("2024-01-02")

    client = _FakeClient()
    result = ingest_symbol_range(client, _LegacyProvider(), "alpaca", "AAPL", "5min", _START, _END)
    assert result.success is True
    with pytest.raises(TypeError):
        # Confirms this double really is legacy-shaped (proves the test above
        # is meaningful, not accidentally passing because the double secretly
        # accepts adjustment=).
        _LegacyProvider().get_ohlcv("AAPL", "5min", _START, _END, adjustment="split")
