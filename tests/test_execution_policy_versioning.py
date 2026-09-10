"""Phase 5.2V §13 — ExecutionPolicy version safeguards, and §9 — EXPIRED_EOD/
TIMEOUT produce a genuine executable exit price eligible for realized
statistics (integration with statistics_policy), while END_OF_DATA does not.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.backtest.outcomes import ExecutionPolicy, default_execution_policy, simulate_trade_outcome
from engine.backtest.statistics_policy import is_realized_trade
from engine.trade.candidate import EntryMethod, TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.targets import TargetResult
from engine.playbooks.evaluation import PlaybookEvaluation


def _evaluation():
    candidate = TradeCandidate(
        setup_type="TEST_PB", direction="LONG", status="TRIGGERED", structural_level=100.0,
        entry_zone_low=None, entry_zone_high=None, entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value,
    )
    return PlaybookEvaluation(
        playbook_id="TEST_PB", playbook_version="1.0", name="Test", direction="LONG", family="TEST",
        eligibility_status="ELIGIBLE", setup_status="TRIGGERED", candidate=candidate,
        invalidation=InvalidationResult(stop_price=50.0, structural_level=100.0, atr_buffer=0.0, reason="t"),
        targets=(TargetResult(price=500.0, reason="t", distance=400.0, reward_per_unit=400.0, r_multiple=8.0),),
        entry_price=100.0, quality_score=70, quality_band="HIGH",
    )


def _full_session_bars():
    idx = pd.date_range("2024-01-02 09:30", periods=78, freq="5min", tz="America/New_York")
    df = pd.DataFrame(
        {"open": [100.0] * 78, "high": [101.0] * 78, "low": [99.5] * 78, "close": [100.0] * 78,
         "volume": [1000] * 78},
        index=idx,
    )
    return df.tz_convert("UTC")


def _daily_bars(n):
    idx = pd.date_range("2024-01-02", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n, "volume": [1000] * n},
        index=idx,
    )


# ---- §13 execution-policy version safeguards ----


def test_execution_policy_version_is_required_and_retained_on_outcome():
    policy = default_execution_policy()
    bars = _full_session_bars()
    outcome = simulate_trade_outcome(_evaluation(), "TEST", "5min", bars.index[0], bars, policy, occurrence_id="x")
    assert outcome.execution_policy_version == policy.version == "v1"


def test_changing_max_entry_wait_bars_requires_a_different_policy_version_to_be_distinguishable():
    default_policy = default_execution_policy()
    faster_policy = ExecutionPolicy(
        version="v2-shorter-wait", entry_activation_rule=default_policy.entry_activation_rule,
        max_entry_wait_bars=5, entry_expiry_rule=default_policy.entry_expiry_rule,
        same_bar_collision_rule=default_policy.same_bar_collision_rule, target_policy=default_policy.target_policy,
        intraday_overnight_policy=default_policy.intraday_overnight_policy,
        daily_max_holding_bars=default_policy.daily_max_holding_bars, gap_policy=default_policy.gap_policy,
        cost_model_version=default_policy.cost_model_version,
    )
    assert default_policy.max_entry_wait_bars == 10
    assert faster_policy.max_entry_wait_bars == 5
    assert default_policy.version != faster_policy.version  # a behavior change forces a version change

    bars = _full_session_bars()
    ev = _evaluation()
    out_default = simulate_trade_outcome(ev, "TEST", "5min", bars.index[0], bars, default_policy, occurrence_id="a")
    out_faster = simulate_trade_outcome(ev, "TEST", "5min", bars.index[0], bars, faster_policy, occurrence_id="b")
    assert out_default.execution_policy_version != out_faster.execution_policy_version


def test_execution_policy_requires_explicit_version_string():
    with pytest.raises(ValueError):
        ExecutionPolicy(
            version="", entry_activation_rule="x", max_entry_wait_bars=10, entry_expiry_rule="x",
            same_bar_collision_rule="x", target_policy="x", intraday_overnight_policy="x",
            daily_max_holding_bars=20, gap_policy="x", cost_model_version="x",
        )


def test_execution_policy_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        ExecutionPolicy(
            version="bad", entry_activation_rule="x", max_entry_wait_bars=0, entry_expiry_rule="x",
            same_bar_collision_rule="x", target_policy="x", intraday_overnight_policy="x",
            daily_max_holding_bars=20, gap_policy="x", cost_model_version="x",
        )
    with pytest.raises(ValueError):
        ExecutionPolicy(
            version="bad", entry_activation_rule="x", max_entry_wait_bars=10, entry_expiry_rule="x",
            same_bar_collision_rule="x", target_policy="x", intraday_overnight_policy="x",
            daily_max_holding_bars=0, gap_policy="x", cost_model_version="x",
        )


# ---- §9 EXPIRED_EOD/TIMEOUT executable exit vs END_OF_DATA ----


def test_expired_eod_has_a_real_exit_price_and_is_realized_eligible():
    bars = _full_session_bars()
    out = simulate_trade_outcome(_evaluation(), "TEST", "5min", bars.index[0], bars, default_execution_policy(), occurrence_id="eod")
    assert out.exit_status == "EXPIRED_EOD"
    assert out.exit_price is not None
    assert out.gross_R is not None
    assert is_realized_trade(out) is True


def test_timeout_has_a_real_exit_price_and_is_realized_eligible():
    bars = _daily_bars(26)
    out = simulate_trade_outcome(_evaluation(), "TEST", "1day", bars.index[0], bars, default_execution_policy(), occurrence_id="to")
    assert out.exit_status == "TIMEOUT"
    assert out.exit_price is not None
    assert out.gross_R is not None
    assert is_realized_trade(out) is True


def test_end_of_data_has_a_mark_price_but_is_excluded_from_realized():
    bars = _daily_bars(6)  # far short of the 20-day holding limit
    out = simulate_trade_outcome(_evaluation(), "TEST", "1day", bars.index[0], bars, default_execution_policy(), occurrence_id="eod2")
    assert out.exit_status == "END_OF_DATA"
    assert out.exit_price is not None  # a mark, not a realized exit
    assert is_realized_trade(out) is False
