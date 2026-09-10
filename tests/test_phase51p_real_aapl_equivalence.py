"""Phase 5.1P §Part B item 9/16 — real AAPL equivalence: REFERENCE vs
OPTIMIZED market-state computation must be exactly equal on real historical
data too, not just synthetic fixtures. Opt-in only (network + credentials),
consistent with tests/test_alpaca_live_validation.py — never part of the
default/offline test run.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_ALPACA_TESTS") != "1",
    reason="live/network Alpaca test — opt in with RUN_LIVE_ALPACA_TESTS=1 (requires real .env credentials)",
)


def test_reference_and_optimized_market_state_identical_on_real_aapl_bars():
    from engine.backtest.calendar import filter_to_regular_trading_hours
    from engine.data_provider.alpaca_provider import AlpacaProvider
    from engine.market_state.market_intelligence import build_snapshot
    from engine.playbooks.engine import evaluate_all_playbooks

    provider = AlpacaProvider()
    end = datetime.now(timezone.utc) - timedelta(days=2)
    start = end - timedelta(days=10)
    raw = provider.get_ohlcv("AAPL", "5min", start, end, adjustment="split")
    df = filter_to_regular_trading_hours(raw)
    assert len(df) > 100, "need a real multi-session window for a meaningful check"

    warmup = 30
    checked = 0
    for i in range(warmup, len(df), 5):  # every 5th bar keeps this practical
        prefix = df.iloc[: i + 1]
        now = prefix.index[-1]
        ref = build_snapshot(
            symbol="AAPL", timeframe="5min", df=prefix, data_source="alpaca", timeframe_minutes=5,
            now=now, use_optimized_computation=False,
        )
        opt = build_snapshot(
            symbol="AAPL", timeframe="5min", df=prefix, data_source="alpaca", timeframe_minutes=5,
            now=now, use_optimized_computation=True,
        )
        assert ref.market_state == opt.market_state
        assert ref.swing_points == opt.swing_points
        assert ref.events == opt.events
        assert ref.support_zones == opt.support_zones
        assert ref.resistance_zones == opt.resistance_zones
        assert ref.rvol == opt.rvol
        assert ref.consolidation == opt.consolidation
        assert ref.breakout == opt.breakout
        assert ref.atr_value == opt.atr_value

        if ref.data_quality_ok:
            price = float(prefix["close"].iloc[-1])
            ref_evals = evaluate_all_playbooks(ref, price, client=None)
            opt_evals = evaluate_all_playbooks(opt, price, client=None)
            assert [e.setup_status for e in ref_evals] == [e.setup_status for e in opt_evals]
            assert [e.quality_score for e in ref_evals] == [e.quality_score for e in opt_evals]
        checked += 1

    assert checked > 5
