"""Phase 5.1 §16 A-J — reference replay engine correctness: no look-ahead,
historical `now` semantics, determinism, trigger-transition semantics,
version pinning, long/short symmetry, fail-closed data-quality/provenance
gates."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_rth_days

from engine.backtest.bar_source import (
    AdjustmentStatus, DataProvenance, DataType, RunMode, SyntheticDataInProductionError, UnverifiedAdjustmentError,
)
from engine.backtest.contracts import PlaybookPin
from engine.backtest.replay import (
    PlaybookVersionMismatchError, ReplayDataQualityError, resolve_pinned_definition, run_replay,
    visible_higher_timeframe_data,
)
from engine.playbooks.taxonomy import SetupStatus


def _provenance(df, provider="synthetic", adjustment=AdjustmentStatus.SPLIT_ADJUSTED, symbol="SPY", timeframe="5min"):
    return DataProvenance(
        provider=provider,
        data_type=DataType.SYNTHETIC_TEST_DATA if provider == "synthetic" else DataType.REAL_MARKET_DATA,
        adjustment_status=adjustment, symbol=symbol, timeframe=timeframe,
        range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


_PINS = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"))


def _run(df, pins=_PINS, run_mode=RunMode.TEST_SYNTHETIC, higher_timeframes=(), warmup_bars=20, **kw):
    prov = kw.pop("provenance", None) or _provenance(df)
    return run_replay(
        df, "SPY", "5min", prov, run_mode, pins, run_id="test-run",
        higher_timeframes=higher_timeframes, warmup_bars=warmup_bars, enforce_session_integrity=False, **kw,
    )


# ---- A: no look-ahead — base timeframe ----


def test_reference_replay_never_sees_base_timeframe_future_bars():
    df = make_rth_days(3)
    result = _run(df)
    # Every evaluation's timestamp must be one of the bars actually visible
    # up to that point — trivially true by construction, but this proves no
    # evaluation is stamped with a timestamp beyond the input range at all.
    for e in result.evaluations:
        assert df.index[0] <= e.timestamp <= df.index[-1]


def test_evaluation_at_cursor_i_is_unaffected_by_bars_after_i():
    # Truncate the SAME data at bar i+1 vs. feed the FULL dataset — the
    # evaluation AT bar i must be identical either way, proving nothing
    # beyond i influenced it.
    df_full = make_rth_days(3)
    cut = 60
    df_truncated = df_full.iloc[: cut + 1]

    result_full = _run(df_full, warmup_bars=20)
    result_truncated = _run(df_truncated, warmup_bars=20)

    evals_full_up_to_cut = [e for e in result_full.evaluations if e.timestamp <= df_full.index[cut]]
    evals_truncated = list(result_truncated.evaluations)
    assert [(e.timestamp, e.playbook_id, e.setup_status) for e in evals_full_up_to_cut] == [
        (e.timestamp, e.playbook_id, e.setup_status) for e in evals_truncated
    ]


# ---- A: no look-ahead — higher timeframe + partially formed candle ----


def test_higher_timeframe_forming_bucket_is_never_exposed():
    idx = pd.date_range("2024-01-02 09:30", periods=78, freq="5min", tz="America/New_York")
    n = len(idx)
    df = pd.DataFrame(
        {"open": range(100, 100 + n), "high": [x + 1 for x in range(100, 100 + n)],
         "low": [x - 1 for x in range(100, 100 + n)], "close": range(100, 100 + n), "volume": [1000] * n},
        index=idx,
    ).tz_convert("UTC")
    # Cursor at 10:15 local: only 9:30-10:15 is visible; the 9:30-10:30
    # session-anchored hourly bucket has NOT closed yet.
    cursor_local = pd.Timestamp("2024-01-02 10:15", tz="America/New_York")
    cursor_idx = df.index.get_indexer([cursor_local.tz_convert("UTC")], method="nearest")[0]
    df_visible = df.iloc[: cursor_idx + 1]
    cursor_ts = df.index[cursor_idx]

    higher = visible_higher_timeframe_data(df_visible, cursor_ts, ("1hour",), base_timeframe_minutes=5)
    assert higher["1hour"].empty  # the forming bucket must be entirely absent, not partially exposed


def test_higher_timeframe_bucket_becomes_visible_only_once_fully_closed():
    idx = pd.date_range("2024-01-02 09:30", periods=78, freq="5min", tz="America/New_York")
    n = len(idx)
    df = pd.DataFrame(
        {"open": range(100, 100 + n), "high": [x + 1 for x in range(100, 100 + n)],
         "low": [x - 1 for x in range(100, 100 + n)], "close": range(100, 100 + n), "volume": [1000] * n},
        index=idx,
    ).tz_convert("UTC")
    # First session-anchored hourly bucket is 9:30-10:30. Just before its
    # last contributing 5-min bar (10:25) is visible, it must be absent;
    # once that bar is visible, the bucket must appear with FINAL values.
    before_ts = pd.Timestamp("2024-01-02 10:20", tz="America/New_York").tz_convert("UTC")
    at_close_ts = pd.Timestamp("2024-01-02 10:25", tz="America/New_York").tz_convert("UTC")

    before_idx = df.index.get_indexer([before_ts], method="nearest")[0]
    at_idx = df.index.get_indexer([at_close_ts], method="nearest")[0]

    before_higher = visible_higher_timeframe_data(df.iloc[: before_idx + 1], df.index[before_idx], ("1hour",), 5)
    at_higher = visible_higher_timeframe_data(df.iloc[: at_idx + 1], df.index[at_idx], ("1hour",), 5)

    assert before_higher["1hour"].empty
    assert len(at_higher["1hour"]) == 1
    # The now-complete bucket's close must be the LAST bar's close that
    # actually belongs to it (10:25 bar, close=112), never a placeholder.
    expected_close = df["close"].loc[df.index[at_idx]]
    assert at_higher["1hour"]["close"].iloc[0] == expected_close


def test_future_higher_timeframe_sessions_are_entirely_invisible():
    df = make_rth_days(3)
    mid_idx = 78 + 30  # partway through day 2
    df_visible = df.iloc[: mid_idx + 1]
    cursor_ts = df.index[mid_idx]
    higher = visible_higher_timeframe_data(df_visible, cursor_ts, ("1day",), base_timeframe_minutes=5)
    # No daily bucket may be stamped on or after day 3.
    day3_start = df.index[156]
    assert all(ts < day3_start for ts in higher["1day"].index)


# ---- B: historical `now` semantics — never wall-clock ----


def test_replay_uses_historical_bar_timestamp_never_wall_clock(monkeypatch):
    df = make_rth_days(2)
    captured_now = []
    import engine.backtest.replay as replay_module

    real_build_snapshot = replay_module.build_snapshot

    def spy_build_snapshot(*args, **kwargs):
        captured_now.append(kwargs.get("now"))
        return real_build_snapshot(*args, **kwargs)

    monkeypatch.setattr(replay_module, "build_snapshot", spy_build_snapshot)
    _run(df, warmup_bars=20)

    assert captured_now  # build_snapshot was actually called
    wall_clock_now = datetime.now(timezone.utc)
    for now_value in captured_now:
        assert now_value is not None
        assert now_value in set(df.index)  # always a real historical bar timestamp
        assert now_value < pd.Timestamp(wall_clock_now) - pd.Timedelta(days=1)  # never anywhere near real "now"


# ---- C: deterministic replay ----


def test_repeated_replay_produces_identical_output():
    df = make_rth_days(3)
    r1 = _run(df)
    r2 = _run(df)
    key = lambda e: (e.timestamp, e.playbook_id, e.setup_status, e.quality_score, e.entry_price, e.stop_price)
    assert [key(e) for e in r1.evaluations] == [key(e) for e in r2.evaluations]


# ---- D: trigger-transition semantics ----


def test_triggered_to_triggered_is_never_duplicated():
    df = make_rth_days(4)
    result = _run(df)
    by_playbook: dict[str, list[str]] = {}
    for e in result.evaluations:
        by_playbook.setdefault(e.playbook_id, []).append(e.setup_status)
    for statuses in by_playbook.values():
        for a, b in zip(statuses, statuses[1:]):
            assert a != b  # no two consecutive recorded rows share the same status


def test_is_new_trigger_occurrence_flags_only_the_forming_to_triggered_edge():
    df = make_rth_days(4)
    result = _run(df)
    for e in result.evaluations:
        if e.is_new_trigger_occurrence:
            assert e.setup_status == SetupStatus.TRIGGERED.value


def test_forming_to_triggered_recorded_exactly_once_per_episode():
    df = make_rth_days(4)
    result = _run(df)
    # Reconstruct the transition sequence per playbook and confirm every
    # TRIGGERED entry is immediately preceded (in the recorded log) by a
    # non-TRIGGERED status — i.e. it really is a transition, not a repeat.
    by_playbook: dict[str, list[str]] = {}
    for e in result.evaluations:
        by_playbook.setdefault(e.playbook_id, []).append(e.setup_status)
    for statuses in by_playbook.values():
        for i, status in enumerate(statuses):
            if status == SetupStatus.TRIGGERED.value and i > 0:
                assert statuses[i - 1] != SetupStatus.TRIGGERED.value


# ---- E: version pinning ----


def test_pinned_v1_0_replay_stays_v1_0_on_every_row():
    df = make_rth_days(4)
    result = _run(df, pins=(PlaybookPin("TREND_PULLBACK_LONG", "1.0"),))
    assert result.evaluations  # sanity: something was actually recorded
    assert all(e.playbook_version == "1.0" for e in result.evaluations)


def test_current_registry_version_cannot_silently_replace_a_mismatched_pin():
    df = make_rth_days(2)
    with pytest.raises(PlaybookVersionMismatchError):
        _run(df, pins=(PlaybookPin("TREND_PULLBACK_LONG", "1.1"),))  # registry only has 1.0


def test_unknown_playbook_id_pin_fails_closed():
    with pytest.raises(PlaybookVersionMismatchError):
        resolve_pinned_definition(PlaybookPin("NOT_A_REAL_PLAYBOOK", "1.0"))


def test_not_implementable_playbook_pin_fails_closed():
    with pytest.raises(PlaybookVersionMismatchError):
        resolve_pinned_definition(PlaybookPin("VWAP_RECLAIM_LONG", "0.0"))


# ---- F: long/short symmetry ----


def test_long_and_short_pins_are_evaluated_independently_and_symmetrically():
    df = make_rth_days(5)
    pins = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"))
    result = _run(df, pins=pins)
    ids_present = {e.playbook_id for e in result.evaluations}
    # Not asserting BOTH necessarily trigger (depends on the random walk),
    # but both must be independently represented in the transition log —
    # neither is suppressed or silently dropped.
    long_rows = [e for e in result.evaluations if e.playbook_id == "TREND_PULLBACK_LONG"]
    short_rows = [e for e in result.evaluations if e.playbook_id == "TREND_PULLBACK_SHORT"]
    assert long_rows  # LONG produced its own transition history
    assert short_rows  # SHORT produced its own transition history, independently
    for e in long_rows:
        assert e.direction == "LONG"
    for e in short_rows:
        assert e.direction == "SHORT"


# ---- G: synthetic-data production rejection (replay-level, not just bar_source) ----


def test_run_replay_rejects_synthetic_data_in_production_mode():
    df = make_rth_days(2)
    with pytest.raises(SyntheticDataInProductionError):
        _run(df, run_mode=RunMode.PRODUCTION, provenance=_provenance(df, provider="synthetic"))


def test_run_replay_allows_synthetic_data_in_test_synthetic_mode():
    df = make_rth_days(2)
    result = _run(df, run_mode=RunMode.TEST_SYNTHETIC, provenance=_provenance(df, provider="synthetic"))
    assert result.bars_evaluated > 0


# ---- H: mixed-provider rejection — full pipeline (HistoricalBarSource -> run_replay) ----


def test_full_pipeline_rejects_mixed_provider_data_before_replay_ever_runs():
    # A mixed-provider `bars` table row set must be refused at the
    # HistoricalBarSource layer (Phase 5.0) — proving run_replay never even
    # gets a chance to see it, since SupabaseBarSource.fetch_range raises
    # before returning anything run_replay could consume.
    from engine.backtest.bar_source import MixedProviderDataError, SupabaseBarSource

    class _FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def gte(self, *a, **k):
            return self

        def lte(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def range(self, *a, **k):
            return self

        def execute(self):
            return type("Res", (), {"data": self._rows})()

    class _FakeClient:
        def __init__(self, rows):
            self._rows = rows

        def table(self, _name):
            return _FakeQuery(self._rows)

    rows = [
        {"ts": "2024-01-02T09:30:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500, "provider": "alpaca"},
        {"ts": "2024-01-02T09:35:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500, "provider": "synthetic"},
    ]
    source = SupabaseBarSource(_FakeClient(rows))
    with pytest.raises(MixedProviderDataError):
        source.fetch_range("SPY", "5min", datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 3, tzinfo=timezone.utc))
    # run_replay is never reached in this scenario — nothing further to call.


# ---- I: missing/malformed-bar fail-closed behavior ----


def test_run_replay_fails_closed_on_malformed_ohlc():
    df = make_rth_days(2).copy()
    df.iloc[5, df.columns.get_loc("high")] = -1.0  # non-positive price
    with pytest.raises(ReplayDataQualityError):
        _run(df)


def test_run_replay_fails_closed_on_duplicate_timestamps():
    df = make_rth_days(2)
    dup = pd.concat([df, df.iloc[[5]]]).sort_index()
    with pytest.raises(ReplayDataQualityError):
        _run(dup)


def test_run_replay_fails_closed_on_unsorted_timestamps():
    df = make_rth_days(2)
    shuffled = df.sample(frac=1.0, random_state=1)
    with pytest.raises(ReplayDataQualityError):
        _run(shuffled)


def test_data_quality_status_is_ok_on_every_recorded_row_for_valid_input():
    # Given check_bars(bars_df) already passed up front and `now` is always
    # exactly the cursor's own last-bar timestamp (zero staleness by
    # construction), build_snapshot's per-cursor internal data-quality gate
    # can never newly fail on an already-validated prefix — see
    # engine/backtest/replay.py's comment at the `data_quality_ok` check.
    df = make_rth_days(3)
    result = _run(df)
    assert result.evaluations
    assert all(e.data_quality_status == "OK" for e in result.evaluations)


def test_interior_gap_is_a_warning_not_a_hard_failure():
    df = make_rth_days(2)
    with_gap = pd.concat([df.iloc[:10], df.iloc[20:]])  # drop 10 interior bars -> a gap, not malformed data
    result = _run(with_gap)
    assert any("GAP_DETECTED" in w for w in result.data_quality_warnings)
    assert result.bars_evaluated > 0  # proceeded despite the warning


# ---- J: adjustment-status fail-closed behavior ----


def test_run_replay_fails_closed_on_unverified_adjustment_status():
    df = make_rth_days(2)
    prov = _provenance(df, adjustment=AdjustmentStatus.UNKNOWN)
    with pytest.raises(UnverifiedAdjustmentError):
        _run(df, provenance=prov, allow_unverified_adjustment=False)


def test_run_replay_allows_unverified_adjustment_with_explicit_override():
    df = make_rth_days(2)
    prov = _provenance(df, adjustment=AdjustmentStatus.RAW)
    result = _run(df, provenance=prov, allow_unverified_adjustment=True)
    assert result.bars_evaluated > 0


# ---- misc: unsupported timeframe / higher-timeframe validation ----


def test_unsupported_base_timeframe_rejected():
    df = make_rth_days(1)
    with pytest.raises(ValueError):
        run_replay(df, "SPY", "3min", _provenance(df, timeframe="3min"), RunMode.TEST_SYNTHETIC, _PINS, "r")


def test_higher_timeframe_must_be_coarser_than_base():
    df = make_rth_days(1)
    with pytest.raises(ValueError):
        _run(df, higher_timeframes=("5min",))  # same as base, not coarser


def test_higher_timeframe_finer_than_base_rejected():
    df = make_rth_days(1)
    with pytest.raises(ValueError):
        _run(df, higher_timeframes=("1min",))
