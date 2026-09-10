"""Domain-model completeness tests for the PlaybookDefinition registry
(§2-5 of the Phase 4 report)."""
from engine.playbooks.registry import all_definitions, enabled_definitions, get_definition, implementable_definitions
from engine.playbooks.taxonomy import PlaybookFamily, PlaybookId


def test_all_twelve_playbooks_registered():
    ids = {d.playbook_id for d in all_definitions()}
    assert ids == {p.value for p in PlaybookId}


def test_ten_implementable_two_deferred():
    assert len(implementable_definitions()) == 10
    deferred = {d.playbook_id for d in all_definitions() if not d.implementable}
    assert deferred == {"VWAP_RECLAIM_LONG", "VWAP_REJECTION_SHORT"}


def test_deferred_playbooks_documented_not_fabricated():
    for pid in ("VWAP_RECLAIM_LONG", "VWAP_REJECTION_SHORT"):
        d = get_definition(pid)
        assert d.enabled is False
        assert d.not_implementable_reason is not None
        assert "VWAP" in d.not_implementable_reason
        assert d.quality_components == ()  # no fabricated scoring config


def test_every_implementable_playbook_has_a_version():
    for d in implementable_definitions():
        assert d.version and d.version != "0.0"


def test_every_playbook_belongs_to_exactly_one_family():
    for d in all_definitions():
        assert d.family in {f.value for f in PlaybookFamily}


def test_quality_components_sum_to_100_for_every_implementable_playbook():
    for d in implementable_definitions():
        assert sum(c.max_points for c in d.quality_components) == 100


def test_every_implementable_playbook_has_plain_english_fields():
    for d in implementable_definitions():
        assert d.short_description
        assert d.plain_english_description
        assert d.when_it_works
        assert d.what_can_go_wrong
        assert d.prerequisites
        assert d.trigger_rules
        assert d.disqualifiers
        assert d.entry_rules
        assert d.stop_rules
        assert d.target_rules


def test_manual_review_notes_present_for_every_implementable_playbook():
    for d in implementable_definitions():
        assert "MANUAL REVIEW REQUIRED" in d.manual_review_notes


def test_statistical_validation_status_not_fabricated():
    for d in implementable_definitions():
        assert d.statistical_validation_status == "NOT_YET_AVAILABLE"


def test_enabled_definitions_excludes_deferred():
    ids = {d.playbook_id for d in enabled_definitions()}
    assert "VWAP_RECLAIM_LONG" not in ids
    assert "VWAP_REJECTION_SHORT" not in ids
    assert len(ids) == 10


def test_paired_long_short_playbooks_share_a_family():
    pairs = [
        ("BREAKOUT_RETEST_LONG", "BREAKOUT_RETEST_SHORT"),
        ("TREND_PULLBACK_LONG", "TREND_PULLBACK_SHORT"),
        ("FAILED_BREAKOUT_REVERSAL_LONG", "FAILED_BREAKOUT_REVERSAL_SHORT"),
        ("OPENING_RANGE_BREAKOUT_LONG", "OPENING_RANGE_BREAKOUT_SHORT"),
        ("RANGE_MEAN_REVERSION_LONG", "RANGE_MEAN_REVERSION_SHORT"),
    ]
    for long_id, short_id in pairs:
        long_def, short_def = get_definition(long_id), get_definition(short_id)
        assert long_def.family == short_def.family
        assert long_def.direction == "LONG"
        assert short_def.direction == "SHORT"
        assert long_def.supported_timeframes == short_def.supported_timeframes
