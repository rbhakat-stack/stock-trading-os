import functools
from datetime import datetime, timedelta, timezone

from conftest import make_flat_df

from engine.data_provider.synthetic_provider import SyntheticProvider
from engine.market_state.market_intelligence import build_snapshot
from engine.market_state.time_of_day import classify_time_of_day
from engine.market_state.types import MarketState

BULLISH_CLOSES = [
    10.0, 10.6, 11.2, 10.7, 10.1, 10.6, 11.0, 11.8, 11.3, 10.9,
    10.8, 11.1, 11.6, 12.1, 11.7, 11.3, 11.6, 11.4, 11.5,
]

# Extends BULLISH_CLOSES into a genuine full reversal: BEARISH_CHOCH ->
# DOWNTREND_CONFIRMED_FROM_CHOCH — verified deterministically in
# tests/test_bos_choch.py. The market's LATEST event is the downtrend
# confirmation, chronologically long after the one-and-only BULLISH_BOS this
# series ever emits (BOS only fires transitioning out of RANGE).
REVERSAL_EXTENSION = [10.5, 10.0, 9.6, 10.0, 10.4, 10.8, 10.5, 10.1, 9.3, 9.7, 10.0]


def test_snapshot_composes_all_engines_for_a_healthy_uptrend():
    df = make_flat_df(BULLISH_CLOSES)
    snapshot = build_snapshot("SPY", "5min", df, data_source="synthetic", timeframe_minutes=5)

    assert snapshot.data_quality_ok is True
    assert snapshot.market_state == "UPTREND_CONFIRMED"
    assert snapshot.trend_quality is not None
    assert snapshot.latest_bos is not None
    assert snapshot.latest_bos.direction == "BULLISH"
    assert snapshot.latest_choch is None  # no CHOCH occurred in this series
    assert snapshot.atr_value is not None
    assert snapshot.volume_level in (
        "VERY_LOW", "LOW", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "INSUFFICIENT_DATA",
    )
    assert isinstance(snapshot.support_zones, list)
    assert isinstance(snapshot.resistance_zones, list)
    assert snapshot.time_of_day is not None
    # No trading signal ever appears anywhere in the snapshot's serializable state.
    dump = str(snapshot)
    for forbidden in ("BUY", "SELL", "ENTER NOW", "ENTRY", "STOP LOSS", "TARGET PRICE", "POSITION SIZE"):
        assert forbidden not in dump


def test_snapshot_fails_closed_on_bad_data():
    df = make_flat_df(BULLISH_CLOSES)
    df.iloc[3, df.columns.get_loc("close")] = float("nan")

    snapshot = build_snapshot("SPY", "5min", df, data_source="synthetic", timeframe_minutes=5)

    assert snapshot.data_quality_ok is False
    assert snapshot.market_state is None
    assert any("DATA QUALITY FAILURE" in w for w in snapshot.warnings)


def test_snapshot_includes_multi_timeframe_alignment_when_higher_timeframe_data_given():
    df = make_flat_df(BULLISH_CLOSES)
    higher_df = make_flat_df(BULLISH_CLOSES, freq="15min")

    snapshot = build_snapshot(
        "SPY", "5min", df, data_source="synthetic", timeframe_minutes=5,
        higher_timeframe_data={"15min": higher_df},
    )

    assert snapshot.multi_timeframe_alignment is not None
    assert "15min" in snapshot.multi_timeframe_alignment.timeframe_states


def test_snapshot_never_computed_without_higher_timeframe_data():
    df = make_flat_df(BULLISH_CLOSES)
    snapshot = build_snapshot("SPY", "5min", df, data_source="synthetic", timeframe_minutes=5)
    assert snapshot.multi_timeframe_alignment is None


# ---- B: "latest structural event" must be the true chronological latest, not
# whichever BOS happens to be most recent (a real production bug — see
# MarketIntelligenceSnapshot.latest_structural_event's docstring) ----


def test_latest_structural_event_is_the_true_chronological_latest_not_a_stale_bos():
    # Built directly from the lower-level pipeline (left_bars=2, right_bars=2 —
    # verified deterministically in tests/test_bos_choch.py to reach
    # DOWNTREND_CONFIRMED_FROM_CHOCH) rather than through build_snapshot(),
    # which uses different swing-detection defaults (3,3) and would need its
    # own separately-tuned fixture — this test targets the
    # latest_structural_event PROPERTY specifically, decoupled from that.
    from engine.market_state.bos_choch import evaluate_bos, evaluate_latest_choch
    from engine.market_state.market_intelligence import MarketIntelligenceSnapshot
    from engine.market_state.structure import label_structure
    from engine.market_state.swings import detect_swings
    from engine.market_state.trend import classify_trend
    from engine.features.volatility import atr

    df = make_flat_df(BULLISH_CLOSES + REVERSAL_EXTENSION)
    points = label_structure(detect_swings(df, left_bars=2, right_bars=2))
    events = classify_trend(df, points)
    atr_series = atr(df)

    assert events[-1].state == MarketState.DOWNTREND_CONFIRMED
    assert events[-1].evidence.get("event") == "DOWNTREND_CONFIRMED_FROM_CHOCH"

    bos_event = next(e for e in events if e.evidence.get("event") == "BULLISH_BOS")
    latest_bos = evaluate_bos(df, bos_event, atr_series)
    latest_choch = evaluate_latest_choch(events)
    assert latest_choch.outcome == "REVERSAL_CONFIRMED"
    assert latest_bos.direction == "BULLISH"
    assert latest_bos.ts < events[-1].ts  # the BOS really is chronologically stale here

    snapshot = MarketIntelligenceSnapshot(
        symbol="SPY", timeframe="5min", as_of=df.index[-1], data_source="synthetic",
        market_state=events[-1].state.value, trend_quality=None,
        latest_bos=latest_bos, latest_choch=latest_choch,
        support_zones=[], resistance_zones=[], consolidation=None, breakout=None,
        volume_level="NORMAL", rvol=None, atr_value=None, volatility=None,
        opening_range=None, multi_timeframe_alignment=None, time_of_day=None,
        events=events,
    )

    # The bug: latest_bos is legitimately a much earlier, now-stale BULLISH BOS —
    # but latest_structural_event must be the genuinely most recent event.
    lse = snapshot.latest_structural_event
    assert lse is snapshot.events[-1]
    assert lse.evidence.get("event") == "DOWNTREND_CONFIRMED_FROM_CHOCH"
    assert lse.ts > snapshot.latest_bos.ts  # the true latest event postdates the stale BOS
    assert lse.state == MarketState.DOWNTREND_CONFIRMED


def test_latest_structural_event_is_none_with_no_events():
    # A flat/insufficient series with no swing points produces no structural events.
    df = make_flat_df([10.0] * 5)
    snapshot = build_snapshot("SPY", "5min", df, data_source="synthetic", timeframe_minutes=5)
    assert snapshot.latest_structural_event is None


# ---- C: BOS/CHOCH contextual invariants — a BOS can never contradict the
# prevailing structure without an intervening state transition, and a
# confirmed trend can never flip directly to the opposite confirmed trend
# without passing through a WARNING (CHOCH) state first ----


@functools.lru_cache(maxsize=1)
def _fixed_synthetic_events(symbol="SPY", timeframe="5min", days=4):
    provider = SyntheticProvider()
    # Fixed, non-"now"-relative window so this is fully deterministic. Cached
    # (pure/deterministic inputs) since both invariant tests below use the
    # same fixture and building a snapshot (S/R zone enrichment especially)
    # isn't free.
    end = datetime(2025, 6, 2, tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    df = provider.get_ohlcv(symbol, timeframe, start, end)
    snapshot = build_snapshot(symbol, timeframe, df, data_source="synthetic", timeframe_minutes=5)
    assert snapshot.data_quality_ok
    return tuple(snapshot.events)


def test_bos_events_only_ever_occur_transitioning_out_of_range():
    events = _fixed_synthetic_events()
    bos_count = 0
    for i, e in enumerate(events):
        if e.evidence.get("event") in ("BULLISH_BOS", "BEARISH_BOS"):
            bos_count += 1
            prior_state = events[i - 1].state if i > 0 else MarketState.RANGE
            assert prior_state == MarketState.RANGE, (
                f"BOS at {e.ts} fired from state {prior_state}, not RANGE — a BOS must never "
                "contradict an already-established trend without an intervening CHOCH."
            )
    assert bos_count > 0, "test fixture should contain at least one BOS to be a meaningful check"


def test_confirmed_trend_never_flips_directly_to_the_opposite_confirmed_trend():
    events = _fixed_synthetic_events()
    for i in range(1, len(events)):
        prev_state, cur_state = events[i - 1].state, events[i].state
        if prev_state == MarketState.UPTREND_CONFIRMED:
            assert cur_state != MarketState.DOWNTREND_CONFIRMED, (
                f"UPTREND_CONFIRMED jumped straight to DOWNTREND_CONFIRMED at {events[i].ts} "
                "without an intervening warning/CHOCH state"
            )
        if prev_state == MarketState.DOWNTREND_CONFIRMED:
            assert cur_state != MarketState.UPTREND_CONFIRMED, (
                f"DOWNTREND_CONFIRMED jumped straight to UPTREND_CONFIRMED at {events[i].ts} "
                "without an intervening warning/CHOCH state"
            )


def test_downtrend_plus_break_above_meaningful_lh_produces_choch_first_not_immediate_uptrend():
    # SL1 -> SH1 -> break below SL1 (Bearish BOS) -> LL -> rebound -> LH below SH1
    # (Downtrend confirmed) -> break back above that LH -> must be BULLISH_CHOCH,
    # landing in DOWNTREND_WARNING (the CONFIRMED downtrend is now under warning
    # — the state name reflects which established trend is being challenged,
    # not the direction of the challenge), never an immediate UPTREND_CONFIRMED.
    from engine.market_state.structure import label_structure
    from engine.market_state.swings import detect_swings
    from engine.market_state.trend import classify_trend

    bearish_closes = [round(21.8 - c, 2) for c in BULLISH_CLOSES]  # mirrors the tested bullish sequence
    reclaim_extension = [round(21.8 - c, 2) for c in REVERSAL_EXTENSION]  # mirrors the bullish reversal shape
    df = make_flat_df(bearish_closes + reclaim_extension)
    points = label_structure(detect_swings(df, left_bars=2, right_bars=2))
    events = classify_trend(df, points)

    assert any(e.state == MarketState.DOWNTREND_CONFIRMED for e in events)
    bullish_choch_events = [e for e in events if e.evidence.get("event") == "BULLISH_CHOCH"]
    assert bullish_choch_events, "expected a Bullish CHOCH when reclaiming above the latest LH"
    choch = bullish_choch_events[0]
    assert choch.state == MarketState.DOWNTREND_WARNING
    assert choch.state != MarketState.UPTREND_CONFIRMED


# ---- D: every field in a snapshot is computed as of the same final bar ----


def test_snapshot_as_of_matches_the_dataframes_last_bar_and_time_of_day_is_consistent():
    df = make_flat_df(BULLISH_CLOSES)
    snapshot = build_snapshot("SPY", "5min", df, data_source="synthetic", timeframe_minutes=5)

    assert snapshot.as_of == df.index[-1]
    assert snapshot.time_of_day == classify_time_of_day(df.index[-1])
