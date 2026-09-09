from datetime import datetime, timedelta

from engine.market_state.support_resistance import EnrichedZone, rank_zones_by_relevance


def _zone(upper, lower, strength=0.5, touch=0, rejection=0, last_touch=None):
    return EnrichedZone(
        zone_type="RESISTANCE", upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=rejection, break_count=0, retest_count=0,
        last_touch_time=last_touch, role_reversal_history=[], source_bar_indices=[],
    )


def test_ranking_never_mutates_or_filters_the_input():
    zones = [_zone(101, 99), _zone(201, 199), _zone(301, 299)]
    original_ids = [id(z) for z in zones]
    ranked = rank_zones_by_relevance(zones, current_price=100.0)

    assert len(ranked) == len(zones)
    assert set(id(z) for z in ranked) == set(original_ids)
    assert [id(z) for z in zones] == original_ids  # input list order untouched


def test_empty_input_returns_empty():
    assert rank_zones_by_relevance([], current_price=100.0) == []


def test_closer_zone_ranks_above_farther_zone_all_else_equal():
    near = _zone(101, 99, strength=0.5, touch=3)
    far = _zone(301, 299, strength=0.5, touch=3)
    ranked = rank_zones_by_relevance([far, near], current_price=100.0)
    assert ranked[0] is near


def test_stronger_zone_ranks_above_weaker_zone_all_else_equal():
    strong = _zone(101, 99, strength=0.9, touch=1)
    weak = _zone(101, 99, strength=0.1, touch=1)
    ranked = rank_zones_by_relevance([weak, strong], current_price=100.0)
    assert ranked[0] is strong


def test_more_touched_zone_ranks_above_less_touched_zone_all_else_equal():
    busy = _zone(101, 99, strength=0.5, touch=10, rejection=5)
    quiet = _zone(101, 99, strength=0.5, touch=1, rejection=0)
    ranked = rank_zones_by_relevance([quiet, busy], current_price=100.0)
    assert ranked[0] is busy


def test_more_recently_touched_zone_ranks_above_stale_zone_all_else_equal():
    now = datetime(2024, 1, 15, 12, 0)
    recent = _zone(101, 99, strength=0.5, touch=2, last_touch=now)
    stale = _zone(101, 99, strength=0.5, touch=2, last_touch=now - timedelta(days=5))
    ranked = rank_zones_by_relevance([stale, recent], current_price=100.0)
    assert ranked[0] is recent


def test_zones_never_touched_do_not_crash_ranking():
    never_touched = _zone(101, 99, strength=0.5, touch=0, last_touch=None)
    touched = _zone(101, 99, strength=0.5, touch=2, last_touch=datetime(2024, 1, 15))
    ranked = rank_zones_by_relevance([never_touched, touched], current_price=100.0)
    assert len(ranked) == 2
