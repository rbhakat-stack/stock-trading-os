from engine.market_state.support_resistance import EnrichedZone
from engine.trade.targets import compute_targets


def _zone(zone_type, upper, lower, strength=0.5, touch=1):
    return EnrichedZone(
        zone_type=zone_type, upper_boundary=upper, lower_boundary=lower, strength_score=strength,
        touch_count=touch, rejection_count=0, break_count=0, retest_count=0,
        last_touch_time=None, role_reversal_history=[], source_bar_indices=[],
    )


def test_long_targets_use_resistance_zones_above_entry():
    resistance_zones = [_zone("RESISTANCE", 106, 105), _zone("RESISTANCE", 112, 110)]
    targets = compute_targets("LONG", entry=100.0, stop=98.0, resistance_zones=resistance_zones)
    assert len(targets) == 2
    assert targets[0].price == 105.0  # nearest zone's lower edge
    assert targets[1].price == 110.0
    assert targets[0].r_multiple == round((105.0 - 100.0) / 2.0, 3)


def test_short_targets_use_support_zones_below_entry():
    support_zones = [_zone("SUPPORT", 95, 94), _zone("SUPPORT", 90, 88)]
    targets = compute_targets("SHORT", entry=100.0, stop=102.0, support_zones=support_zones)
    assert len(targets) == 2
    assert targets[0].price == 95.0  # nearest zone's upper edge
    assert targets[1].price == 90.0


def test_zones_behind_entry_are_never_used_as_targets():
    # A "resistance" zone below current price is behind us for a long — must be ignored.
    resistance_zones = [_zone("RESISTANCE", 99, 97)]
    targets = compute_targets("LONG", entry=100.0, stop=98.0, resistance_zones=resistance_zones)
    assert targets == []


def test_no_fabricated_target_when_no_zones_exist():
    targets = compute_targets("LONG", entry=100.0, stop=98.0, resistance_zones=[], support_zones=[])
    assert targets == []


def test_only_one_target_when_only_one_zone_qualifies():
    resistance_zones = [_zone("RESISTANCE", 106, 105)]
    targets = compute_targets("LONG", entry=100.0, stop=98.0, resistance_zones=resistance_zones)
    assert len(targets) == 1


def test_at_most_two_targets_even_with_many_zones():
    resistance_zones = [_zone("RESISTANCE", 106 + i, 105 + i) for i in range(10)]
    targets = compute_targets("LONG", entry=100.0, stop=98.0, resistance_zones=resistance_zones)
    assert len(targets) == 2


def test_invalid_direction_raises():
    import pytest

    with pytest.raises(ValueError):
        compute_targets("SIDEWAYS", entry=100.0, stop=98.0)


def test_zero_risk_per_unit_returns_no_targets():
    resistance_zones = [_zone("RESISTANCE", 106, 105)]
    targets = compute_targets("LONG", entry=100.0, stop=100.0, resistance_zones=resistance_zones)
    assert targets == []
