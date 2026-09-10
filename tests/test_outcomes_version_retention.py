"""Phase 5.2V §7 — HistoricalTradeOutcome must retain the PINNED historical
playbook_version, never inferring it from today's current registry pointer.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.backtest.outcomes import default_execution_policy, simulate_trade_outcome
from engine.playbooks.definition import PlaybookDefinition, QualityComponentConfig
from engine.playbooks.registry import (
    get_current_version, register_definition_for_testing, unregister_definition_for_testing,
)
from engine.trade.candidate import EntryMethod, TradeCandidate
from engine.trade.invalidation import InvalidationResult
from engine.trade.targets import TargetResult
from engine.playbooks.evaluation import PlaybookEvaluation

_QUALITY = (QualityComponentConfig("structure", "Structure", 100),)


def _test_definition(playbook_id: str, version: str) -> PlaybookDefinition:
    return PlaybookDefinition(
        playbook_id=playbook_id, version=version, name=f"{playbook_id} test def", family="TREND_CONTINUATION",
        direction="LONG", short_description="test", plain_english_description="test", when_it_works="test",
        what_can_go_wrong="test", supported_timeframes=("5min",), supported_regimes=("UPTREND_CONFIRMED",),
        prerequisites=(), trigger_rules=(), confirmation_rules=(), disqualifiers=(), entry_rules=(),
        stop_rules=(), target_rules=(), management_rules=(), quality_components=_QUALITY,
        required_evidence=(), optional_evidence=(), contra_evidence=(), entry_type="RETEST_HOLD",
    )


@pytest.fixture
def isolated_v11():
    d = _test_definition("TREND_PULLBACK_LONG", "1.1")
    register_definition_for_testing(d)
    try:
        yield d
    finally:
        unregister_definition_for_testing("TREND_PULLBACK_LONG", "1.1")


def _bars():
    idx = pd.date_range("2024-01-02 09:30", periods=3, freq="5min", tz="America/New_York")
    df = pd.DataFrame(
        {"open": [100, 100, 100.5], "high": [100, 101, 106], "low": [100, 99.5, 100],
         "close": [100, 100.5, 105], "volume": [1000, 1000, 1000]},
        index=idx,
    )
    return df.tz_convert("UTC")


def _evaluation(playbook_version: str) -> PlaybookEvaluation:
    candidate = TradeCandidate(
        setup_type="TREND_PULLBACK_LONG", direction="LONG", status="TRIGGERED", structural_level=100.0,
        entry_zone_low=None, entry_zone_high=None, entry_method=EntryMethod.MARKET_ON_CONFIRMATION.value,
    )
    return PlaybookEvaluation(
        playbook_id="TREND_PULLBACK_LONG", playbook_version=playbook_version, name="Trend Pullback Long",
        direction="LONG", family="TREND_CONTINUATION", eligibility_status="ELIGIBLE", setup_status="TRIGGERED",
        candidate=candidate, invalidation=InvalidationResult(stop_price=98.0, structural_level=100.0, atr_buffer=0.0, reason="t"),
        targets=(TargetResult(price=104.0, reason="t", distance=4.0, reward_per_unit=4.0, r_multiple=2.0),),
        entry_price=100.0, quality_score=70, quality_band="HIGH",
    )


def test_outcome_retains_pinned_v1_0_regardless_of_current_pointer(isolated_v11):
    assert get_current_version("TREND_PULLBACK_LONG") == "1.0"  # current is still 1.0, unaffected by the test v1.1
    bars = _bars()
    evaluation = _evaluation("1.0")  # this is what a v1.0-pinned historical replay would have captured
    outcome = simulate_trade_outcome(
        evaluation, "TEST", "5min", bars.index[0], bars, default_execution_policy(), occurrence_id="v-retain-1",
    )
    assert outcome.playbook_version == "1.0"


def test_outcome_retains_pinned_v1_1_even_though_current_stays_v1_0(isolated_v11):
    assert get_current_version("TREND_PULLBACK_LONG") == "1.0"
    bars = _bars()
    evaluation = _evaluation("1.1")  # simulates a (hypothetical) v1.1-pinned historical replay's captured evaluation
    outcome = simulate_trade_outcome(
        evaluation, "TEST", "5min", bars.index[0], bars, default_execution_policy(), occurrence_id="v-retain-2",
    )
    assert outcome.playbook_version == "1.1"
    assert get_current_version("TREND_PULLBACK_LONG") == "1.0"  # never mutated as a side effect
