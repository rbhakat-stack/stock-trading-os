"""AlpacaProvider adjustment pass-through + live-caller backward compatibility
(Phase 5.1P Part A-2/A-3). Entirely network-free: the alpaca-py SDK client is
monkeypatched so these tests never hit the real API and never touch
credentials — construction still requires *some* key strings (never real
ones), and nothing is asserted about their values."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from engine.data_provider.alpaca_provider import AlpacaProvider


class _FakeBarSet:
    def __init__(self, df: pd.DataFrame):
        self.df = df


def _empty_df():
    idx = pd.MultiIndex.from_tuples([], names=["symbol", "timestamp"])
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=idx)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret-not-real")
    return AlpacaProvider()


def _capture_request(monkeypatch, provider, df=None):
    captured = {}

    def fake_get_stock_bars(self, request_params):
        captured["request"] = request_params
        return _FakeBarSet(df if df is not None else _empty_df())

    monkeypatch.setattr(
        "alpaca.data.historical.stock.StockHistoricalDataClient.get_stock_bars", fake_get_stock_bars,
    )
    return captured


_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
_END = datetime(2024, 1, 3, tzinfo=timezone.utc)


def test_default_call_sends_no_adjustment_field_at_all(provider, monkeypatch):
    """§A-2 backward compatibility: existing live callers (Market Reader,
    Trade Planner) never pass `adjustment=` — this proves that path still
    sends nothing, exactly as before this round."""
    captured = _capture_request(monkeypatch, provider)
    provider.get_ohlcv("AAPL", "5min", _START, _END)
    assert captured["request"].adjustment is None


def test_explicit_raw_is_sent_through(provider, monkeypatch):
    from alpaca.data.enums import Adjustment

    captured = _capture_request(monkeypatch, provider)
    provider.get_ohlcv("AAPL", "5min", _START, _END, adjustment="raw")
    assert captured["request"].adjustment == Adjustment.RAW


def test_explicit_split_is_sent_through(provider, monkeypatch):
    from alpaca.data.enums import Adjustment

    captured = _capture_request(monkeypatch, provider)
    provider.get_ohlcv("AAPL", "5min", _START, _END, adjustment="split")
    assert captured["request"].adjustment == Adjustment.SPLIT


def test_explicit_all_is_sent_through(provider, monkeypatch):
    from alpaca.data.enums import Adjustment

    captured = _capture_request(monkeypatch, provider)
    provider.get_ohlcv("AAPL", "5min", _START, _END, adjustment="all")
    assert captured["request"].adjustment == Adjustment.ALL


def test_unsupported_adjustment_raises_before_any_network_call(provider, monkeypatch):
    captured = _capture_request(monkeypatch, provider)
    with pytest.raises(ValueError, match="Unsupported adjustment"):
        provider.get_ohlcv("AAPL", "5min", _START, _END, adjustment="not-a-real-mode")
    assert "request" not in captured  # failed closed before ever calling the SDK


def test_feed_stays_hardcoded_iex_regardless_of_adjustment(provider, monkeypatch):
    captured = _capture_request(monkeypatch, provider)
    provider.get_ohlcv("AAPL", "5min", _START, _END, adjustment="split")
    assert captured["request"].feed.value == "iex"


def test_live_caller_style_four_positional_args_still_works(provider, monkeypatch):
    """Exactly how app/pages/market_reader.py and trade_planner.py call this
    today — positional, no adjustment. Must keep working unchanged."""
    captured = _capture_request(monkeypatch, provider)
    df = provider.get_ohlcv("AAPL", "5min", _START, _END)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert captured["request"].adjustment is None
