"""Phase 5.0 (§13/§14) — controlled historical-bar ingestion for a handful
of symbols over a specified date range. Run manually/server-side, never
through Streamlit; obtains its Supabase client exclusively via
repository.admin_repository.get_service_role_client_for_backtest_ingestion()
so the service-role key never reaches app/ or Streamlit session state.

Usage:
    python scripts/ingest_historical_bars.py SPY AAPL --timeframe 5min \\
        --start 2024-01-01 --end 2024-01-31 --provider synthetic

Deliberately NOT a full-universe backfill — see the module docstring in
engine/backtest/ingestion.py. Intended for a handful of symbols at a time,
not thousands.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from engine.backtest.ingestion import ingest_symbol_range  # noqa: E402
from repository.admin_repository import get_service_role_client_for_backtest_ingestion  # noqa: E402

_SUPPORTED_TIMEFRAMES = ("1min", "5min", "15min", "30min", "1hour", "1day")


def _get_provider(name: str):
    if name == "alpaca":
        from engine.data_provider.alpaca_provider import AlpacaProvider

        return AlpacaProvider()
    from engine.data_provider.synthetic_provider import SyntheticProvider

    return SyntheticProvider()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Symbols to ingest, e.g. SPY AAPL")
    parser.add_argument("--timeframe", default="5min", choices=_SUPPORTED_TIMEFRAMES)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (interpreted as UTC)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (interpreted as UTC)")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "alpaca"])
    args = parser.parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end <= start:
        parser.error("--end must be after --start")

    client = get_service_role_client_for_backtest_ingestion()
    provider = _get_provider(args.provider)

    # Phase 5.1P §Part A-2 — Phase 5 historical statistical validation policy:
    # real (Alpaca) ingestion always explicitly requests split-adjusted bars,
    # so historical splits never appear as artificial price discontinuities
    # to swing/trend/BOS/breakout/S-R logic. Synthetic data has no splits, so
    # it passes no adjustment at all (preserves its pre-existing UNKNOWN
    # provenance exactly).
    adjustment = "split" if args.provider == "alpaca" else None
    filter_rth = args.provider == "alpaca"

    exit_code = 0
    for symbol in args.symbols:
        result = ingest_symbol_range(
            client, provider, args.provider, symbol, args.timeframe, start, end,
            adjustment=adjustment, filter_regular_trading_hours=filter_rth,
        )
        status = "OK" if result.success else "FAILED"
        print(f"[{status}] {symbol}/{args.timeframe}: {result.bar_count} bars, {result.attempts} attempt(s)")
        for issue in result.data_quality_issues:
            print(f"    - {issue}")
        if result.error:
            print(f"    ERROR: {result.error}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
