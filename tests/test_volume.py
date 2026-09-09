import pandas as pd

from engine.market_state.volume import (
    MIN_BARS_FOR_ROLLING_RVOL,
    MIN_DAYS_FOR_TIME_OF_DAY_RVOL,
    classify_volume_level,
    compute_rvol,
)


def _df_with_volume(volumes, start="2024-01-02 09:30", freq="5min"):
    idx = pd.date_range(start=start, periods=len(volumes), freq=freq, tz="UTC")
    closes = [100.0] * len(volumes)
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes}, index=idx)
    df.index.name = "ts"
    return df


def test_classify_volume_level_buckets():
    assert classify_volume_level(40, 100) == "VERY_LOW"
    assert classify_volume_level(70, 100) == "LOW"
    assert classify_volume_level(100, 100) == "NORMAL"
    assert classify_volume_level(150, 100) == "ELEVATED"
    assert classify_volume_level(250, 100) == "HIGH"
    assert classify_volume_level(400, 100) == "EXTREME"


def test_classify_volume_level_insufficient_data_without_baseline():
    assert classify_volume_level(100, None) == "INSUFFICIENT_DATA"
    assert classify_volume_level(100, 0) == "INSUFFICIENT_DATA"


def test_rvol_insufficient_data_with_short_lookback():
    df = _df_with_volume([100_000] * 5)
    result = compute_rvol(df, bar_index=4)
    assert result.value is None
    assert result.method == "INSUFFICIENT_DATA"


def test_rvol_uses_rolling_20_bar_fallback_with_single_day_history():
    volumes = [100_000] * 25 + [250_000]
    df = _df_with_volume(volumes)
    result = compute_rvol(df, bar_index=25)
    assert result.method == "ROLLING_20_BAR"
    assert result.value == 2.5  # 250,000 / 100,000


def test_rvol_prefers_time_of_day_baseline_with_enough_prior_days():
    # 4 trading days of 09:30-09:50 (5 bars/day), each day's 09:30 volume = 100,000
    # except the final day's 09:30 bar, which spikes to 300,000.
    volumes = []
    for day in range(4):
        volumes.extend([100_000, 80_000, 90_000, 85_000, 95_000])
    volumes[15] = 300_000  # day 3 (0-indexed), 09:30 bar
    ts_list = []
    for day in range(4):
        day_start = pd.Timestamp(f"2024-01-0{2+day} 09:30", tz="UTC")
        ts_list.extend(pd.date_range(day_start, periods=5, freq="5min"))
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": volumes}, index=pd.DatetimeIndex(ts_list)
    )
    df.index.name = "ts"

    result = compute_rvol(df, bar_index=15)  # the spiked 09:30 bar on day 3
    assert result.method == "TIME_OF_DAY_BASELINE"
    assert result.evidence["trading_days_used"] == 3
    assert result.value == 3.0  # 300,000 / avg(100,000,100,000,100,000)
