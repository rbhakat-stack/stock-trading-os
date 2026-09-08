"""Alpaca market-data provider — free IEX feed tier (see §32). Requires
`alpaca-py` and ALPACA_API_KEY / ALPACA_SECRET_KEY. Imports of `alpaca` are
deferred into methods so the rest of the app works even when the package or
credentials aren't present (e.g. running fully on synthetic data).
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd


class AlpacaProvider:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not self.api_key or not self.secret_key:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not configured")

        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(self.api_key, self.secret_key)

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf_map = {
            "1min": TimeFrame(1, TimeFrameUnit.Minute),
            "5min": TimeFrame(5, TimeFrameUnit.Minute),
            "15min": TimeFrame(15, TimeFrameUnit.Minute),
            "30min": TimeFrame(30, TimeFrameUnit.Minute),
            "1hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1day": TimeFrame(1, TimeFrameUnit.Day),
        }
        if timeframe not in tf_map:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf_map[timeframe],
            start=start,
            end=end,
            feed="iex",
        )
        bars = self._client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level=0, drop=True)
        df.index.name = "ts"
        return df[["open", "high", "low", "close", "volume"]].sort_index()

    def health(self) -> dict:
        return {"provider": "alpaca", "configured": bool(self.api_key and self.secret_key)}
