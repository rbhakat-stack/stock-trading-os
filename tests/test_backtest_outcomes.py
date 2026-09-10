"""Phase 5.2 — deterministic trade-outcome engine tests (§30/§31).

Fixtures build a minimal-but-real PlaybookEvaluation (with a real
TradeCandidate/InvalidationResult/TargetResult) rather than a duplicate
"fake evaluation" shape, so every test exercises simulate_trade_outcome
exactly as a real replay-produced trigger would.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from engine.backtest.outcomes import (
    ExecutionPolicy, default_execution_policy, simulate_trade_outcome,
)
from engine.trade.candidate import EntryMethod, TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.targets import TargetResult
from engine.playbooks.evaluation import PlaybookEvaluation

POLICY = default_execution_policy()


def _bars(rows: list[tuple], start="2024-01-02 09:30", freq="5min", tz="America/New_York") -> pd.DataFrame:
    """rows: list of (open, high, low, close) tuples, one 5-min RTH bar each."""
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=tz)
    df = pd.DataFrame(
        {
            "open": [r[0] for r in rows], "high": [r[1] for r in rows],
            "low": [r[2] for r in rows], "close": [r[3] for r in rows],
            "volume": [10_000] * len(rows),
        },
        index=idx,
    )
    return df.tz_convert("UTC")


def _full_session_bars(rows: list[tuple], date_str="2024-01-02") -> pd.DataFrame:
    """Pads `rows` out to a full 78-bar RTH session with flat filler bars
    (all equal to the last real row) so `check_session_integrity` sees a
    complete session (needed for EXPIRED_EOD tests)."""
    if len(rows) < 78:
        filler = rows[-1]
        rows = rows + [filler] * (78 - len(rows))
    return _bars(rows, start=f"{date_str} 09:30")


def _evaluation(
    direction="LONG", entry_price=100.0, entry_zone_low=None, entry_zone_high=None,
    stop_price=98.0, target1_price=104.0, target2_price=None,
    entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value, playbook_id="TEST_PLAYBOOK",
    quality_score=70,
) -> PlaybookEvaluation:
    candidate = TradeCandidate(
        setup_type=playbook_id, direction=direction, status="TRIGGERED",
        structural_level=entry_price, entry_zone_low=entry_zone_low, entry_zone_high=entry_zone_high,
        entry_method=entry_method,
    )
    invalidation = (
        InvalidationResult(stop_price=stop_price, structural_level=entry_price, atr_buffer=0.0, reason="test")
        if stop_price is not None else None
    )
    targets = []
    if target1_price is not None:
        dist = abs(target1_price - entry_price) if entry_price is not None else 1.0
        targets.append(TargetResult(price=target1_price, reason="t1", distance=dist, reward_per_unit=dist, r_multiple=1.0))
    if target2_price is not None:
        dist2 = abs(target2_price - entry_price) if entry_price is not None else 2.0
        targets.append(TargetResult(price=target2_price, reason="t2", distance=dist2, reward_per_unit=dist2, r_multiple=2.0))
    return PlaybookEvaluation(
        playbook_id=playbook_id, playbook_version="1.0", name="Test Playbook", direction=direction, family="TEST",
        eligibility_status="ELIGIBLE", setup_status="TRIGGERED", candidate=candidate, invalidation=invalidation,
        targets=tuple(targets), entry_price=entry_price, entry_zone_low=entry_zone_low, entry_zone_high=entry_zone_high,
        quality_score=quality_score, quality_band="HIGH",
    )


def _run(evaluation, bars_df, trigger_timestamp=None, timeframe="5min", policy=POLICY, **kw):
    trigger_timestamp = trigger_timestamp if trigger_timestamp is not None else bars_df.index[0]
    return simulate_trade_outcome(
        evaluation, "TEST", timeframe, trigger_timestamp, bars_df, policy, occurrence_id="occ-1", **kw,
    )


# =============================================================================
# LONG — market-style entries
# =============================================================================


def test_long_market_next_bar_fill():
    bars = _bars([(100, 100, 100, 100), (101, 102, 100.5, 101.5), (101.5, 106, 101, 105)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 101.0  # next bar's open
    assert out.entry_timestamp == bars.index[1]


def test_long_market_target_hit():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.exit_price == 104.0
    assert out.gross_R == pytest.approx((104.0 - 100.0) / (100.0 - 98.0))


def test_long_market_stop_hit():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 101, 97, 97.5)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.exit_price == 98.0
    assert out.gross_R == pytest.approx(-1.0)


def test_long_target_then_stop_on_different_bars():
    bars = _bars([
        (100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 105, 100, 104.5), (104.5, 105, 96, 97),
    ])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"  # target hit on bar 2, before the stop-hitting bar 3 is ever reached
    assert out.exit_timestamp == bars.index[2]


def test_long_stop_then_target_on_different_bars():
    bars = _bars([
        (100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 101, 97, 97.5), (97.5, 110, 97, 105),
    ])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.exit_timestamp == bars.index[2]


def test_long_stop_and_target_same_bar_assumes_stop():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 97, 99)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.same_bar_collision is True
    assert out.exit_price == 98.0


def test_long_gap_below_stop_exit_at_actual_open_may_exceed_1R():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (95, 96, 94, 95)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.exit_price == 95.0  # actual open, not the nominal -1R stop price
    assert out.gross_R < -1.0


def test_long_favorable_gap_beyond_target_fills_at_better_open():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (110, 112, 109, 111)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.exit_price == 110.0  # actual open, better than the nominal target
    assert out.gross_R == pytest.approx((110.0 - 100.0) / 2.0)
    assert out.gross_R > (104.0 - 100.0) / 2.0  # strictly better than the nominal target's R would have been


def test_long_mfe_mae():
    bars = _bars([
        (100, 100, 100, 100), (100, 103, 99.5, 102), (102, 108, 101, 103), (103, 104, 96, 97.5),
    ])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=90.0, target1_price=200.0)
    out = _run(ev, bars)
    # entry fills at bar1 open=100; MFE tracks the running max favorable excursion across held bars
    assert out.mfe_R == pytest.approx((108 - 100) / (100 - 90))
    assert out.mae_R == pytest.approx((100 - 96) / (100 - 90))


def test_r_calculation_long():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.initial_risk_per_share == pytest.approx(abs(100.0 - 98.0))
    assert out.gross_R == pytest.approx((out.exit_price - out.entry_price) / out.initial_risk_per_share)


# =============================================================================
# SHORT — mirror of every relevant LONG case
# =============================================================================


def test_short_market_next_bar_fill():
    bars = _bars([(100, 100, 100, 100), (99, 99.5, 98, 98.5), (98.5, 99, 94, 95)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 99.0


def test_short_target_hit():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 100.5, 95, 95.5)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.exit_price == 96.0
    assert out.gross_R == pytest.approx((100.0 - 96.0) / (102.0 - 100.0))


def test_short_stop_hit():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 103, 99.5, 102.5)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.exit_price == 102.0
    assert out.gross_R == pytest.approx(-1.0)


def test_short_stop_and_target_same_bar_assumes_stop():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 103, 94, 96)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.same_bar_collision is True


def test_short_gap_above_stop_exit_at_actual_open():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (106, 107, 105, 106)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.exit_price == 106.0
    assert out.gross_R < -1.0


def test_short_favorable_gap_beyond_target():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (90, 91, 89, 90)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.exit_price == 90.0
    assert out.gross_R == pytest.approx((100.0 - 90.0) / 2.0)
    assert out.gross_R > (100.0 - 96.0) / 2.0  # strictly better than the nominal target's R would have been


def test_short_mfe_mae():
    bars = _bars([
        (100, 100, 100, 100), (100, 100.5, 97, 98), (98, 99, 92, 93), (93, 104, 99, 103),
    ])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=110.0, target1_price=0.0)
    out = _run(ev, bars)
    assert out.mfe_R == pytest.approx((100 - 92) / (110 - 100))
    assert out.mae_R == pytest.approx((104 - 100) / (110 - 100))


def test_wrong_side_stop_short_is_invalid_geometry():
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=98.0, target1_price=90.0)  # stop below entry: wrong side for SHORT
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100)])
    out = _run(ev, bars)
    assert out.entry_status == "INVALID_STOP_GEOMETRY"
    assert out.gross_R is None


# =============================================================================
# Zone/LIMIT entries
# =============================================================================


def test_long_unfilled_zone_entry_price_never_returns():
    bars = _bars([(100, 100, 100, 100)] + [(110, 111, 109, 110)] * 12)
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=92.0, target1_price=104.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "EXPIRED_UNFILLED"
    assert out.entry_price is None
    assert out.holding_period_bars is None


def test_long_delayed_zone_fill_within_window():
    bars = _bars(
        [(100, 100, 100, 100), (100, 101, 99, 100.5), (100.5, 101, 99, 100.2),
         (100.2, 100.5, 95, 96), (96, 100, 95.5, 99)]
    )
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=90.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 96.0  # exact limit price, not the bar's more favorable low
    assert out.entry_timestamp == bars.index[3]


def test_long_zone_gap_fill_at_open_is_better_than_limit():
    bars = _bars([(100, 100, 100, 100), (93, 94, 92, 93.5)])
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=90.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 93.0  # actual open, better than the 96.0 limit


def test_long_zone_never_grants_more_favorable_than_limit_price():
    """§6 — the bar's low crosses deep through the zone (but NOT through the
    stop, kept well below), so the fill must still be AT the limit price,
    never the more optimistic bar low."""
    bars = _bars([(100, 100, 100, 100), (100.5, 101, 85, 99)])  # low=85, way below the 96.0 limit
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=80.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 96.0


def test_long_zone_entry_expires_after_wait_window():
    policy = ExecutionPolicy(
        version="test-short-wait", entry_activation_rule="x", max_entry_wait_bars=2, entry_expiry_rule="x",
        same_bar_collision_rule="ASSUME_STOP_FIRST", target_policy="FULL_EXIT_AT_TARGET1",
        intraday_overnight_policy="x", daily_max_holding_bars=20, gap_policy="x",
        cost_model_version="ZERO_COST_RESEARCH_V1",
    )
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    ev = _evaluation(
        direction="LONG", entry_price=90.0, entry_zone_low=88.0, entry_zone_high=90.0,
        stop_price=85.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars, policy=policy)
    assert out.entry_status == "EXPIRED_UNFILLED"


def test_long_zone_gapped_through_stop_before_fill_is_invalidated():
    bars = _bars([(100, 100, 100, 100), (80, 81, 79, 80)])
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=90.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "INVALIDATED_BEFORE_FILL"
    assert out.entry_price is None
    assert out.holding_period_bars is None


def test_long_zone_same_bar_touches_zone_and_stop_before_fill_is_invalidated():
    bars = _bars([(100, 100, 100, 100), (100.5, 101, 85, 99)])  # low=85 crosses BOTH the zone and the 90 stop
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=90.0, target1_price=110.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "INVALIDATED_BEFORE_FILL"
    assert out.same_bar_collision is True


def test_short_zone_delayed_fill_and_gap():
    bars = _bars([(100, 100, 100, 100), (100.5, 101, 99, 100.2), (106, 107, 105, 106)])
    ev = _evaluation(
        direction="SHORT", entry_price=104.0, entry_zone_low=104.0, entry_zone_high=106.0,
        stop_price=110.0, target1_price=90.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.entry_status == "FILLED"
    assert out.entry_price == 106.0  # actual open, better than the 104.0 limit for a short


# =============================================================================
# Invalidated-before-fill via Phase 4's OWN structural signal (monkeypatched
# evaluate_all_playbooks — isolated unit test of that specific branch; the
# price-based invalidation paths above already exercise the real code path
# end to end without needing a mock).
# =============================================================================


def test_structural_invalidation_before_fill_reuses_phase4_setup_status(monkeypatch):
    import engine.backtest.outcomes as outcomes_mod

    class _FakeEval:
        def __init__(self, playbook_id, status):
            self.playbook_id = playbook_id
            self.setup_status = status

    def fake_evaluate_all_playbooks(snapshot, current_price, client=None):
        return [_FakeEval("MY_PB", "INVALIDATED")]

    monkeypatch.setattr(outcomes_mod, "evaluate_all_playbooks", fake_evaluate_all_playbooks)

    bars = _bars([(100, 100, 100, 100)] + [(100, 101, 99.5, 100)] * 5)
    ev = _evaluation(
        direction="LONG", entry_price=90.0, entry_zone_low=88.0, entry_zone_high=90.0,
        stop_price=85.0, target1_price=120.0, entry_method=EntryMethod.LIMIT.value, playbook_id="MY_PB",
    )
    out = _run(ev, bars)
    assert out.entry_status == "INVALIDATED_BEFORE_FILL"
    assert "INVALIDATED" in out.evidence["reason"]


# =============================================================================
# Intraday EOD expiry / end-of-data
# =============================================================================


def test_intraday_expires_at_end_of_session_when_neither_hit():
    bars = _full_session_bars([(100, 100, 100, 100), (100, 101, 99.5, 100)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars)
    assert out.exit_status == "EXPIRED_EOD"
    assert out.exit_timestamp == bars.index[-1]


def test_intraday_end_of_data_when_session_looks_incomplete():
    # only 40 of 78 bars present -> ends ~03:10 before scheduled 16:00 close, well beyond the 30-min tolerance
    bars = _bars([(100, 100, 100, 100)] + [(100, 101, 99.5, 100)] * 39)
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars)
    assert out.exit_status == "END_OF_DATA"
    assert "still open" in out.evidence["note"]


def test_never_filled_holding_period_is_null_not_zero():
    bars = _bars([(100, 100, 100, 100)] + [(110, 111, 109, 110)] * 12)
    ev = _evaluation(
        direction="LONG", entry_price=96.0, entry_zone_low=94.0, entry_zone_high=96.0,
        stop_price=92.0, target1_price=104.0, entry_method=EntryMethod.LIMIT.value,
    )
    out = _run(ev, bars)
    assert out.holding_period_bars is None
    assert out.holding_period_minutes is None


# =============================================================================
# Daily timeframe: timeout, end of data
# =============================================================================


def _daily_bars(rows):
    idx = pd.date_range("2024-01-02", periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": [r[0] for r in rows], "high": [r[1] for r in rows], "low": [r[2] for r in rows],
         "close": [r[3] for r in rows], "volume": [10_000] * len(rows)},
        index=idx,
    )


def test_daily_timeout_after_max_holding_bars():
    rows = [(100, 100, 100, 100)] + [(100, 101, 99, 100)] * 25
    bars = _daily_bars(rows)
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars, timeframe="1day")
    assert out.exit_status == "TIMEOUT"
    # entry is day 1 of the hold; TIMEOUT fires once daily_max_holding_bars (20)
    # days have been HELD, i.e. 19 bars elapsed since entry (the index delta) —
    # this also makes a same-bar fill+exit correctly report 0, not 1.
    assert out.holding_period_bars == 19


def test_daily_end_of_data_before_timeout():
    rows = [(100, 100, 100, 100)] + [(100, 101, 99, 100)] * 5  # only 6 bars total, well short of 20
    bars = _daily_bars(rows)
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars, timeframe="1day")
    assert out.exit_status == "END_OF_DATA"


def test_daily_playbook_version_and_policy_version_retained():
    rows = [(100, 100, 100, 100), (100, 101, 99, 100.5), (100.5, 106, 100, 105)]
    bars = _daily_bars(rows)
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars, timeframe="1day")
    assert out.playbook_version == "1.0"
    assert out.execution_policy_version == "v1"


# =============================================================================
# End of data at entry stage; missing/negative geometry
# =============================================================================


def test_end_of_data_right_at_trigger_market_entry():
    bars = _bars([(100, 100, 100, 100)])  # trigger is the ONLY bar -> no next bar exists at all
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out = _run(ev, bars)
    assert out.entry_status == "EXPIRED_UNFILLED"


def test_missing_stop_is_invalid_geometry():
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=None, target1_price=104.0)
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100)])
    out = _run(ev, bars)
    assert out.entry_status == "INVALID_STOP_GEOMETRY"


def test_entry_equals_stop_is_invalid_geometry():
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=100.0, target1_price=104.0)
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100)])
    out = _run(ev, bars)
    assert out.entry_status == "INVALID_STOP_GEOMETRY"


def test_negative_price_is_invalid_geometry():
    ev = _evaluation(direction="LONG", entry_price=-5.0, stop_price=-8.0, target1_price=10.0)
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100)])
    out = _run(ev, bars)
    assert out.entry_status == "INVALID_STOP_GEOMETRY"


def test_zero_price_is_invalid_geometry():
    ev = _evaluation(direction="LONG", entry_price=0.0, stop_price=-2.0, target1_price=10.0)
    bars = _bars([(100, 100, 100, 100), (100, 101, 99, 100)])
    out = _run(ev, bars)
    assert out.entry_status == "INVALID_STOP_GEOMETRY"


# =============================================================================
# Half-day / DST session boundaries (reuses the exact fixtures/dates Phase
# 5.1's own session-integrity tests use).
# =============================================================================


def test_half_day_session_does_not_fabricate_a_full_close():
    # 2024-01-03 truncated to 42 bars (ends 13:00 ET), mirroring
    # tests/test_backtest_replay_session.py's own half-day fixture exactly.
    rows = [(100, 100, 100, 100)] + [(100, 101, 99.5, 100)] * 41
    bars = _bars(rows, start="2024-01-03 09:30")
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars)
    assert out.exit_status == "END_OF_DATA"  # never fabricated as a real 16:00 EXPIRED_EOD close


def test_dst_boundary_session_replay_runs_cleanly():
    idx1 = pd.date_range("2024-03-08 09:30", periods=78, freq="5min", tz="America/New_York")
    idx2 = pd.date_range("2024-03-11 09:30", periods=78, freq="5min", tz="America/New_York")  # DST spring-forward
    rows1 = [(100.0, 100.5, 99.5, 100.0)] * 78
    rows2 = [(100.0, 100.5, 99.5, 100.0)] * 78
    df1 = pd.DataFrame({"open": [r[0] for r in rows1], "high": [r[1] for r in rows1], "low": [r[2] for r in rows1],
                         "close": [r[3] for r in rows1], "volume": [1000] * 78}, index=idx1)
    df2 = pd.DataFrame({"open": [r[0] for r in rows2], "high": [r[1] for r in rows2], "low": [r[2] for r in rows2],
                         "close": [r[3] for r in rows2], "volume": [1000] * 78}, index=idx2)
    bars = pd.concat([df1, df2]).tz_convert("UTC")
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=50.0, target1_price=500.0)
    out = _run(ev, bars, trigger_timestamp=bars.index[0])
    assert out.exit_status == "EXPIRED_EOD"
    assert out.exit_timestamp.tz_convert("America/New_York").date() == date(2024, 3, 8)


# =============================================================================
# Determinism / reproducibility
# =============================================================================


def test_same_input_same_policy_identical_outcome():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out1 = _run(ev, bars)
    out2 = _run(ev, bars)
    assert out1 == out2


def test_trigger_timestamp_not_in_bars_raises():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    bad_ts = pd.Timestamp("2099-01-01", tz="UTC")
    with pytest.raises(ValueError):
        _run(ev, bars, trigger_timestamp=bad_ts)


def test_unsupported_entry_method_raises_not_implemented():
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0,
                      entry_method=EntryMethod.STOP_TRIGGER.value)
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5)])
    with pytest.raises(NotImplementedError):
        _run(ev, bars)


# =============================================================================
# §31 — property / invariant tests
# =============================================================================


def test_invariant_long_target_above_entry_gives_positive_r():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 110, 100, 108)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=105.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.gross_R > 0


def test_invariant_long_stop_below_entry_gives_negative_r():
    bars = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 101, 96, 97)])
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=200.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.gross_R < 0


def test_invariant_short_target_below_entry_gives_positive_r():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 100.5, 93, 94)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=95.0)
    out = _run(ev, bars)
    assert out.exit_status == "TARGET1"
    assert out.gross_R > 0


def test_invariant_short_stop_above_entry_gives_negative_r():
    bars = _bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 103, 99.5, 102.5)])
    ev = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=0.0)
    out = _run(ev, bars)
    assert out.exit_status == "STOP"
    assert out.gross_R < 0


def test_invariant_mfe_never_decreases_with_more_bars():
    """Processing prefixes of increasing length can never SHRINK the
    reported MFE for the bar range already covered."""
    rows = [(100, 100, 100, 100), (100, 105, 99.5, 104), (104, 104.5, 103, 104),
            (104, 108, 103, 107), (107, 107.5, 106, 107)]
    bars = _bars(rows)
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=90.0, target1_price=500.0)
    prev_mfe = 0.0
    for n in range(2, len(rows) + 1):
        out = _run(ev, bars.iloc[:n])
        if out.mfe_R is not None:
            assert out.mfe_R >= prev_mfe - 1e-9
            prev_mfe = out.mfe_R


def test_invariant_outcome_never_looks_at_bars_after_exit():
    bars_short = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105)])
    bars_long = _bars(
        [(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105), (105, 999, 999, 999)]
    )
    ev = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    out_short = _run(ev, bars_short)
    out_long = _run(ev, bars_long)
    assert out_short.exit_status == out_long.exit_status == "TARGET1"
    assert out_short.exit_price == out_long.exit_price
    assert out_short.mfe_R == out_long.mfe_R  # the absurd future bar must never influence this


def test_invariant_long_short_symmetric_geometry_produce_symmetric_r():
    bars_long = _bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 106, 100, 105)])
    bars_short = _bars([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (99.5, 100, 94, 95)])
    ev_long = _evaluation(direction="LONG", entry_price=100.0, stop_price=98.0, target1_price=104.0)
    ev_short = _evaluation(direction="SHORT", entry_price=100.0, stop_price=102.0, target1_price=96.0)
    out_long = _run(ev_long, bars_long)
    out_short = _run(ev_short, bars_short)
    assert out_long.exit_status == out_short.exit_status == "TARGET1"
    assert out_long.gross_R == pytest.approx(out_short.gross_R)
