"""Market-data provider abstraction (see §19). Providers are never silently
mixed for the same symbol/timeframe — every stored bar is stamped with its
`provider` at the repository layer."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame: ...

    def health(self) -> dict: ...
