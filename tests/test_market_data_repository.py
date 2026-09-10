"""Tests for repository/market_data.py's swing-point deduplication —
regression coverage for the "ON CONFLICT DO UPDATE command cannot affect row
a second time" bug (see also tests/test_swings_and_structure.py for the
root-cause fix in engine.market_state.structure.label_structure). Also
covers Phase 5.0 §4's `fetch_bars_range` (additive; `fetch_bars` itself is
unmodified — see the dedicated regression section below)."""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.market_state.opening_range import OpeningRangeEvaluation
from engine.market_state.types import SwingPoint, SwingSignificance, SwingType
from repository.market_data import dedupe_swing_rows, fetch_bars, fetch_bars_range, upsert_opening_range_event, upsert_swing_points


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


# ---- Phase 5.0 §4: fetch_bars_range (additive — fetch_bars is unmodified) ----


class _FakeRangeQuery:
    """Records every chained call so tests can assert BOTH the returned data
    handling AND which columns/filters `fetch_bars_range` actually sent —
    the closest thing to an "indexed query path" check available without a
    live Postgres instance: `bars_symbol_tf_ts_idx` is (symbol, timeframe, ts
    desc), so this asserts symbol/timeframe are filtered via `.eq()` and
    ordering happens on `ts`, exactly the columns that index covers."""

    def __init__(self, pages: list[list[dict]]):
        self._pages = pages
        self.calls: list[tuple[str, tuple, dict]] = []
        self._page_index = 0

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self

    def select(self, *a, **k):
        return self._record("select", *a, **k)

    def eq(self, *a, **k):
        return self._record("eq", *a, **k)

    def gte(self, *a, **k):
        return self._record("gte", *a, **k)

    def lte(self, *a, **k):
        return self._record("lte", *a, **k)

    def order(self, *a, **k):
        return self._record("order", *a, **k)

    def range(self, *a, **k):
        self._record("range", *a, **k)
        return self

    def execute(self):
        page = self._pages[self._page_index] if self._page_index < len(self._pages) else []
        self._page_index += 1
        return type("Res", (), {"data": page})()


class _FakeRangeClient:
    def __init__(self, pages: list[list[dict]]):
        self.query = _FakeRangeQuery(pages)

    def table(self, name):
        assert name == "bars"
        return self.query


def _bar_row(ts, provider="alpaca", price=100.0):
    return {"ts": ts, "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 500, "provider": provider}


_RSTART = datetime(2024, 1, 2, tzinfo=timezone.utc)
_REND = datetime(2024, 1, 3, tzinfo=timezone.utc)


def test_fetch_bars_range_empty_result_is_empty_not_none():
    client = _FakeRangeClient(pages=[[]])
    df = fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    assert df is not None
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "provider"]


def test_fetch_bars_range_returns_chronological_order():
    rows = [_bar_row("2024-01-02T09:40:00Z"), _bar_row("2024-01-02T09:30:00Z"), _bar_row("2024-01-02T09:35:00Z")]
    client = _FakeRangeClient(pages=[rows])
    df = fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    assert list(df.index) == sorted(df.index)  # re-sorted ascending regardless of row arrival order


def test_fetch_bars_range_filters_by_symbol_and_timeframe_and_ts_bounds():
    client = _FakeRangeClient(pages=[[_bar_row("2024-01-02T09:30:00Z")]])
    fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    calls_by_name = [c[0] for c in client.query.calls]
    assert "eq" in calls_by_name and "gte" in calls_by_name and "lte" in calls_by_name and "order" in calls_by_name
    eq_args = [c[1] for c in client.query.calls if c[0] == "eq"]
    assert ("symbol", "SPY") in eq_args
    assert ("timeframe", "5min") in eq_args
    order_args = [c[1] for c in client.query.calls if c[0] == "order"]
    assert order_args and order_args[0][0] == "ts"  # ordered on the indexed ts column


def test_fetch_bars_range_optional_provider_filter():
    client = _FakeRangeClient(pages=[[_bar_row("2024-01-02T09:30:00Z", provider="synthetic")]])
    fetch_bars_range(client, "SPY", "5min", _RSTART, _REND, provider="synthetic")
    eq_args = [c[1] for c in client.query.calls if c[0] == "eq"]
    assert ("provider", "synthetic") in eq_args


def test_fetch_bars_range_no_provider_filter_by_default():
    client = _FakeRangeClient(pages=[[_bar_row("2024-01-02T09:30:00Z")]])
    fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    eq_args = [c[1] for c in client.query.calls if c[0] == "eq"]
    assert not any(args and args[0] == "provider" for args in eq_args)


def test_fetch_bars_range_paginates_past_the_default_page_size():
    # Two full 1000-row pages + one partial page -> 2500 total rows, proving
    # a multi-year range isn't silently truncated at PostgREST's 1000-row cap.
    page1 = [_bar_row(f"2024-01-01T{(i % 24):02d}:00:00Z") for i in range(1000)]
    page2 = [_bar_row(f"2024-01-02T{(i % 24):02d}:00:00Z") for i in range(1000)]
    page3 = [_bar_row(f"2024-01-03T{(i % 24):02d}:00:00Z") for i in range(500)]
    client = _FakeRangeClient(pages=[page1, page2, page3])
    df = fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    assert len(df) == 2500
    range_calls = [c[1] for c in client.query.calls if c[0] == "range"]
    assert range_calls == [(0, 999), (1000, 1999), (2000, 2999)]


def test_fetch_bars_range_stops_pagination_on_a_short_final_page():
    client = _FakeRangeClient(pages=[[_bar_row("2024-01-02T09:30:00Z")]])  # 1 row < page size -> exactly one page
    fetch_bars_range(client, "SPY", "5min", _RSTART, _REND)
    range_calls = [c[1] for c in client.query.calls if c[0] == "range"]
    assert range_calls == [(0, 999)]


# ---- fetch_bars (existing, unmodified) — locks in current live-page behavior ----


class _FakeFetchQuery:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def select(self, *a, **k):
        self.calls.append(("select", a)); return self

    def eq(self, *a, **k):
        self.calls.append(("eq", a)); return self

    def order(self, *a, **k):
        self.calls.append(("order", a, k)); return self

    def limit(self, *a, **k):
        self.calls.append(("limit", a)); return self

    def execute(self):
        return type("Res", (), {"data": self._rows})()


class _FakeFetchClient:
    def __init__(self, rows):
        self.query = _FakeFetchQuery(rows)

    def table(self, name):
        assert name == "bars"
        return self.query


def test_fetch_bars_unchanged_most_recent_n_semantics():
    # No "provider" key here: a real Postgres `.select("ts,open,high,low,
    # close,volume")` (what fetch_bars actually requests) would never return
    # one — unlike fetch_bars_range's tests, this fake must mirror that.
    rows = [{"ts": "2024-01-02T09:30:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500}]
    client = _FakeFetchClient(rows)
    df = fetch_bars(client, "SPY", "5min", limit=50)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]  # no "provider" column — unchanged shape
    limit_calls = [c[1] for c in client.query.calls if c[0] == "limit"]
    assert limit_calls == [(50,)]
    order_calls = [c for c in client.query.calls if c[0] == "order"]
    assert order_calls and order_calls[0][2] == {"desc": True}  # still most-recent-first at the query level


def test_fetch_bars_empty_result_unchanged_shape():
    client = _FakeFetchClient([])
    df = fetch_bars(client, "SPY", "5min")
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
