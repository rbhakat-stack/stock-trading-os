"""Phase 5.1 §9/§16-M — reference-vs-optimized replay equivalence.

Proves `run_replay(..., use_resample_cache=True)` (the OPTIMIZED path)
produces output IDENTICAL to `use_resample_cache=False` (the REFERENCE
path) for the same input — the only permitted difference between them is
speed, never correctness. See engine/backtest/replay.py's
IncrementalResampleCache docstring for why this is provably true (sessions
are resampled independently, so caching already-closed sessions can never
change a later bucket's value).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_rth_days

from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode
from engine.backtest.contracts import PlaybookPin
from engine.backtest.replay import IncrementalResampleCache, visible_higher_timeframe_data, run_replay

_PINS = (
    PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"),
    PlaybookPin("BREAKOUT_RETEST_LONG", "1.0"), PlaybookPin("BREAKOUT_RETEST_SHORT", "1.0"),
)


def _provenance(df):
    return DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="SPY", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


def _eval_key(e):
    return (
        e.timestamp, e.playbook_id, e.setup_status, e.eligibility_status, e.quality_score, e.quality_band,
        e.entry_price, e.entry_zone_low, e.entry_zone_high, e.stop_price, e.target1, e.target2,
        e.market_regime, e.volatility_regime, e.is_new_trigger_occurrence,
    )


def test_reference_and_cached_higher_timeframe_data_are_bar_for_bar_identical():
    df = make_rth_days(6)
    cache = IncrementalResampleCache()
    mismatches = []
    for i in range(20, len(df)):
        df_visible = df.iloc[: i + 1]
        cursor_ts = df.index[i]
        ref = visible_higher_timeframe_data(df_visible, cursor_ts, ("15min", "1hour"), 5)
        cached = cache.get(df_visible, cursor_ts, ("15min", "1hour"), 5)
        for tf in ("15min", "1hour"):
            if not ref[tf].equals(cached[tf]):
                mismatches.append((i, tf))
    assert mismatches == []


def test_full_replay_reference_vs_optimized_produce_identical_evaluations():
    df = make_rth_days(5)
    prov = _provenance(df)

    reference = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="ref",
        higher_timeframes=("1hour",), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=False,
    )
    optimized = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="opt",
        higher_timeframes=("1hour",), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=True,
    )

    assert len(reference.evaluations) > 0  # sanity: the fixture actually produced transitions
    ref_keys = [_eval_key(e) for e in reference.evaluations]
    opt_keys = [_eval_key(e) for e in optimized.evaluations]
    assert ref_keys == opt_keys


def test_reference_vs_optimized_equivalence_holds_with_multiple_higher_timeframes():
    df = make_rth_days(4)
    prov = _provenance(df)
    reference = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="ref2",
        higher_timeframes=("15min", "1hour", "1day"), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=False,
    )
    optimized = run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, _PINS, run_id="opt2",
        higher_timeframes=("15min", "1hour", "1day"), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=True,
    )
    assert [_eval_key(e) for e in reference.evaluations] == [_eval_key(e) for e in optimized.evaluations]


def test_optimized_path_is_not_slower_than_reference_for_a_multi_day_fixture():
    import time

    df = make_rth_days(8)
    prov = _provenance(df)

    t0 = time.perf_counter()
    run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, (_PINS[0],), run_id="perf-ref",
        higher_timeframes=("1hour",), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=False,
    )
    t_ref = time.perf_counter() - t0

    t0 = time.perf_counter()
    run_replay(
        df, "SPY", "5min", prov, RunMode.TEST_SYNTHETIC, (_PINS[0],), run_id="perf-opt",
        higher_timeframes=("1hour",), warmup_bars=30, enforce_session_integrity=False,
        allow_unverified_adjustment=True, use_resample_cache=True,
    )
    t_opt = time.perf_counter() - t0

    # Not a tight bound (build_snapshot's own cost dominates both — see the
    # Phase 5.1 report §M/§N) — just proves the cache never makes things
    # WORSE, which would indicate a bug in the caching logic itself.
    assert t_opt <= t_ref * 1.15
