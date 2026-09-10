"""Historical-bar-source abstraction (Phase 5.0 §2) + hard synthetic/real and
adjustment-status provenance enforcement (§3, §7).

Nothing in this module talks to Supabase directly except `SupabaseBarSource`,
which is one interchangeable implementation of `HistoricalBarSource` — future
replay/outcome/statistics code (Phase 5.1+, not built yet) must depend on the
Protocol, never on `SupabaseBarSource` or `repository.market_data` directly.
This is the seam that keeps "the final system may need historical validation
across thousands of tickers, and one Supabase `bars` table may not scale
forever" from ever forcing a rewrite of the engines built on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

import pandas as pd


class DataType(str, Enum):
    REAL_MARKET_DATA = "REAL_MARKET_DATA"
    SYNTHETIC_TEST_DATA = "SYNTHETIC_TEST_DATA"


class AdjustmentStatus(str, Enum):
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
    RAW = "RAW"
    UNKNOWN = "UNKNOWN"


class RunMode(str, Enum):
    """A run must EXPLICITLY declare which of these it is — never inferred
    from the data it happens to receive. See `require_production_safe`."""
    PRODUCTION = "PRODUCTION"
    TEST_SYNTHETIC = "TEST_SYNTHETIC"


# Providers this system currently knows how to originate. A provider NOT in
# this set is classified REAL_MARKET_DATA by default — fail-closed in the
# safer direction, so an unrecognized/future provider can never silently
# slip past the synthetic-data gate just because it isn't named "synthetic".
_KNOWN_SYNTHETIC_PROVIDERS = frozenset({"synthetic"})


def classify_provider_data_type(provider: str) -> DataType:
    return DataType.SYNTHETIC_TEST_DATA if provider in _KNOWN_SYNTHETIC_PROVIDERS else DataType.REAL_MARKET_DATA


@dataclass(frozen=True)
class DataProvenance:
    """Attached to every `BarSeries` a `HistoricalBarSource` returns — the
    provenance metadata §3/§7 require to exist on every historical bar
    request/ingestion batch: provider, data type, adjustment status, symbol,
    timeframe, and date range."""
    provider: str
    data_type: DataType
    adjustment_status: AdjustmentStatus
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    bar_count: int


@dataclass(frozen=True)
class BarSeries:
    df: pd.DataFrame
    provenance: DataProvenance

    @property
    def empty(self) -> bool:
        return self.df.empty


class SyntheticDataInProductionError(RuntimeError):
    """§3 — raised when a PRODUCTION run would otherwise silently include
    synthetic test data."""


class UnverifiedAdjustmentError(RuntimeError):
    """§7 — raised when a caller requires verified split-adjusted data but
    the provenance says RAW or UNKNOWN."""


class MixedProviderDataError(RuntimeError):
    """§3 — raised when stored bars for one symbol/timeframe/range came from
    more than one provider; this must never be silently blended."""


def require_production_safe(provenance: DataProvenance, run_mode: RunMode) -> None:
    """Fail-closed gate (§3): a PRODUCTION run must never silently include
    synthetic data. A TEST_SYNTHETIC run explicitly declares that's fine —
    that declaration is made by the CALLER (the run itself), never inferred
    from whatever data happened to come back."""
    if run_mode == RunMode.PRODUCTION and provenance.data_type == DataType.SYNTHETIC_TEST_DATA:
        raise SyntheticDataInProductionError(
            f"{provenance.symbol}/{provenance.timeframe}: provider {provenance.provider!r} is synthetic "
            "test data, refused for a PRODUCTION run. Pass run_mode=RunMode.TEST_SYNTHETIC if this is "
            "intentional (e.g. exercising the replay engine itself, not validating real historical edge)."
        )


def require_verified_adjustment(provenance: DataProvenance, allow_unverified: bool = False) -> None:
    """§7 — a production backtest must fail closed on RAW/UNKNOWN adjustment
    status unless the caller explicitly opts in (e.g. a symbol/range already
    confirmed split-free out of band). Neither current provider
    (SyntheticProvider, AlpacaProvider) reports adjustment status, so this
    defaults to UNKNOWN for everything ingested through them today — that is
    the honest answer, not a gap to silently paper over."""
    if not allow_unverified and provenance.adjustment_status != AdjustmentStatus.SPLIT_ADJUSTED:
        raise UnverifiedAdjustmentError(
            f"{provenance.symbol}/{provenance.timeframe} {provenance.range_start}..{provenance.range_end}: "
            f"adjustment_status={provenance.adjustment_status.value}, not verified SPLIT_ADJUSTED. Pass "
            "allow_unverified=True only once this symbol/range is confirmed free of unadjusted splits."
        )


class HistoricalBarSource(Protocol):
    """The seam Phase 5.1+ replay/outcome/statistics code must depend on —
    never Supabase directly (§2)."""

    def fetch_range(self, symbol: str, timeframe: str, start_ts: datetime, end_ts: datetime) -> BarSeries: ...


class SupabaseBarSource:
    """First concrete implementation (§2: "MAY use the existing Supabase
    bars table"). Detects and REFUSES (`MixedProviderDataError`) a
    symbol/timeframe/range whose stored bars come from more than one
    provider — never silently blends synthetic and real data (§3).

    `adjustment_status` defaults to UNKNOWN because neither `SyntheticProvider`
    nor `AlpacaProvider` currently reports split-adjustment status — pass an
    explicit override only once a symbol/range's adjustment status has been
    verified out of band (e.g. confirmed no split occurred in this window).
    """

    def __init__(self, client, adjustment_status: AdjustmentStatus = AdjustmentStatus.UNKNOWN):
        self._client = client
        self._adjustment_status = adjustment_status

    def fetch_range(self, symbol: str, timeframe: str, start_ts: datetime, end_ts: datetime) -> BarSeries:
        from repository.market_data import fetch_bars_range  # late import — keep this module Supabase-import-free

        df = fetch_bars_range(self._client, symbol, timeframe, start_ts, end_ts)
        if df.empty:
            provenance = DataProvenance(
                provider="NONE", data_type=DataType.REAL_MARKET_DATA, adjustment_status=self._adjustment_status,
                symbol=symbol, timeframe=timeframe, range_start=start_ts, range_end=end_ts, bar_count=0,
            )
            return BarSeries(df=df.drop(columns=["provider"], errors="ignore"), provenance=provenance)

        providers = sorted(df["provider"].unique().tolist())
        if len(providers) > 1:
            raise MixedProviderDataError(
                f"{symbol}/{timeframe} {start_ts}..{end_ts}: bars come from multiple providers {providers} "
                "— refusing to silently blend them. Ingest/backtest one provider at a time for this "
                "symbol/timeframe/range."
            )
        provider = providers[0]
        provenance = DataProvenance(
            provider=provider, data_type=classify_provider_data_type(provider),
            adjustment_status=self._adjustment_status, symbol=symbol, timeframe=timeframe,
            range_start=start_ts, range_end=end_ts, bar_count=len(df),
        )
        return BarSeries(df=df.drop(columns=["provider"]), provenance=provenance)
