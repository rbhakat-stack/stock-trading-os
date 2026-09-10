"""Deterministic synthetic OHLCV generator.

Lets the whole app run — and be demoed — before any Alpaca account exists, in
keeping with the free-tier-first, zero-friction-to-start goal. This is NOT a
substitute for real data once you're validating an actual edge; it exists so
Phase 1 can be exercised end-to-end without requiring an external signup first.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

_MINUTES_MAP = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1day": 1440}


class SyntheticProvider:
    def __init__(self, seed: int = 42):
        self.seed = seed

    def get_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, adjustment: str | None = None,
    ) -> pd.DataFrame:
        """`adjustment` is accepted only for `MarketDataProvider` Protocol
        uniformity and ignored — synthetic data has no corporate actions, so
        no adjustment mode changes anything about it."""
        step = _MINUTES_MAP.get(timeframe, 5)
        n = max(50, int((end - start).total_seconds() / 60 / step))
        rng = np.random.default_rng((abs(hash(symbol)) % (2**32)) + self.seed)

        ts = pd.date_range(start=start, periods=n, freq=f"{step}min", tz="UTC")
        drift = rng.normal(0.02, 0.15, n).cumsum()
        close = 100 + drift + rng.normal(0, 0.3, n)
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        high = np.maximum(open_, close) + rng.uniform(0, 0.4, n)
        low = np.minimum(open_, close) - rng.uniform(0, 0.4, n)
        volume = rng.integers(50_000, 500_000, n)

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=ts,
        )
        df.index.name = "ts"
        return df

    def health(self) -> dict:
        return {"provider": "synthetic", "configured": True}
