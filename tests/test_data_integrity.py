import numpy as np
import pandas as pd
from conftest import make_flat_df

from engine.data_integrity.checks import check_bars, has_failure


def test_clean_data_has_no_failures():
    df = make_flat_df([100 + i * 0.1 for i in range(30)])
    issues = check_bars(df, timeframe_minutes=5, now=df.index[-1] + pd.Timedelta(minutes=5))
    assert not has_failure(issues)


def test_empty_dataframe_is_a_failure():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    issues = check_bars(df, timeframe_minutes=5)
    assert has_failure(issues)
    assert issues[0].code == "NO_DATA"


def test_nan_price_is_a_failure():
    df = make_flat_df([100.0] * 20)
    df.iloc[5, df.columns.get_loc("close")] = np.nan
    issues = check_bars(df, timeframe_minutes=5)
    assert has_failure(issues)
    assert any(i.code == "MISSING_PRICE" for i in issues)


def test_stale_data_is_a_failure():
    # Application acceptance hardening: staleness is exchange-session-aware
    # (see tests/test_data_integrity_session_aware_staleness.py for the full
    # suite) — this fixture must land DURING a real active RTH session for
    # "2 hours since the last bar" to mean anything as staleness. Real ET
    # session bars, not make_flat_df's default UTC-stamped "09:30".
    idx = pd.date_range("2024-01-02 09:30", periods=20, freq="5min", tz="America/New_York")
    df = pd.DataFrame(
        {"open": [100.0] * 20, "high": [100.0] * 20, "low": [100.0] * 20, "close": [100.0] * 20,
         "volume": [100_000] * 20},
        index=idx,
    ).tz_convert("UTC")
    now = df.index[-1] + pd.Timedelta(hours=2)  # still well within 2024-01-02's 09:30-16:00 ET session
    issues = check_bars(df, timeframe_minutes=5, now=now)
    assert has_failure(issues)
    assert any(i.code == "STALE_DATA" for i in issues)


def test_gap_is_only_a_warning_not_a_failure():
    df = make_flat_df([100.0] * 10)
    # Introduce an overnight-style gap.
    new_index = list(df.index[:5]) + [df.index[4] + pd.Timedelta(hours=16)] + list(df.index[6:])
    df.index = pd.DatetimeIndex(new_index)
    issues = check_bars(df, timeframe_minutes=5, now=df.index[-1] + pd.Timedelta(minutes=5))
    gap_issues = [i for i in issues if i.code == "GAP_DETECTED"]
    assert gap_issues and gap_issues[0].severity == "WARNING"
