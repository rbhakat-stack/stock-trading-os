"""Phase 5.0 (§12) — isolated benchmark harness for build_snapshot()'s
performance characteristics, measured on SYNTHETIC data only. Never touches
the live database, never touches any real market data, never imported by
any production code path, and NOT part of the pytest suite (a benchmark
shouldn't run — and shouldn't be able to fail — on every test invocation).

Run manually:
    python scripts/benchmark_market_intelligence.py

This script MEASURES; it does not assert, does not gate CI, and does not
change any Phase 2 behavior. Results are reported in the Phase 5.0
acceptance report, not enforced here.
"""
from __future__ import annotations

import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.data_provider.synthetic_provider import SyntheticProvider  # noqa: E402
from engine.market_state.market_intelligence import build_snapshot  # noqa: E402

_TIMEFRAME_MINUTES = {"5min": 5, "15min": 15, "1day": 1440}
_BAR_COUNTS = (500, 2_000, 10_000)


def _lookback_days(timeframe: str, bar_count: int) -> int:
    """How many calendar days of SyntheticProvider output (its bars are
    continuous, not RTH-gated) are needed to yield at least `bar_count` bars
    at `timeframe`."""
    minutes_per_day = 24 * 60
    step = _TIMEFRAME_MINUTES[timeframe]
    return max(1, (bar_count * step) // minutes_per_day + 2)


def main() -> None:
    provider = SyntheticProvider()
    end = datetime.now(timezone.utc)

    print(f"{'timeframe':<10}{'bars':>8}{'seconds':>12}{'bars/sec':>12}")
    for timeframe, step in _TIMEFRAME_MINUTES.items():
        for target_bars in _BAR_COUNTS:
            start = end - timedelta(days=_lookback_days(timeframe, target_bars))
            df = provider.get_ohlcv("BENCH", timeframe, start, end)
            if len(df) > target_bars:
                df = df.iloc[-target_bars:]
            if df.empty:
                print(f"{timeframe:<10}{'0':>8}{'skipped (no bars)':>24}")
                continue

            t0 = time.perf_counter()
            build_snapshot(
                symbol="BENCH", timeframe=timeframe, df=df, data_source="synthetic",
                timeframe_minutes=step, now=df.index[-1],
            )
            elapsed = time.perf_counter() - t0

            rate = len(df) / elapsed if elapsed > 0 else float("inf")
            print(f"{timeframe:<10}{len(df):>8}{elapsed:>12.4f}{rate:>12.1f}")


if __name__ == "__main__":
    main()
