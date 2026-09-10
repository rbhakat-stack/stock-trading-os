"""Phase 5.2V §6 — Phase 5.1 replay version-pin resolution, now backed by
the versioned registry. Proves the previous limitation ("a pin only works
when requested version == current registry version") is gone, using an
isolated test-only v1.1 that coexists with the real v1.0 — never a real
production v1.1.
"""
from __future__ import annotations

import pytest

from engine.backtest.contracts import PlaybookPin
from engine.backtest.replay import PlaybookVersionMismatchError, resolve_pinned_definition
from engine.playbooks.definition import PlaybookDefinition, QualityComponentConfig
from engine.playbooks.registry import register_definition_for_testing, unregister_definition_for_testing

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


def test_pin_v1_0_resolves_even_when_a_test_only_v1_1_also_exists(isolated_v11):
    resolved = resolve_pinned_definition(PlaybookPin("TREND_PULLBACK_LONG", "1.0"))
    assert resolved.version == "1.0"


def test_current_v1_1_does_not_replace_a_pinned_historical_v1_0(isolated_v11):
    # even though 1.1 now exists, requesting 1.0 must never silently resolve to 1.1.
    resolved = resolve_pinned_definition(PlaybookPin("TREND_PULLBACK_LONG", "1.0"))
    assert resolved.version == "1.0"
    assert resolved is not isolated_v11


def test_pin_the_test_only_v1_1_resolves_to_v1_1_not_v1_0(isolated_v11):
    resolved = resolve_pinned_definition(PlaybookPin("TREND_PULLBACK_LONG", "1.1"))
    assert resolved.version == "1.1"
    assert resolved is isolated_v11


def test_pin_unknown_version_fails_closed(isolated_v11):
    with pytest.raises(PlaybookVersionMismatchError):
        resolve_pinned_definition(PlaybookPin("TREND_PULLBACK_LONG", "2.0"))


def test_pin_unknown_playbook_fails_closed():
    with pytest.raises(PlaybookVersionMismatchError):
        resolve_pinned_definition(PlaybookPin("NOT_A_REAL_PLAYBOOK", "1.0"))


def test_not_implementable_version_pin_remains_blocked():
    with pytest.raises(PlaybookVersionMismatchError):
        resolve_pinned_definition(PlaybookPin("VWAP_RECLAIM_LONG", "0.0"))


def test_current_version_pin_still_works_without_any_test_version_present():
    resolved = resolve_pinned_definition(PlaybookPin("TREND_PULLBACK_LONG", "1.0"))
    assert resolved.version == "1.0"
