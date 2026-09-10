"""Phase 5.2 §32 — real-data smoke test. Finds at least one real historical
Phase 5.1 TRIGGERED occurrence on AAPL/NVDA and runs it through Phase 5.2 end
to end. This is a MECHANICAL verification only (§32) — one real trade proves
nothing statistically; see engine/backtest/outcomes.py for the actual
outcome logic and tests/test_backtest_outcomes.py for correctness proof.
Opt-in only (network + credentials), consistent with the other live-Alpaca
tests in this suite — never part of the default/offline test run.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_ALPACA_TESTS") != "1",
    reason="live/network Alpaca test — opt in with RUN_LIVE_ALPACA_TESTS=1 (requires real .env credentials)",
)


def _find_and_simulate(symbol: str):
    from engine.backtest.bar_source import AdjustmentStatus, DataProvenance, DataType, RunMode
    from engine.backtest.calendar import filter_to_regular_trading_hours
    from engine.backtest.contracts import PlaybookPin
    from engine.backtest.outcomes import default_execution_policy, simulate_trade_outcome
    from engine.backtest.replay import run_replay
    from engine.data_provider.alpaca_provider import AlpacaProvider
    from engine.market_state.market_intelligence import build_snapshot
    from engine.playbooks.engine import evaluate_all_playbooks

    provider = AlpacaProvider()
    end = datetime.now(timezone.utc) - timedelta(days=2)
    start = end - timedelta(days=25)
    raw = provider.get_ohlcv(symbol, "5min", start, end, adjustment="split")  # Phase 5.1P policy: split-adjusted
    df = filter_to_regular_trading_hours(raw)  # Phase 5.1P policy: RTH only
    if len(df) < 100:
        return None

    provenance = DataProvenance(
        provider="alpaca", data_type=DataType.REAL_MARKET_DATA, adjustment_status=AdjustmentStatus.SPLIT_ADJUSTED,
        symbol=symbol, timeframe="5min", range_start=df.index[0], range_end=df.index[-1], bar_count=len(df),
    )
    pins = (
        PlaybookPin("TREND_PULLBACK_LONG", "1.0"), PlaybookPin("TREND_PULLBACK_SHORT", "1.0"),
        PlaybookPin("BREAKOUT_RETEST_LONG", "1.0"), PlaybookPin("BREAKOUT_RETEST_SHORT", "1.0"),
        PlaybookPin("RANGE_MEAN_REVERSION_LONG", "1.0"), PlaybookPin("RANGE_MEAN_REVERSION_SHORT", "1.0"),
        PlaybookPin("OPENING_RANGE_BREAKOUT_LONG", "1.0"), PlaybookPin("OPENING_RANGE_BREAKOUT_SHORT", "1.0"),
    )
    result = run_replay(
        bars_df=df, symbol=symbol, timeframe="5min", provenance=provenance, run_mode=RunMode.PRODUCTION,
        playbook_pins=pins, run_id=f"phase52-smoke-{symbol}", allow_unverified_adjustment=False,
        enforce_session_integrity=True, use_optimized_market_state=True,  # Phase 5.1P — keeps this smoke test practical
    )
    triggers = [e for e in result.evaluations if e.is_new_trigger_occurrence]
    if not triggers:
        return None

    trigger_row = triggers[0]
    trigger_ts = trigger_row.timestamp
    prefix = df.loc[:trigger_ts]
    snapshot = build_snapshot(
        symbol=symbol, timeframe="5min", df=prefix, data_source="alpaca", timeframe_minutes=5, now=trigger_ts,
    )
    evals = evaluate_all_playbooks(snapshot, float(prefix["close"].iloc[-1]), client=None)
    full_evaluation = next(e for e in evals if e.playbook_id == trigger_row.playbook_id)
    assert full_evaluation.setup_status == "TRIGGERED"

    policy = default_execution_policy()
    outcome = simulate_trade_outcome(
        full_evaluation, symbol, "5min", trigger_ts, df, policy, occurrence_id=f"smoke-{symbol}-1",
        market_regime_at_trigger=snapshot.market_state,
        volatility_regime_at_trigger=(snapshot.volatility.level if snapshot.volatility else None),
    )
    return outcome


def test_real_aapl_or_nvda_triggered_occurrence_end_to_end():
    outcome = None
    for symbol in ("AAPL", "NVDA"):
        outcome = _find_and_simulate(symbol)
        if outcome is not None:
            break

    if outcome is None:
        pytest.skip("no real TRIGGERED occurrence found for AAPL/NVDA in the sampled window — not a failure, just no signal")

    print(f"\n--- Phase 5.2 real-data smoke test result ---")
    print(f"symbol={outcome.symbol} playbook={outcome.playbook_id} direction={outcome.direction}")
    print(f"trigger_timestamp={outcome.trigger_timestamp}")
    print(f"entry_status={outcome.entry_status} entry_timestamp={outcome.entry_timestamp} entry_price={outcome.entry_price}")
    print(f"stop_price={outcome.stop_price} target1_price={outcome.target1_price}")
    print(f"exit_status={outcome.exit_status} exit_timestamp={outcome.exit_timestamp} exit_price={outcome.exit_price}")
    print(f"gross_R={outcome.gross_R} mfe_R={outcome.mfe_R} mae_R={outcome.mae_R}")
    print(f"holding_period_bars={outcome.holding_period_bars} same_bar_collision={outcome.same_bar_collision}")

    assert outcome.entry_status in ("FILLED", "INVALIDATED_BEFORE_FILL", "EXPIRED_UNFILLED", "INVALID_STOP_GEOMETRY")
    if outcome.entry_status == "FILLED":
        assert outcome.exit_status in ("TARGET1", "STOP", "EXPIRED_EOD", "TIMEOUT", "END_OF_DATA")
        assert outcome.gross_R is not None
