"""Market Reader acceptance fix — RTH normalization at the live analysis
boundary (§8: pre-market stripped, post-market stripped, full-session bar
counts, timestamp consistency), plus session-aware GAP_DETECTED and the
sparse-extended-hours protection chain.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.backtest.calendar import MARKET_TZ, normalize_intraday_for_live_analysis
from engine.data_integrity.checks import check_bars, has_failure


def _extended_hours_day(date_str: str, seed: int = 1) -> pd.DataFrame:
    idx = pd.date_range(f"{date_str} 04:00", f"{date_str} 19:55", freq="5min", tz=MARKET_TZ)
    rng = np.random.default_rng(seed)
    n = len(idx)
    close = 100 + rng.normal(0, 0.1, n).cumsum()
    df = pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
         "volume": rng.integers(1000, 5000, n)},
        index=idx,
    )
    return df.tz_convert("UTC")


# ---------------------------------------------------------------------------
# §8 — RTH normalization at the live analysis boundary
# ---------------------------------------------------------------------------


def test_premarket_bars_stripped_for_real_intraday_data():
    df = _extended_hours_day("2024-01-02")
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=True)
    local = normalized.index.tz_convert(MARKET_TZ)
    assert (local.time >= pd.Timestamp("09:30").time()).all()


def test_postmarket_bars_stripped_for_real_intraday_data():
    df = _extended_hours_day("2024-01-02")
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=True)
    local = normalized.index.tz_convert(MARKET_TZ)
    assert (local.time < pd.Timestamp("16:00").time()).all()


def test_full_5min_session_normalizes_to_78_bars():
    df = _extended_hours_day("2024-01-02")
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=True)
    assert len(normalized) == 78


def test_15min_rth_behavior():
    idx = pd.date_range("2024-01-02 04:00", "2024-01-02 19:45", freq="15min", tz=MARKET_TZ)
    df = pd.DataFrame(
        {"open": [100.0] * len(idx), "high": [100.5] * len(idx), "low": [99.5] * len(idx),
         "close": [100.0] * len(idx), "volume": [1000] * len(idx)},
        index=idx,
    ).tz_convert("UTC")
    normalized = normalize_intraday_for_live_analysis(df, 15, is_real_market_data=True)
    assert len(normalized) == 26  # 09:30..15:45 inclusive, 15-min steps
    local = normalized.index.tz_convert(MARKET_TZ)
    assert local[-1].time() == pd.Timestamp("15:45").time()


def test_1hour_rth_behavior():
    idx = pd.date_range("2024-01-02 04:00", "2024-01-02 19:00", freq="1h", tz=MARKET_TZ)
    df = pd.DataFrame(
        {"open": [100.0] * len(idx), "high": [100.5] * len(idx), "low": [99.5] * len(idx),
         "close": [100.0] * len(idx), "volume": [1000] * len(idx)},
        index=idx,
    ).tz_convert("UTC")
    normalized = normalize_intraday_for_live_analysis(df, 60, is_real_market_data=True)
    local = normalized.index.tz_convert(MARKET_TZ)
    assert (local.time >= pd.Timestamp("09:30").time()).all()
    assert (local.time < pd.Timestamp("16:00").time()).all()


def test_daily_bars_never_stripped():
    idx = pd.date_range("2024-01-02", periods=5, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {"open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5, "close": [100.0] * 5, "volume": [1000] * 5},
        index=idx,
    )
    normalized = normalize_intraday_for_live_analysis(df, 1440, is_real_market_data=True)
    assert len(normalized) == 5  # completely unaffected


def test_synthetic_data_never_rth_filtered():
    # Continuous, non-session-aware timestamps (like SyntheticProvider) —
    # RTH-filtering these would strip nearly everything.
    idx = pd.date_range("2024-01-02 00:00", periods=200, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {"open": [100.0] * 200, "high": [101.0] * 200, "low": [99.0] * 200, "close": [100.0] * 200,
         "volume": [1000] * 200},
        index=idx,
    )
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=False)
    assert len(normalized) == 200  # completely unaffected


def test_no_extended_hours_timestamp_can_survive_normalization():
    """The exact regression this round fixes: a 16:50 ET post-market bar
    (the literal timestamp the user saw) must never survive normalization
    and reach Phase 2/the UI."""
    df = _extended_hours_day("2024-01-02")
    assert (df.index.tz_convert(MARKET_TZ) == pd.Timestamp("2024-01-02 16:50", tz=MARKET_TZ)).any()  # present pre-fix
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=True)
    assert not (normalized.index.tz_convert(MARKET_TZ) == pd.Timestamp("2024-01-02 16:50", tz=MARKET_TZ)).any()
    local = normalized.index.tz_convert(MARKET_TZ)
    assert local[-1] == pd.Timestamp("2024-01-02 15:55", tz=MARKET_TZ)  # bar-open convention: final RTH bar is 15:55


def test_as_of_and_latest_bar_are_consistent_after_normalization():
    """§1D — AS OF / latest-bar / build_snapshot `now`-relative freshness all
    derive from the SAME normalized df.index[-1] — no separate code path."""
    from engine.market_state.market_intelligence import build_snapshot

    df = _extended_hours_day("2024-01-02")
    normalized = normalize_intraday_for_live_analysis(df, 5, is_real_market_data=True)
    now = pd.Timestamp("2024-01-02 20:35", tz=MARKET_TZ).tz_convert("UTC")  # 8:35pm ET, after close
    snapshot = build_snapshot(
        symbol="AAPL", timeframe="5min", df=normalized, data_source="alpaca", timeframe_minutes=5, now=now,
    )
    assert snapshot.data_quality_ok is True
    assert snapshot.as_of.tz_convert(MARKET_TZ) == pd.Timestamp("2024-01-02 15:55", tz=MARKET_TZ)


# ---------------------------------------------------------------------------
# §2/§8 — session-aware GAP_DETECTED
# ---------------------------------------------------------------------------


def _rth_only(date_str: str, seed: int = 1) -> pd.DataFrame:
    idx = pd.date_range(f"{date_str} 09:30", periods=78, freq="5min", tz=MARKET_TZ)
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 0.1, 78).cumsum()
    df = pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
         "volume": rng.integers(1000, 5000, 78)},
        index=idx,
    )
    return df.tz_convert("UTC")


def _gap_issues(df, timeframe_minutes=5):
    return [i for i in check_bars(df, timeframe_minutes=timeframe_minutes) if i.code == "GAP_DETECTED"]


def test_overnight_boundary_does_not_trigger_false_gap():
    df = pd.concat([_rth_only("2024-01-02", seed=1), _rth_only("2024-01-03", seed=2)])
    assert not _gap_issues(df)


def test_weekend_boundary_does_not_trigger_false_gap():
    df = pd.concat([_rth_only("2024-01-05", seed=1), _rth_only("2024-01-08", seed=2)])  # Fri -> Mon
    assert not _gap_issues(df)


def test_holiday_boundary_does_not_trigger_false_gap():
    df = pd.concat([_rth_only("2023-12-29", seed=1), _rth_only("2024-01-02", seed=2)])  # skips New Year's Day
    assert not _gap_issues(df)


def test_true_missing_intra_session_bar_remains_detected():
    df = _rth_only("2024-01-02")
    df = df.drop(df.index[40])  # remove one bar mid-session -> a real 10-minute gap
    assert _gap_issues(df)


def test_sparse_after_hours_data_cannot_cause_false_gap_once_normalized():
    """The full protection chain: raw extended-hours data (sparse, genuinely
    gappy) is normalized away BEFORE check_bars ever sees it, so it cannot
    manufacture a false GAP_DETECTED."""
    raw = _extended_hours_day("2024-01-02")
    # Simulate sparse post-market prints by dropping most post-16:00 bars,
    # leaving big raw gaps that WOULD trip a naive check.
    local = raw.index.tz_convert(MARKET_TZ)
    keep = (local.time < pd.Timestamp("16:00").time()) | (local.minute == 0)
    sparse_raw = raw[keep]
    normalized = normalize_intraday_for_live_analysis(sparse_raw, 5, is_real_market_data=True)
    assert not _gap_issues(normalized)


def test_daily_gap_detection_behavior_unchanged():
    # Daily gap detection keeps its pre-existing flat-interval behavior —
    # not part of this round's scope; just confirms it still runs without error.
    idx = pd.date_range("2024-01-02", periods=5, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {"open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5, "close": [100.0] * 5, "volume": [1000] * 5},
        index=idx,
    )
    issues = check_bars(df, timeframe_minutes=1440)
    assert not has_failure(issues)
