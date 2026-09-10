"""Phase 5.1P §Part B item 11 — REFERENCE vs OPTIMIZED (market-state)
end-to-end replay benchmark. Never touches the live database, not part of
the pytest suite. Run manually:

    python scripts/benchmark_replay_5_1p.py [--skip-large-reference]

REFERENCE is `use_optimized_market_state=False` (byte-identical to Phase 5.1
— the untouched golden path). OPTIMIZED is `use_optimized_market_state=True`
(this round's `enrich_zones_fast`/`compute_rvol_fast`/atr-passthrough).
`--skip-large-reference` skips the 5,000/10,000-bar REFERENCE runs, which
the reference path's own (unchanged, pre-existing) super-linear cost makes
impractical at that scale — OPTIMIZED still runs at every size.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode  # noqa: E402
from engine.backtest.contracts import PlaybookPin  # noqa: E402
from engine.backtest.replay import run_replay  # noqa: E402

_PINS = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"))
_WARMUP = 30


def _rth_bars(n_bars: int, seed: int = 11) -> pd.DataFrame:
    """Session-shaped 5-min RTH bars, `n_bars` total (78/day), for a
    controllable exact total-bar count independent of calendar dates."""
    rng = np.random.default_rng(seed)
    frames = []
    cur = pd.Timestamp("2024-01-02", tz="America/New_York")
    got = 0
    while got < n_bars:
        if cur.weekday() < 5:
            remaining = n_bars - got
            day_len = min(78, remaining)
            idx = pd.date_range(f"{cur.date()} 09:30", periods=day_len, freq="5min", tz="America/New_York")
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
            got += n
        cur += pd.Timedelta(days=1)
    return pd.concat(frames)


def _provenance(df: pd.DataFrame) -> DataProvenance:
    return DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="BENCH", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


def _run(df: pd.DataFrame, label: str, use_optimized: bool, n: int) -> tuple[float, float, int]:
    prov = _provenance(df)
    t0 = time.perf_counter()
    result = run_replay(
        df, "BENCH", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id=f"bench51p-{label}-{n}",
        warmup_bars=_WARMUP, enforce_session_integrity=False, allow_unverified_adjustment=True,
        use_optimized_market_state=use_optimized,
    )
    elapsed = time.perf_counter() - t0
    rate = result.bars_evaluated / elapsed if elapsed > 0 else float("inf")
    print(f"{n:>10}{label:>22}{elapsed:>14.3f}{rate:>14.2f}{result.bars_evaluated:>18}", flush=True)
    return elapsed, rate, result.bars_evaluated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-large-reference", action="store_true")
    args = parser.parse_args()

    print(f"{'total_bars':>10}{'mode':>22}{'seconds':>14}{'bars/sec':>14}{'bars_evaluated':>18}", flush=True)

    sizes = (500, 1560, 5000, 10000)
    ref_times: dict[int, float] = {}
    opt_times: dict[int, float] = {}

    for n in sizes:
        df = _rth_bars(n)
        skip_ref = args.skip_large_reference and n >= 5000
        if not skip_ref:
            elapsed, _, _ = _run(df, "REFERENCE", False, n)
            ref_times[n] = elapsed
        else:
            print(f"{n:>10}{'REFERENCE':>22}{'SKIPPED (impractical)':>14}", flush=True)

        elapsed, _, _ = _run(df, "OPTIMIZED_MARKET_STATE", True, n)
        opt_times[n] = elapsed

    print(flush=True)
    print("=== speedup (REFERENCE seconds / OPTIMIZED seconds) ===", flush=True)
    for n in sizes:
        if n in ref_times:
            print(f"{n:>10} bars: {ref_times[n] / opt_times[n]:.2f}x", flush=True)


if __name__ == "__main__":
    main()
