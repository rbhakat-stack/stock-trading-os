from conftest import make_flat_df

from engine.market_state.structure import detect_swing_legs, label_structure
from engine.market_state.swings import detect_swings
from engine.market_state.types import StructureLabel, SwingSignificance, SwingType

# Hand-constructed zigzag: SH1(11.2)@2 -> SL1(10.1)@4 -> new high 11.8@7 -> new low 10.8@10 -> new high 12.1@13
BULLISH_CLOSES = [
    10.0, 10.6, 11.2, 10.7, 10.1, 10.6, 11.0, 11.8, 11.3, 10.9,
    10.8, 11.1, 11.6, 12.1, 11.7, 11.3, 11.6, 11.4, 11.5,
]


def test_detect_swings_finds_expected_candidates():
    df = make_flat_df(BULLISH_CLOSES)
    points = detect_swings(df, left_bars=2, right_bars=2)
    by_index = {p.bar_index: p for p in points}

    # The five primary swing points of the hand-constructed zigzag must all be
    # found and scored MAJOR. (A smaller, legitimate local extremum may also be
    # detected later in the series — e.g. the minor pullback at idx15 — that's
    # correct fractal-detector behavior, not asserted against here.)
    expected_majors = {2: SwingType.HIGH, 4: SwingType.LOW, 7: SwingType.HIGH, 10: SwingType.LOW, 13: SwingType.HIGH}
    assert expected_majors.keys() <= by_index.keys()
    for idx, expected_type in expected_majors.items():
        assert by_index[idx].swing_type == expected_type
        assert by_index[idx].significance == SwingSignificance.MAJOR


def test_label_structure_produces_hh_hl_sequence():
    df = make_flat_df(BULLISH_CLOSES)
    points = detect_swings(df, left_bars=2, right_bars=2)
    labeled = label_structure(points)
    by_index = {p.bar_index: p for p in labeled}

    assert by_index[2].label == StructureLabel.NONE  # first high, nothing to compare against
    assert by_index[4].label == StructureLabel.NONE  # first low
    assert by_index[7].label == StructureLabel.HH    # 11.8 > 11.2
    assert by_index[10].label == StructureLabel.HL   # 10.8 > 10.1
    assert by_index[13].label == StructureLabel.HH   # 12.1 > 11.8


def test_swing_legs_are_movements_between_points():
    df = make_flat_df(BULLISH_CLOSES)
    points = detect_swings(df, left_bars=2, right_bars=2)
    legs = detect_swing_legs(points)

    assert len(legs) == len(points) - 1
    assert legs[0].direction == "DOWN"  # SH1(11.2) -> SL1(10.1)
    assert legs[1].direction == "UP"    # SL1(10.1) -> 11.8
