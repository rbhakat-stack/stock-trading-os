"""Phase 5.1P Part A-2 — real-data verification that Alpaca's `adjustment`
modes actually have an effect (not just that the SDK accepts the parameter).

This is the "real-data verification test... that compares a known
split-sensitive period and proves the requested adjustment mode has an
effect" the Phase 5.1P task explicitly asked for. It hits the real Alpaca
API, so it is opt-in only (skipped by default everywhere, including this
dev environment, unless RUN_LIVE_ALPACA_TESTS=1 is set) — it must never make
the full/offline pytest suite depend on network access or credentials.
No credential value is ever printed, logged, or asserted on.

This symbol/period (AAPL's 2020-08-31 4:1 split) is a TEST FIXTURE CHOICE,
not production logic — production code (AlpacaProvider, ingest_symbol_range)
has no AAPL-specific behavior anywhere; see those modules.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_ALPACA_TESTS") != "1",
    reason="live/network Alpaca test — opt in with RUN_LIVE_ALPACA_TESTS=1 (requires real .env credentials)",
)


@pytest.fixture
def provider():
    from engine.data_provider.alpaca_provider import AlpacaProvider

    return AlpacaProvider()


def test_split_adjustment_removes_the_price_discontinuity_across_a_known_split(provider):
    """AAPL split 4:1 on 2020-08-31. adjustment='raw' must show the ~4x
    discontinuity; adjustment='split' must NOT."""
    start = datetime(2020, 8, 20, tzinfo=timezone.utc)
    end = datetime(2020, 9, 10, tzinfo=timezone.utc)

    raw = provider.get_ohlcv("AAPL", "1day", start, end, adjustment="raw")
    split = provider.get_ohlcv("AAPL", "1day", start, end, adjustment="split")

    assert not raw.empty and not split.empty

    pre_split_raw = raw["close"].iloc[0]
    post_split_raw = raw["close"].iloc[-1]
    # Real, unadjusted AAPL closes straddling the split are ~4x apart in RAW mode.
    assert pre_split_raw / post_split_raw > 3.0

    pre_split_adj = split["close"].iloc[0]
    post_split_adj = split["close"].iloc[-1]
    # Same window, split-adjusted: no artificial ~4x jump remains.
    ratio = pre_split_adj / post_split_adj
    assert 0.5 < ratio < 2.0


def test_raw_adjustment_with_no_explicit_param_are_identical(provider):
    """Confirms AlpacaProvider's un-set default really is RAW (empirically,
    not by assumption) — the same check performed manually during the
    Alpaca capability-validation round, now automated."""
    start = datetime(2020, 8, 20, tzinfo=timezone.utc)
    end = datetime(2020, 9, 10, tzinfo=timezone.utc)

    default = provider.get_ohlcv("AAPL", "1day", start, end)
    raw = provider.get_ohlcv("AAPL", "1day", start, end, adjustment="raw")

    pd_testing_close = (default["close"] - raw["close"]).abs().max()
    assert pd_testing_close < 1e-9
