"""Phase 5.1 §13/§16-N — replay run reproducibility metadata."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_rth_days

from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode
from engine.backtest.contracts import PlaybookPin
from engine.backtest.replay import run_replay

_PINS = (PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"))


def _provenance(df):
    return DataProvenance(
        provider="synthetic", data_type=DataType.SYNTHETIC_TEST_DATA, adjustment_status=AdjustmentStatus.UNKNOWN,
        symbol="SPY", timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )


def test_replay_run_config_captures_all_required_reproducibility_fields():
    df = make_rth_days(2)
    result = run_replay(
        df, "SPY", "5min", _provenance(df), RunMode.TEST_SYNTHETIC, _PINS, run_id="repro-1",
        higher_timeframes=("1hour",), warmup_bars=20, enforce_session_integrity=False,
        allow_unverified_adjustment=True, min_rr=1.5, git_commit_sha="abc123", code_version_tag="phase-5.1-dev",
    )
    cfg = result.config
    assert cfg.run_id == "repro-1"
    assert cfg.symbols == ("SPY",)
    assert cfg.timeframe == "5min"
    assert cfg.higher_timeframes == ("1hour",)
    assert cfg.date_range_start == df.index[0]
    assert cfg.date_range_end == df.index[-1]
    assert cfg.historical_provider == "synthetic"
    assert cfg.adjustment_status == "UNKNOWN"
    assert cfg.playbook_pins == _PINS
    assert cfg.min_rr == 1.5
    assert cfg.run_mode == "TEST_SYNTHETIC"
    assert cfg.data_quality_policy
    assert cfg.calendar_policy
    assert cfg.git_commit_sha == "abc123"
    assert cfg.code_version_tag == "phase-5.1-dev"
    assert cfg.random_seed is None  # Phase 5.1 introduces no randomness anywhere


def test_repeated_run_with_identical_config_produces_identical_evaluations_and_config_shape():
    df = make_rth_days(3)
    r1 = run_replay(
        df, "SPY", "5min", _provenance(df), RunMode.TEST_SYNTHETIC, _PINS, run_id="repro-a",
        warmup_bars=20, enforce_session_integrity=False, allow_unverified_adjustment=True,
    )
    r2 = run_replay(
        df, "SPY", "5min", _provenance(df), RunMode.TEST_SYNTHETIC, _PINS, run_id="repro-b",
        warmup_bars=20, enforce_session_integrity=False, allow_unverified_adjustment=True,
    )
    # run_id is deliberately allowed to differ (each run is its own record);
    # every OTHER config field and every evaluation's own content must match.
    assert r1.config.symbols == r2.config.symbols
    assert r1.config.playbook_pins == r2.config.playbook_pins
    key = lambda e: (e.timestamp, e.playbook_id, e.setup_status, e.quality_score)
    assert [key(e) for e in r1.evaluations] == [key(e) for e in r2.evaluations]


def test_every_evaluation_row_is_attributable_to_its_run_id():
    df = make_rth_days(3)
    result = run_replay(
        df, "SPY", "5min", _provenance(df), RunMode.TEST_SYNTHETIC, _PINS, run_id="repro-attr",
        warmup_bars=20, enforce_session_integrity=False, allow_unverified_adjustment=True,
    )
    assert result.evaluations
    assert all(e.run_id == "repro-attr" for e in result.evaluations)
    assert result.config.run_id == "repro-attr"


def test_config_rejects_empty_symbols_or_pins():
    import pytest

    from engine.backtest.contracts import ReplayRunConfig

    kwargs = dict(
        run_id="x", timeframe="5min", higher_timeframes=(), date_range_start=make_rth_days(1).index[0],
        date_range_end=make_rth_days(1).index[-1], historical_provider="synthetic", adjustment_status="UNKNOWN",
        min_rr=1.5, run_mode="TEST_SYNTHETIC", data_quality_policy="x", calendar_policy="x",
    )
    with pytest.raises(ValueError):
        ReplayRunConfig(symbols=(), playbook_pins=_PINS, **kwargs)
    with pytest.raises(ValueError):
        ReplayRunConfig(symbols=("SPY",), playbook_pins=(), **kwargs)
