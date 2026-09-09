"""Tests for repository/market_data.py's swing-point deduplication —
regression coverage for the "ON CONFLICT DO UPDATE command cannot affect row
a second time" bug (see also tests/test_swings_and_structure.py for the
root-cause fix in engine.market_state.structure.label_structure)."""
import pathlib
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.market_state.opening_range import OpeningRangeEvaluation
from engine.market_state.types import SwingPoint, SwingSignificance, SwingType
from repository.market_data import dedupe_swing_rows, upsert_opening_range_event, upsert_swing_points


def _pt(bar_index, swing_type, significance=SwingSignificance.MAJOR, score=0.9, price=100.0):
    ts = datetime(2024, 1, 2, 9, 30) + timedelta(minutes=5 * bar_index)
    return SwingPoint(ts=ts, price=price, swing_type=swing_type, significance=significance, score=score, bar_index=bar_index)


# ---- A/D: exact duplicates -> only one survives ----


def test_exact_duplicate_rows_collapse_to_one():
    p1 = _pt(4, SwingType.HIGH)
    p2 = _pt(4, SwingType.HIGH)
    result = dedupe_swing_rows([p1, p2])
    assert len(result) == 1
    assert result[0].bar_index == 4


# ---- B: MAJOR beats MINOR ----


def test_major_beats_minor_on_conflict():
    minor = _pt(4, SwingType.HIGH, significance=SwingSignificance.MINOR, score=0.9)
    major = _pt(4, SwingType.HIGH, significance=SwingSignificance.MAJOR, score=0.1)
    result = dedupe_swing_rows([minor, major])
    assert len(result) == 1
    assert result[0].significance == SwingSignificance.MAJOR

    # Order shouldn't matter.
    result_reversed = dedupe_swing_rows([major, minor])
    assert result_reversed[0].significance == SwingSignificance.MAJOR


# ---- C: same significance, higher score wins ----


def test_higher_score_wins_when_significance_ties():
    low_score = _pt(4, SwingType.HIGH, significance=SwingSignificance.MAJOR, score=0.4)
    high_score = _pt(4, SwingType.HIGH, significance=SwingSignificance.MAJOR, score=0.8)
    result = dedupe_swing_rows([low_score, high_score])
    assert len(result) == 1
    assert result[0].score == 0.8


def test_full_tie_keeps_first_seen_for_stable_ordering():
    first = _pt(4, SwingType.HIGH, price=100.0)
    second = _pt(4, SwingType.HIGH, price=200.0)  # identical significance/score, different price
    result = dedupe_swing_rows([first, second])
    assert len(result) == 1
    assert result[0].price == 100.0


# ---- E: different swing_type at the same timestamp must remain separate ----


def test_high_and_low_at_the_same_bar_remain_separate():
    high = _pt(4, SwingType.HIGH)
    low = _pt(4, SwingType.LOW)
    result = dedupe_swing_rows([high, low])
    assert len(result) == 2
    assert {p.swing_type for p in result} == {SwingType.HIGH, SwingType.LOW}


def test_dedupe_preserves_non_conflicting_points_and_stable_order():
    p1 = _pt(2, SwingType.HIGH)
    p2 = _pt(4, SwingType.LOW)
    p3 = _pt(7, SwingType.HIGH)
    result = dedupe_swing_rows([p1, p2, p3])
    assert [p.bar_index for p in result] == [2, 4, 7]


# ---- Repository-level: the actual batch sent to Supabase has unique keys ----


class _FakeTable:
    def __init__(self, captured):
        self._captured = captured

    def upsert(self, rows, on_conflict=None):
        self._captured["rows"] = rows
        self._captured["on_conflict"] = on_conflict
        return self

    def execute(self):
        return None


class _FakeClient:
    def __init__(self):
        self.captured = {}

    def table(self, name):
        assert name == "swing_points"
        return _FakeTable(self.captured)


def test_upsert_swing_points_sends_a_batch_with_unique_conflict_keys():
    # The exact production scenario: a bar that is both a MAJOR high and low.
    duplicate_prone = [
        _pt(4, SwingType.HIGH, price=12.0),
        _pt(4, SwingType.LOW, price=8.0),
        _pt(4, SwingType.HIGH, price=12.0),  # a hypothetical accidental re-emit of the same point
    ]
    client = _FakeClient()
    upsert_swing_points(client, "SPY", "5min", duplicate_prone, algorithm_version="swings-v1")

    rows = client.captured["rows"]
    keys = [(r["symbol"], r["timeframe"], r["ts"], r["swing_type"]) for r in rows]
    assert len(keys) == len(set(keys)), "batch sent to Supabase must not contain duplicate conflict keys"
    assert len(rows) == 2  # the accidental re-emit collapsed away


def test_upsert_swing_points_calls_are_independent_across_symbols_and_timeframes():
    # F/G: different timeframe or symbol must never interact with each other's
    # dedup — each call operates on its own batch only, no shared/cached state.
    client = _FakeClient()
    upsert_swing_points(client, "SPY", "5min", [_pt(4, SwingType.HIGH)], algorithm_version="swings-v1")
    first_call_rows = client.captured["rows"]
    assert first_call_rows[0]["symbol"] == "SPY" and first_call_rows[0]["timeframe"] == "5min"

    upsert_swing_points(client, "QQQ", "15min", [_pt(4, SwingType.HIGH)], algorithm_version="swings-v1")
    second_call_rows = client.captured["rows"]
    assert second_call_rows[0]["symbol"] == "QQQ" and second_call_rows[0]["timeframe"] == "15min"
    assert len(second_call_rows) == 1  # unaffected by the earlier SPY/5min call


# ---- opening_range_events: opening_volume must be an int (bigint column), not a float ----


class _FakeOpeningRangeTable:
    def __init__(self, captured):
        self._captured = captured

    def upsert(self, row, on_conflict=None):
        self._captured["row"] = row
        self._captured["on_conflict"] = on_conflict
        return self

    def execute(self):
        return None


class _FakeOpeningRangeClient:
    def __init__(self):
        self.captured = {}

    def table(self, name):
        assert name == "opening_range_events"
        return _FakeOpeningRangeTable(self.captured)


def test_upsert_opening_range_event_sends_an_int_opening_volume():
    # Regression test for a real production failure: Postgres rejected
    # opening_volume when it arrived as a float ("1832485.0" into a `bigint`
    # column) with APIError 22P02 "invalid input syntax for type bigint".
    opening_range = OpeningRangeEvaluation(
        session_date="2024-01-15", window_minutes=30, orh=101.0, orl=99.0, midpoint=100.0,
        width=2.0, width_atr=1.0, opening_volume=1_832_485, status="INSIDE_OPENING_RANGE",
        breakout=None, evidence={},
    )
    client = _FakeOpeningRangeClient()
    upsert_opening_range_event(
        client, "SPY", "5min", session_date=datetime(2024, 1, 15).date(),
        opening_range=opening_range, algorithm_version="opening-range-v1",
    )

    sent = client.captured["row"]["opening_volume"]
    assert isinstance(sent, int)
    assert not isinstance(sent, bool)
    assert sent == 1_832_485
