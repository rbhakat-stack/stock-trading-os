"""Phase 5.0 §8/§9/§10 — forward-looking contract SHAPES only: no
implementation logic exists yet, these tests only verify the contracts
themselves enforce the rules the task requires (explicit version pinning,
optional symbol for pooled evidence)."""
from datetime import datetime, timezone

import pytest

from engine.backtest.contracts import (
    BacktestRunSpec, RunDataMode, StatisticalValidationResult, ValidationStatus,
)

_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
_END = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _spec(**overrides):
    defaults = dict(
        playbook_id="TREND_PULLBACK_LONG", playbook_version="1.0", symbol_universe=("SPY",),
        timeframe="5min", date_range_start=_START, date_range_end=_END, data_mode=RunDataMode.PRODUCTION,
    )
    defaults.update(overrides)
    return BacktestRunSpec(**defaults)


# ---- §8: explicit playbook version pinning, never inferred from date ----


def test_backtest_run_spec_requires_explicit_playbook_id_and_version():
    with pytest.raises(ValueError):
        _spec(playbook_id="")
    with pytest.raises(ValueError):
        _spec(playbook_version="")


def test_backtest_run_spec_has_no_date_derived_version_field():
    # Structural proof, not just a runtime check: the dataclass's own field
    # set contains no "as_of_date"/"effective_date"-style field that could
    # let a version be inferred from the backtested date range.
    fields = {f for f in BacktestRunSpec.__dataclass_fields__}
    assert "playbook_version" in fields
    assert not any("as_of" in f or "effective_date" in f for f in fields)


def test_two_versions_of_the_same_playbook_are_independent_specs():
    v1 = _spec(playbook_version="1.0")
    v11 = _spec(playbook_version="1.1")
    assert v1 != v11
    assert v1.playbook_version != v11.playbook_version
    # Same date range, same symbol universe, deliberately different pinned version.
    assert v1.date_range_start == v11.date_range_start
    assert v1.symbol_universe == v11.symbol_universe


def test_backtest_run_spec_requires_at_least_one_symbol():
    with pytest.raises(ValueError):
        _spec(symbol_universe=())


def test_backtest_run_spec_requires_end_after_start():
    with pytest.raises(ValueError):
        _spec(date_range_start=_END, date_range_end=_START)


def test_backtest_run_spec_supports_multi_symbol_universe():
    spec = _spec(symbol_universe=("SPY", "AAPL", "QQQ"))
    assert len(spec.symbol_universe) == 3


# ---- §10: pooled / cross-symbol evidence must be representable ----


def _evidence(**overrides):
    defaults = dict(
        playbook_id="TREND_PULLBACK_LONG", playbook_version="1.0", direction="LONG", timeframe="5min",
        market_regime="UPTREND_CONFIRMED", volatility_regime="NORMAL", sample_size=100,
        expectancy_R=0.5, expectancy_R_ci_low=0.2, expectancy_R_ci_high=0.8, win_rate=0.48,
        average_win_R=2.1, average_loss_R=-0.9, validation_status=ValidationStatus.POSITIVE_EDGE.value,
        validation_policy_version="v1", data_period_start=_START, data_period_end=_END, run_id="run-1",
    )
    defaults.update(overrides)
    return StatisticalValidationResult(**defaults)


def test_symbol_is_optional_and_defaults_to_pooled():
    pooled = _evidence()
    assert pooled.symbol is None  # None = pooled across the backtested universe


def test_symbol_specific_evidence_is_also_representable():
    per_symbol = _evidence(symbol="AAPL")
    assert per_symbol.symbol == "AAPL"


def test_pooled_and_symbol_specific_evidence_share_the_same_shape():
    pooled = _evidence()
    per_symbol = _evidence(symbol="AAPL")
    assert type(pooled) is type(per_symbol)
    assert {f for f in pooled.__dataclass_fields__} == {f for f in per_symbol.__dataclass_fields__}


def test_market_and_volatility_regime_are_also_optional_for_further_pooling():
    fully_pooled = _evidence(market_regime=None, volatility_regime=None)
    assert fully_pooled.market_regime is None
    assert fully_pooled.volatility_regime is None


def test_validation_status_taxonomy_is_the_approved_minimum_four():
    assert {s.value for s in ValidationStatus} == {
        "NOT_TESTED", "INSUFFICIENT_SAMPLE", "NO_EDGE", "POSITIVE_EDGE",
    }


def test_positive_edge_is_not_a_recommendation_by_construction():
    # Structural proof: StatisticalValidationResult has no "recommended" /
    # "should_trade" boolean field — POSITIVE_EDGE is evidence, not a
    # recommendation (§18-G, explicit requirement).
    fields = {f for f in StatisticalValidationResult.__dataclass_fields__}
    assert not any("recommend" in f.lower() for f in fields)


# ---- §11/§15: unresolved design notes and approved principles must be
# recorded somewhere durable, not just in this conversation ----


def test_entry_lifecycle_design_note_is_recorded_in_package_docstring():
    import engine.backtest

    doc = engine.backtest.__doc__
    assert "ENTRY_ACTIVE" in doc
    assert "TRIGGERED -> ENTRY_ACTIVE -> FILLED" in doc


def test_approved_backtest_principles_are_recorded_in_package_docstring():
    import engine.backtest

    doc = engine.backtest.__doc__
    for marker in ("STOP FIRST", "same_bar_collision", "GROSS and NET", "NOT_TESTED", "20 trading days"):
        assert marker in doc
