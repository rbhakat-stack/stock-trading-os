"""Phase 5.2V §8/§9/§15 — StatisticalPopulationPolicy classification tests.
No statistic is computed here — only population membership."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.backtest.statistics_policy import (
    DEFAULT_STATISTICAL_POPULATION_POLICY, classify_populations, is_excluded_from_realized_statistics,
    is_fill_rate_eligible, is_filled, is_realized_trade, is_valid_occurrence,
)
from engine.backtest.outcomes import HistoricalTradeOutcome

_NOW = datetime(2024, 1, 2, tzinfo=timezone.utc)


def _outcome(entry_status: str, exit_status: str | None = None) -> HistoricalTradeOutcome:
    return HistoricalTradeOutcome(
        occurrence_id="occ", symbol="TEST", timeframe="5min", playbook_id="TEST_PB", playbook_version="1.0",
        family="TEST", direction="LONG", trigger_timestamp=_NOW, entry_status=entry_status,
        entry_timestamp=_NOW if entry_status == "FILLED" else None,
        entry_price=100.0 if entry_status == "FILLED" else None,
        entry_zone_low=None, entry_zone_high=None, stop_price=98.0, target1_price=104.0, target2_price=None,
        exit_status=exit_status, exit_timestamp=_NOW if exit_status else None,
        exit_price=100.0 if exit_status else None,
        initial_risk_per_share=2.0 if entry_status == "FILLED" else None,
        gross_R=1.0 if exit_status else None, net_R=1.0 if exit_status else None,
        mfe_R=1.0 if entry_status == "FILLED" else None, mae_R=0.1 if entry_status == "FILLED" else None,
        holding_period_bars=1 if entry_status == "FILLED" else None,
        holding_period_minutes=5.0 if entry_status == "FILLED" else None, same_bar_collision=False,
        market_regime_at_trigger=None, volatility_regime_at_trigger=None, quality_score_at_trigger=70,
        execution_policy_version="v1",
    )


def test_policy_is_versioned():
    assert DEFAULT_STATISTICAL_POPULATION_POLICY.version == "v1"
    with pytest.raises(ValueError):
        from engine.backtest.statistics_policy import StatisticalPopulationPolicy
        StatisticalPopulationPolicy(version="", description="x")


# ---- §8-A occurrence population ----


def test_valid_occurrence_includes_every_entry_status_except_invalid_geometry():
    for status in ("FILLED", "EXPIRED_UNFILLED", "INVALIDATED_BEFORE_FILL"):
        assert is_valid_occurrence(_outcome(status)) is True
    assert is_valid_occurrence(_outcome("INVALID_STOP_GEOMETRY")) is False


# ---- §8-B fill-rate population ----


def test_fill_rate_eligible_matches_valid_occurrence_but_excludes_invalid_geometry():
    assert is_fill_rate_eligible(_outcome("FILLED", "TARGET1")) is True
    assert is_fill_rate_eligible(_outcome("EXPIRED_UNFILLED")) is True
    assert is_fill_rate_eligible(_outcome("INVALIDATED_BEFORE_FILL")) is True
    assert is_fill_rate_eligible(_outcome("INVALID_STOP_GEOMETRY")) is False


def test_is_filled_distinguishes_filled_from_not_filled():
    assert is_filled(_outcome("FILLED", "TARGET1")) is True
    assert is_filled(_outcome("EXPIRED_UNFILLED")) is False
    assert is_filled(_outcome("INVALIDATED_BEFORE_FILL")) is False


# ---- §8-C/§9 realized-trade population ----


def test_realized_trade_includes_target1_stop_expired_eod_timeout():
    for exit_status in ("TARGET1", "STOP", "EXPIRED_EOD", "TIMEOUT"):
        assert is_realized_trade(_outcome("FILLED", exit_status)) is True, exit_status


def test_realized_trade_excludes_end_of_data():
    assert is_realized_trade(_outcome("FILLED", "END_OF_DATA")) is False


def test_realized_trade_excludes_never_filled_and_invalid_geometry():
    assert is_realized_trade(_outcome("EXPIRED_UNFILLED")) is False
    assert is_realized_trade(_outcome("INVALIDATED_BEFORE_FILL")) is False
    assert is_realized_trade(_outcome("INVALID_STOP_GEOMETRY")) is False


def test_excluded_from_realized_is_the_exact_complement():
    for status, exit_status in [
        ("FILLED", "TARGET1"), ("FILLED", "STOP"), ("FILLED", "EXPIRED_EOD"), ("FILLED", "TIMEOUT"),
        ("FILLED", "END_OF_DATA"), ("EXPIRED_UNFILLED", None), ("INVALIDATED_BEFORE_FILL", None),
        ("INVALID_STOP_GEOMETRY", None),
    ]:
        o = _outcome(status, exit_status)
        assert is_excluded_from_realized_statistics(o) == (not is_realized_trade(o))


def test_end_of_data_excluded_but_expired_eod_and_timeout_included_side_by_side():
    end_of_data = _outcome("FILLED", "END_OF_DATA")
    expired_eod = _outcome("FILLED", "EXPIRED_EOD")
    timeout = _outcome("FILLED", "TIMEOUT")
    assert is_realized_trade(end_of_data) is False
    assert is_realized_trade(expired_eod) is True
    assert is_realized_trade(timeout) is True


# ---- classify_populations: nothing is ever silently hidden ----


def test_classify_populations_never_drops_a_valid_occurrence():
    for status, exit_status in [
        ("FILLED", "TARGET1"), ("FILLED", "END_OF_DATA"), ("EXPIRED_UNFILLED", None),
        ("INVALIDATED_BEFORE_FILL", None),
    ]:
        pops = classify_populations(_outcome(status, exit_status))
        assert "OCCURRENCE" in pops
        assert "FILL_RATE_ELIGIBLE" in pops


def test_classify_populations_excludes_invalid_geometry_from_every_population():
    pops = classify_populations(_outcome("INVALID_STOP_GEOMETRY"))
    assert pops == ()


def test_classify_populations_realized_trade_only_for_genuine_exits():
    assert "REALIZED_TRADE" in classify_populations(_outcome("FILLED", "TARGET1"))
    assert "REALIZED_TRADE" not in classify_populations(_outcome("FILLED", "END_OF_DATA"))
    assert "REALIZED_TRADE" not in classify_populations(_outcome("EXPIRED_UNFILLED"))
