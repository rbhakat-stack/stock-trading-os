"""Phase 5.2V §1-4/§15 — versioned playbook registry tests."""
from __future__ import annotations

import pytest

from engine.playbooks.definition import PlaybookDefinition, QualityComponentConfig
from engine.playbooks.registry import (
    DuplicatePlaybookVersionError, UnknownPlaybookError, UnknownPlaybookVersionError,
    all_definitions, enabled_definitions, get_current_definition, get_current_version, get_definition,
    implementable_definitions, list_versions, register_definition_for_testing, unregister_definition_for_testing,
)

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
def isolated_test_version():
    """Registers a test-only TREND_PULLBACK_LONG v1.1 (current=False, never
    touching the real current pointer), yielded for the test body, always
    torn down afterward so no test leaks registry state into another."""
    d = _test_definition("TREND_PULLBACK_LONG", "1.1")
    register_definition_for_testing(d)
    try:
        yield d
    finally:
        unregister_definition_for_testing("TREND_PULLBACK_LONG", "1.1")


# ---------------------------------------------------------------------------
# §4 backward compatibility — existing single-arg behavior unchanged
# ---------------------------------------------------------------------------


def test_get_definition_single_arg_returns_current_unchanged():
    d = get_definition("TREND_PULLBACK_LONG")
    assert d.version == "1.0"
    assert d.playbook_id == "TREND_PULLBACK_LONG"


def test_get_current_definition_matches_single_arg_form():
    assert get_current_definition("TREND_PULLBACK_LONG") == get_definition("TREND_PULLBACK_LONG")


def test_all_definitions_unaffected_by_a_coexisting_test_version(isolated_test_version):
    ids_and_versions = {(d.playbook_id, d.version) for d in all_definitions()}
    assert ("TREND_PULLBACK_LONG", "1.0") in ids_and_versions
    assert ("TREND_PULLBACK_LONG", "1.1") not in ids_and_versions  # test-only version invisible here


def test_implementable_and_enabled_definitions_unaffected_by_test_version(isolated_test_version):
    assert all(d.version != "1.1" for d in implementable_definitions())
    assert all(d.version != "1.1" for d in enabled_definitions())


def test_implementable_definitions_count_unchanged():
    assert len(implementable_definitions()) == 10  # matches the pre-existing registry test's own count


# ---------------------------------------------------------------------------
# §1-2 — multiple versions, exact/current lookup
# ---------------------------------------------------------------------------


def test_multiple_versions_same_playbook_id_coexist(isolated_test_version):
    assert list_versions("TREND_PULLBACK_LONG") == ("1.0", "1.1")


def test_exact_version_lookup_is_deterministic(isolated_test_version):
    d10 = get_definition("TREND_PULLBACK_LONG", "1.0")
    d11 = get_definition("TREND_PULLBACK_LONG", "1.1")
    assert d10.version == "1.0"
    assert d11.version == "1.1"
    assert d10 is not d11


def test_current_version_pointer_unaffected_by_additional_registration(isolated_test_version):
    assert get_current_version("TREND_PULLBACK_LONG") == "1.0"
    assert get_definition("TREND_PULLBACK_LONG").version == "1.0"  # no-version-arg form still resolves to 1.0


# ---------------------------------------------------------------------------
# §3 — immutability / fail-closed
# ---------------------------------------------------------------------------


def test_duplicate_version_registration_fails():
    d = _test_definition("TREND_PULLBACK_LONG", "1.0")  # 1.0 already registered
    with pytest.raises(DuplicatePlaybookVersionError):
        register_definition_for_testing(d)


def test_unknown_version_fails_closed():
    with pytest.raises(UnknownPlaybookVersionError):
        get_definition("TREND_PULLBACK_LONG", "9.9")


def test_unknown_playbook_id_fails_closed():
    with pytest.raises(UnknownPlaybookError):
        get_definition("NOT_A_REAL_PLAYBOOK")
    with pytest.raises(UnknownPlaybookError):
        get_definition("NOT_A_REAL_PLAYBOOK", "1.0")
    with pytest.raises(UnknownPlaybookError):
        list_versions("NOT_A_REAL_PLAYBOOK")
    with pytest.raises(UnknownPlaybookError):
        get_current_version("NOT_A_REAL_PLAYBOOK")


def test_unknown_playbook_and_version_errors_are_still_key_errors():
    """Backward compatibility for any existing caller (e.g. Phase 5.1 replay)
    that catches plain KeyError."""
    with pytest.raises(KeyError):
        get_definition("NOT_A_REAL_PLAYBOOK")
    with pytest.raises(KeyError):
        get_definition("TREND_PULLBACK_LONG", "9.9")


def test_cannot_unregister_the_current_version():
    with pytest.raises(ValueError):
        unregister_definition_for_testing("TREND_PULLBACK_LONG", "1.0")


def test_unregistering_a_nonexistent_test_version_is_a_safe_noop():
    unregister_definition_for_testing("TREND_PULLBACK_LONG", "9.9")  # must not raise
