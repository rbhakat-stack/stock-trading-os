import pandas as pd


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
