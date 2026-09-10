"""Phase 5.1 (§9/§11) — isolated benchmark harness comparing REFERENCE vs
OPTIMIZED replay end-to-end, on synthetic RTH-shaped data only. Never
touches the live database, never imported by production code, and NOT part
of the pytest suite. Run manually:

    python scripts/benchmark_replay.py

This script MEASURES; it does not assert or gate CI. Reported in the
Phase 5.1 acceptance report.
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode  # noqa: E402
from engine.backtest.contracts import PlaybookPin  # noqa: E402
from engine.backtest.replay import run_replay  # noqa: E402

_PINS = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"),)
_WARMUP = 30


def _rth_days(n_days: int, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    cur = pd.Timestamp("2024-01-02", tz="America/New_York")
    got = 0
    while got < n_days:
        if cur.weekday() < 5:
            idx = pd.date_range(f"{cur.date()} 09:30", periods=78, freq="5min", tz="America/New_York")
            n = len(idx)
            close = 100 + rng.normal(0.01, 0.1, n).cumsum()
            openp = np.roll(close, 1)
            openp[0] = close[0]
            high = np.maximum(openp, close) + rng.uniform(0, 0.3, n)
            low = np.minimum(openp, close) - rng.uniform(0, 0.3, n)
            volume = rng.integers(1_000, 5_000, n)
            day_df = pd.DataFrame(
                {"open": openp, "high": high, "low": low, "close": close, "volume": volume}, index=idx,
            )
            frames.append(day_df.tz_convert("UTC"))
            got += 1
        cur += pd.Timedelta(days=1)
    return pd.concat(frames)


def _provenance(df: pd.DataFrame) -> DataProvenance:
    return DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="BENCH", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


def main() -> None:
    print(f"{'total_bars':>10}{'mode':>12}{'seconds':>12}{'bars/sec':>12}")
    for n_days in (7, 20):  # ~546 and ~1560 total bars — practical to run to completion
        df = _rth_days(n_days)
        prov = _provenance(df)

        for use_cache, label in ((False, "REFERENCE"), (True, "OPTIMIZED")):
            t0 = time.perf_counter()
            result = run_replay(
                df, "BENCH", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id=f"bench-{label}-{n_days}",
                higher_timeframes=("1hour",), warmup_bars=_WARMUP, enforce_session_integrity=False,
                allow_unverified_adjustment=True, use_resample_cache=use_cache,
            )
            elapsed = time.perf_counter() - t0
            rate = result.bars_evaluated / elapsed if elapsed > 0 else float("inf")
            print(f"{len(df):>10}{label:>12}{elapsed:>12.3f}{rate:>12.2f}")


if __name__ == "__main__":
    main()
