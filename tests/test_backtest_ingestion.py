"""Phase 5.0 §13 — controlled historical-bar ingestion: retry/backoff,
idempotent upsert, data-integrity validation, clear error reporting."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from engine.backtest.bar_source import AdjustmentStatus, DataType
from engine.backtest.ingestion import ingest_symbol_range

_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
_END = datetime(2024, 1, 3, tzinfo=timezone.utc)


def _good_df(n=10):
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n, "close": [100.5] * n, "volume": [1000] * n},
        index=idx,
    )


class _FakeProvider:
    def __init__(self, df=None, fail_times=0, exc=RuntimeError("boom")):
        self._df = df
        self._fail_times = fail_times
        self._exc = exc
        self.calls = 0

    def get_ohlcv(self, symbol, timeframe, start, end):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return self._df


class _FakeTable:
    def __init__(self, captured, name):
        self._captured = captured
        self._name = name

    def upsert(self, rows, on_conflict=None):
        self._captured.setdefault(self._name, []).append(rows)
        return self

    def execute(self):
        return None


class _FakeClient:
    def __init__(self):
        self.captured = {}

    def table(self, name):
        return _FakeTable(self.captured, name)


def test_successful_ingestion_upserts_and_returns_provenance():
    client = _FakeClient()
    provider = _FakeProvider(df=_good_df())
    result = ingest_symbol_range(client, provider, "synthetic", "SPY", "5min", _START, _END)

    assert result.success is True
    assert result.bar_count == 10
    assert result.attempts == 1
    assert result.provenance.provider == "synthetic"
    assert result.provenance.data_type == DataType.SYNTHETIC_TEST_DATA
    assert result.provenance.adjustment_status == AdjustmentStatus.UNKNOWN  # §7 — never fabricated as verified
    assert "bars" in client.captured  # upsert_bars was actually called


def test_retries_on_transient_provider_exception_then_succeeds():
    client = _FakeClient()
    provider = _FakeProvider(df=_good_df(), fail_times=2)
    result = ingest_symbol_range(client, provider, "alpaca", "SPY", "5min", _START, _END, backoff_seconds=0.0)

    assert result.success is True
    assert result.attempts == 3
    assert provider.calls == 3


def test_exhausting_retries_reports_a_clear_failure_never_raises():
    client = _FakeClient()
    provider = _FakeProvider(fail_times=99)  # always fails
    result = ingest_symbol_range(
        client, provider, "alpaca", "SPY", "5min", _START, _END, max_retries=2, backoff_seconds=0.0,
    )

    assert result.success is False
    assert result.attempts == 2
    assert "provider call failed after 2 attempt(s)" in result.error
    assert "bars" not in client.captured  # never upserts on total failure


def test_data_quality_failure_is_not_retried_and_is_not_ingested():
    bad = _good_df()
    bad.loc[bad.index[0], "high"] = -1.0  # non-positive price -> FAILURE, not WARNING
    client = _FakeClient()
    provider = _FakeProvider(df=bad)
    result = ingest_symbol_range(client, provider, "alpaca", "SPY", "5min", _START, _END)

    assert result.success is False
    assert result.attempts == 1  # never retried — retrying can't fix bad data the provider already returned
    assert any("NON_POSITIVE_PRICE" in issue for issue in result.data_quality_issues)
    assert "bars" not in client.captured


def test_empty_provider_result_is_a_success_with_zero_bars():
    client = _FakeClient()
    provider = _FakeProvider(df=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    result = ingest_symbol_range(client, provider, "alpaca", "SPY", "5min", _START, _END)

    assert result.success is True
    assert result.bar_count == 0
    assert "bars" not in client.captured  # nothing to upsert


def test_unsupported_timeframe_fails_closed_without_calling_provider():
    client = _FakeClient()
    provider = _FakeProvider(df=_good_df())
    result = ingest_symbol_range(client, provider, "alpaca", "SPY", "3min", _START, _END)

    assert result.success is False
    assert provider.calls == 0
    assert "unsupported timeframe" in result.error


def test_end_before_start_fails_closed():
    client = _FakeClient()
    provider = _FakeProvider(df=_good_df())
    result = ingest_symbol_range(client, provider, "alpaca", "SPY", "5min", _END, _START)

    assert result.success is False
    assert provider.calls == 0


def test_idempotent_reingestion_calls_upsert_again_not_insert():
    # upsert_bars itself already guarantees idempotency via
    # on_conflict=symbol,timeframe,ts (existing, unmodified behavior) — this
    # proves ingest_symbol_range always routes through THAT function (never
    # a raw .insert()) so re-running over an overlapping range is safe.
    client = _FakeClient()
    provider = _FakeProvider(df=_good_df())
    ingest_symbol_range(client, provider, "synthetic", "SPY", "5min", _START, _END)
    ingest_symbol_range(client, provider, "synthetic", "SPY", "5min", _START, _END)
    assert len(client.captured["bars"]) == 2  # two upsert calls, never a plain insert
