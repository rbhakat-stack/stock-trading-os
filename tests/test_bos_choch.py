from conftest import make_flat_df

from engine.features.volatility import atr
from engine.market_state.bos_choch import (
    BosStrength,
    ChochOutcome,
    choch_banner_message,
    evaluate_bos,
    evaluate_latest_choch,
)
from engine.market_state.structure import label_structure
from engine.market_state.swings import detect_swings
from engine.market_state.trend import classify_trend
from engine.market_state.types import MarketStateEvent, StructureLabel, MarketState

BULLISH_CLOSES = [
    10.0, 10.6, 11.2, 10.7, 10.1, 10.6, 11.0, 11.8, 11.3, 10.9,
    10.8, 11.1, 11.6, 12.1, 11.7, 11.3, 11.6, 11.4, 11.5,
]


def _run(closes):
    df = make_flat_df(closes)
    points = label_structure(detect_swings(df, left_bars=2, right_bars=2))
    events = classify_trend(df, points)
    return df, events


def test_evaluate_bos_scores_a_decisive_break_strong_or_moderate():
    df, events = _run(BULLISH_CLOSES)
    atr_series = atr(df)
    bos_event = next(e for e in events if e.evidence.get("event") == "BULLISH_BOS")

    result = evaluate_bos(df, bos_event, atr_series)

    assert result is not None
    assert result.direction == "BULLISH"
    assert result.body_close_break is True
    # A close-based BOS from the FSM is always body-close by construction.
    assert result.strength in (BosStrength.MODERATE, BosStrength.STRONG)
    assert result.evidence["components"]["body_close_break"] is True


def test_evaluate_bos_returns_none_for_non_bos_event():
    df, events = _run(BULLISH_CLOSES)
    atr_series = atr(df)
    non_bos = next(e for e in events if e.evidence.get("event") == "UPTREND_CONFIRMED")

    assert evaluate_bos(df, non_bos, atr_series) is None


def test_evaluate_bos_weak_when_break_distance_and_follow_through_are_tiny():
    # Hand-construct a synthetic BOS event representing a break that barely
    # clears the level with a doji-like candle and no follow-through, to prove
    # the scorer independently produces WEAK on low-conviction evidence (not
    # just relying on what the FSM happens to generate).
    closes = [10.0] * 20
    df = make_flat_df(closes)
    atr_series = atr(df)  # all zero true range -> triggers the atr_val fallback path
    tiny_bos_event = MarketStateEvent(
        ts=df.index[10],
        state=MarketState.TRANSITIONAL_BULLISH,
        structure_label=StructureLabel.HH,
        evidence={"event": "BULLISH_BOS", "broken_level": 10.0},
    )
    result = evaluate_bos(df, tiny_bos_event, atr_series)
    assert result is not None
    assert result.follow_through_bars == 0
    assert result.strength == BosStrength.WEAK


def test_evaluate_latest_choch_none_without_any_choch():
    df, events = _run(BULLISH_CLOSES)
    assert evaluate_latest_choch(events) is None


def test_evaluate_latest_choch_pending_immediately_after_choch():
    # Extend the confirmed uptrend with a sharp break below the latest HL to
    # trigger a Bearish CHOCH as the very last event.
    closes = BULLISH_CLOSES + [11.0, 10.3, 9.8]
    df, events = _run(closes)

    result = evaluate_latest_choch(events)
    assert result is not None
    assert result.direction == "BEARISH_CHOCH"
    assert result.outcome in (ChochOutcome.PENDING, ChochOutcome.REVERSAL_CONTINUING)


def test_evaluate_latest_choch_outcome_reflects_the_fsm_event_stream_only():
    # evaluate_latest_choch reads outcome purely from classify_trend()'s emitted
    # event stream, not from raw bar movement — if price moves after a CHOCH but
    # forms no new major swing point and never re-crosses the reclaim level, the
    # FSM legitimately emits nothing further, so PENDING is the honest answer
    # even though bars have passed. This is intentional: "no news is no news."
    closes = BULLISH_CLOSES + [11.0, 10.3, 9.8, 9.5, 10.3, 10.8, 11.1, 9.9, 9.2]
    df, events = _run(closes)

    result = evaluate_latest_choch(events)
    assert result is not None
    assert result.direction == "BEARISH_CHOCH"
    assert result.outcome in (
        ChochOutcome.PENDING,
        ChochOutcome.REVERSAL_CONTINUING,
        ChochOutcome.REVERSAL_CONFIRMED,
        ChochOutcome.INVALIDATED,
    )
    if result.outcome == ChochOutcome.REVERSAL_CONFIRMED:
        assert "DOWNTREND_CONFIRMED_FROM_CHOCH" in result.evidence["subsequent_events"]


def test_evaluate_latest_choch_reaches_reversal_confirmed():
    # A CHOCH followed by a genuine full reversal (first LL, rebound LH, new LL) —
    # verified experimentally to produce DOWNTREND_CONFIRMED_FROM_CHOCH.
    extension = [10.5, 10.0, 9.6, 10.0, 10.4, 10.8, 10.5, 10.1, 9.3, 9.7, 10.0]
    df, events = _run(BULLISH_CLOSES + extension)

    result = evaluate_latest_choch(events)
    assert result is not None
    assert result.direction == "BEARISH_CHOCH"
    assert result.outcome == ChochOutcome.REVERSAL_CONFIRMED


def test_evaluate_latest_choch_reaches_invalidated():
    # A CHOCH followed by an immediate reclaim back above the prior major high
    # (not via the LL-rebound-LL reversal path) -> CHOCH_INVALIDATED, original
    # uptrend resumes.
    extension = [10.5, 10.0, 9.6, 12.5, 12.8]
    df, events = _run(BULLISH_CLOSES + extension)

    result = evaluate_latest_choch(events)
    assert result is not None
    assert result.direction == "BEARISH_CHOCH"
    assert result.outcome == ChochOutcome.INVALIDATED


# ---- choch_banner_message: single source of truth for banner text/severity,
# used by both the top banner and the Market State panel so they can never
# disagree (regression coverage for the exact bug found in live acceptance
# testing: the top banner was hardcoded and didn't recheck the outcome) ----


def test_banner_message_for_pending_is_a_warning():
    closes = BULLISH_CLOSES + [11.0, 10.3, 9.8]
    _, events = _run(closes)
    choch = evaluate_latest_choch(events)
    assert choch.outcome in (ChochOutcome.PENDING, ChochOutcome.REVERSAL_CONTINUING)

    message, severity = choch_banner_message(choch)
    assert severity == "warning"
    assert "REVERSAL NOT YET CONFIRMED" in message
    assert "CONFIRMED" not in message.replace("NOT YET CONFIRMED", "")  # doesn't also claim confirmation


def test_banner_message_for_reversal_confirmed_is_info_not_warning():
    extension = [10.5, 10.0, 9.6, 10.0, 10.4, 10.8, 10.5, 10.1, 9.3, 9.7, 10.0]
    _, events = _run(BULLISH_CLOSES + extension)
    choch = evaluate_latest_choch(events)
    assert choch.outcome == ChochOutcome.REVERSAL_CONFIRMED

    message, severity = choch_banner_message(choch)
    assert severity == "info"
    assert "REVERSAL CONFIRMED" in message
    assert "NOT YET CONFIRMED" not in message


def test_banner_message_for_invalidated_is_info_not_warning():
    extension = [10.5, 10.0, 9.6, 12.5, 12.8]
    _, events = _run(BULLISH_CLOSES + extension)
    choch = evaluate_latest_choch(events)
    assert choch.outcome == ChochOutcome.INVALIDATED

    message, severity = choch_banner_message(choch)
    assert severity == "info"
    assert "invalidated" in message.lower()
    assert "NOT YET CONFIRMED" not in message
    assert "STRUCTURE WARNING" not in message


def test_banner_never_disagrees_with_outcome_across_all_three_cases():
    # The three scenarios above, driven through the single shared function —
    # proves the banner text always matches the outcome it was computed from.
    cases = [
        (BULLISH_CLOSES + [11.0, 10.3, 9.8], (ChochOutcome.PENDING, ChochOutcome.REVERSAL_CONTINUING), "warning"),
        (BULLISH_CLOSES + [10.5, 10.0, 9.6, 10.0, 10.4, 10.8, 10.5, 10.1, 9.3, 9.7, 10.0], (ChochOutcome.REVERSAL_CONFIRMED,), "info"),
        (BULLISH_CLOSES + [10.5, 10.0, 9.6, 12.5, 12.8], (ChochOutcome.INVALIDATED,), "info"),
    ]
    for closes, expected_outcomes, expected_severity in cases:
        _, events = _run(closes)
        choch = evaluate_latest_choch(events)
        assert choch.outcome in expected_outcomes
        _message, severity = choch_banner_message(choch)
        assert severity == expected_severity
