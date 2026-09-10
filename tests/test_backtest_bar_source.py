"""Phase 5.0 §2/§3 — HistoricalBarSource abstraction, provenance metadata,
and the hard synthetic/real + adjustment-status fail-closed guards."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from engine.backtest.bar_source import (
    AdjustmentStatus, DataProvenance, DataType, MixedProviderDataError, RunMode, SupabaseBarSource,
    SyntheticDataInProductionError, UnverifiedAdjustmentError, classify_provider_data_type,
    require_production_safe, require_verified_adjustment,
)

_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_END = datetime(2024, 1, 2, tzinfo=timezone.utc)


def _provenance(provider="alpaca", data_type=None, adjustment=AdjustmentStatus.UNKNOWN, bar_count=10):
    return DataProvenance(
        provider=provider, data_type=data_type or classify_provider_data_type(provider),
        adjustment_status=adjustment, symbol="SPY", timeframe="5min",
        range_start=_START, range_end=_END, bar_count=bar_count,
    )


# ---- provider -> data type classification ----


def test_synthetic_provider_classified_as_synthetic():
    assert classify_provider_data_type("synthetic") == DataType.SYNTHETIC_TEST_DATA


def test_alpaca_and_unknown_providers_classified_as_real():
    assert classify_provider_data_type("alpaca") == DataType.REAL_MARKET_DATA
    assert classify_provider_data_type("some_future_vendor") == DataType.REAL_MARKET_DATA  # fail-closed default


# ---- §3: synthetic/real isolation, fail-closed ----


def test_production_run_rejects_synthetic_data():
    prov = _provenance(provider="synthetic")
    with pytest.raises(SyntheticDataInProductionError):
        require_production_safe(prov, RunMode.PRODUCTION)


def test_test_synthetic_run_allows_synthetic_data():
    prov = _provenance(provider="synthetic")
    require_production_safe(prov, RunMode.TEST_SYNTHETIC)  # must not raise


def test_production_run_allows_real_data():
    prov = _provenance(provider="alpaca")
    require_production_safe(prov, RunMode.PRODUCTION)  # must not raise


def test_test_synthetic_run_also_allows_real_data():
    prov = _provenance(provider="alpaca")
    require_production_safe(prov, RunMode.TEST_SYNTHETIC)  # must not raise


# ---- §7: adjustment-status fail-closed ----


def test_unverified_adjustment_fails_closed_by_default():
    for status in (AdjustmentStatus.RAW, AdjustmentStatus.UNKNOWN):
        with pytest.raises(UnverifiedAdjustmentError):
            require_verified_adjustment(_provenance(adjustment=status))


def test_split_adjusted_passes_without_override():
    require_verified_adjustment(_provenance(adjustment=AdjustmentStatus.SPLIT_ADJUSTED))  # must not raise


def test_unverified_adjustment_passes_with_explicit_override():
    require_verified_adjustment(_provenance(adjustment=AdjustmentStatus.UNKNOWN), allow_unverified=True)


# ---- SupabaseBarSource: mixed-provider detection ----


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        return type("Res", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


def _row(ts, provider="alpaca", close=100.0):
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close, "volume": 1000, "provider": provider}


def test_supabase_bar_source_single_provider_returns_correct_provenance():
    rows = [_row("2024-01-02T09:30:00Z"), _row("2024-01-02T09:35:00Z")]
    source = SupabaseBarSource(_FakeClient(rows))
    result = source.fetch_range("SPY", "5min", _START, _END)
    assert result.provenance.provider == "alpaca"
    assert result.provenance.data_type == DataType.REAL_MARKET_DATA
    assert result.provenance.bar_count == 2
    assert "provider" not in result.df.columns  # provenance carries it, not the returned df


def test_supabase_bar_source_refuses_mixed_providers():
    rows = [_row("2024-01-02T09:30:00Z", provider="alpaca"), _row("2024-01-02T09:35:00Z", provider="synthetic")]
    source = SupabaseBarSource(_FakeClient(rows))
    with pytest.raises(MixedProviderDataError):
        source.fetch_range("SPY", "5min", _START, _END)


def test_supabase_bar_source_empty_range_returns_empty_series_not_error():
    source = SupabaseBarSource(_FakeClient([]))
    result = source.fetch_range("SPY", "5min", _START, _END)
    assert result.empty
    assert result.provenance.bar_count == 0
    assert result.provenance.provider == "NONE"
