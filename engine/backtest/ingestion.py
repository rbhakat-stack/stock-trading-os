"""Small, explicitly-scoped historical-bar ingestion path (Phase 5.0 §13).

Deliberately NOT a full-universe backfill pipeline — this validates Phase
5.0's abstractions against a handful of real symbols over a specified date
range, nothing more. See scripts/ingest_historical_bars.py for the intended,
service-role-isolated operational entry point (§14) — this module itself
takes a plain, duck-typed `client` so it works identically whether called
with a service-role client (recommended for bulk ingestion) or an ordinary
authenticated one.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from engine.data_integrity.checks import check_bars, has_failure

from .bar_source import AdjustmentStatus, DataProvenance, classify_provider_data_type

logger = logging.getLogger("trading_os.backtest.ingestion")

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.0
_TIMEFRAME_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1day": 1440}


@dataclass(frozen=True)
class IngestionResult:
    symbol: str
    timeframe: str
    provider_name: str
    requested_start: datetime
    requested_end: datetime
    success: bool
    bar_count: int
    provenance: DataProvenance | None
    data_quality_issues: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None
    attempts: int = 0


def ingest_symbol_range(
    client,
    provider,
    provider_name: str,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> IngestionResult:
    """Fetches `[start, end)` bars for one symbol/timeframe from `provider`
    (an engine.data_provider-shaped object — SyntheticProvider or
    AlpacaProvider both work identically here, this function never
    constructs one itself), validates them with the SAME
    engine.data_integrity.checks.check_bars gate every live Analyze call
    already uses, and idempotently upserts via repository.market_data
    (`upsert_bars`'s existing `on_conflict=symbol,timeframe,ts` already
    makes re-running this over an overlapping range safe).

    Retries ONLY on exceptions the provider call itself raises (network/
    transient failures) — a bounded number of attempts with linear backoff,
    never silent, never infinite. A data-quality FAILURE (bad ticks, etc.)
    is NOT retried: retrying a provider call cannot fix malformed data the
    provider already returned, so this fails closed on the first such
    result instead of retrying a call doomed to fail the same way again.

    `adjustment_status` is always stamped UNKNOWN (§7) — neither current
    provider reports split-adjustment, and this function must not pretend
    otherwise.
    """
    if timeframe not in _TIMEFRAME_MINUTES:
        return IngestionResult(
            symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
            requested_end=end, success=False, bar_count=0, provenance=None,
            error=f"unsupported timeframe: {timeframe!r}", attempts=0,
        )
    if end <= start:
        return IngestionResult(
            symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
            requested_end=end, success=False, bar_count=0, provenance=None,
            error="end must be after start", attempts=0,
        )

    df = None
    last_error: str | None = None
    attempts = 0
    for attempt in range(1, max_retries + 1):
        attempts = attempt
        try:
            df = provider.get_ohlcv(symbol, timeframe, start, end)
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 - any provider-call failure is retryable, logged, never silent
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "ingest_symbol_range: attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, symbol, timeframe, last_error,
            )
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)

    if df is None:
        return IngestionResult(
            symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
            requested_end=end, success=False, bar_count=0, provenance=None,
            error=f"provider call failed after {attempts} attempt(s): {last_error}", attempts=attempts,
        )

    if df.empty:
        return IngestionResult(
            symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
            requested_end=end, success=True, bar_count=0,
            provenance=DataProvenance(
                provider=provider_name, data_type=classify_provider_data_type(provider_name),
                adjustment_status=AdjustmentStatus.UNKNOWN, symbol=symbol, timeframe=timeframe,
                range_start=start, range_end=end, bar_count=0,
            ),
            attempts=attempts,
        )

    issues = check_bars(df, timeframe_minutes=_TIMEFRAME_MINUTES[timeframe])
    if has_failure(issues):
        failure_codes = tuple(f"{i.code}: {i.message}" for i in issues if i.severity == "FAILURE")
        return IngestionResult(
            symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
            requested_end=end, success=False, bar_count=len(df), provenance=None,
            data_quality_issues=failure_codes, error="data quality FAILURE — refused to ingest", attempts=attempts,
        )
    warning_codes = tuple(f"{i.code}: {i.message}" for i in issues if i.severity == "WARNING")

    from repository.market_data import upsert_bars, upsert_symbol  # late import — keeps this module import-light when unused

    upsert_symbol(client, symbol, name=None, exchange=None)
    upsert_bars(client, symbol, timeframe, df, provider=provider_name)

    provenance = DataProvenance(
        provider=provider_name, data_type=classify_provider_data_type(provider_name),
        adjustment_status=AdjustmentStatus.UNKNOWN, symbol=symbol, timeframe=timeframe,
        range_start=start, range_end=end, bar_count=len(df),
    )
    return IngestionResult(
        symbol=symbol, timeframe=timeframe, provider_name=provider_name, requested_start=start,
        requested_end=end, success=True, bar_count=len(df), provenance=provenance,
        data_quality_issues=warning_codes, attempts=attempts,
    )
