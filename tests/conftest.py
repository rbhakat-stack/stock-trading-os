import numpy as np
import pandas as pd


def make_rth_days(
    n_days: int, start: str = "2024-01-02", seed: int = 7, bars_per_day: int = 78,
) -> pd.DataFrame:
    """Phase 5.1 fixture helper: `n_days` of session-shaped 5-minute RTH bars
    (09:30-16:00 America/New_York), skipping weekends, with a deterministic
    (seeded) random walk — used by tests/test_backtest_replay*.py wherever a
    realistic, session-aligned multi-day fixture is needed (the multi-
    timeframe/session-integrity tests specifically need real RTH shape,
    unlike engine.data_provider.synthetic_provider.SyntheticProvider's
    continuous, non-session-aware bars). UTC-indexed, matching what
    HistoricalBarSource implementations return."""
    rng = np.random.default_rng(seed)
    frames = []
    cur = pd.Timestamp(start, tz="America/New_York")
    got = 0
    while got < n_days:
        if cur.weekday() < 5:
            idx = pd.date_range(f"{cur.date()} 09:30", periods=bars_per_day, freq="5min", tz="America/New_York")
            close = 100 + rng.normal(0.01, 0.1, bars_per_day).cumsum()
            openp = np.roll(close, 1)
            openp[0] = close[0]
            high = np.maximum(openp, close) + rng.uniform(0, 0.3, bars_per_day)
            low = np.minimum(openp, close) - rng.uniform(0, 0.3, bars_per_day)
            volume = rng.integers(1_000, 5_000, bars_per_day)
            day_df = pd.DataFrame(
                {"open": openp, "high": high, "low": low, "close": close, "volume": volume}, index=idx,
            )
            frames.append(day_df.tz_convert("UTC"))
            got += 1
        cur += pd.Timedelta(days=1)
    return pd.concat(frames)


def make_flat_df(closes: list[float], start="2024-01-02 09:30", freq="5min") -> pd.DataFrame:
    """Builds an OHLCV frame where open=high=low=close, so swing detection tracks
    the close series exactly — makes hand-constructed test sequences unambiguous."""
    idx = pd.date_range(start=start, periods=len(closes), freq=freq, tz="UTC")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100_000] * len(closes),
        },
        index=idx,
    )
    df.index.name = "ts"
    return df
