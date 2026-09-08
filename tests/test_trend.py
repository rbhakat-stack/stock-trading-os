from conftest import make_flat_df

from engine.market_state.structure import label_structure
from engine.market_state.swings import detect_swings
from engine.market_state.trend import classify_trend
from engine.market_state.types import MarketState, StructureLabel

BULLISH_CLOSES = [
    10.0, 10.6, 11.2, 10.7, 10.1, 10.6, 11.0, 11.8, 11.3, 10.9,
    10.8, 11.1, 11.6, 12.1, 11.7, 11.3, 11.6, 11.4, 11.5,
]
# Mirror of BULLISH_CLOSES around 21.8, turning every local max into a local min
# and vice versa, so the same shape drives a downtrend confirmation instead.
BEARISH_CLOSES = [round(21.8 - c, 2) for c in BULLISH_CLOSES]


def _run(closes):
    df = make_flat_df(closes)
    points = detect_swings(df, left_bars=2, right_bars=2)
    points = label_structure(points)
    return df, classify_trend(df, points)


def test_bullish_sequence_confirms_uptrend_only_after_pullback_hl():
    df, events = _run(BULLISH_CLOSES)

    kinds = [e.evidence["event"] for e in events]
    assert "BULLISH_BOS" in kinds
    assert "UPTREND_CONFIRMED" in kinds

    bos_event = next(e for e in events if e.evidence["event"] == "BULLISH_BOS")
    confirm_event = next(e for e in events if e.evidence["event"] == "UPTREND_CONFIRMED")

    # The break bar must be TRANSITIONAL, never immediately UPTREND_CONFIRMED —
    # confirmation only happens later, on the pullback bar.
    assert bos_event.state == MarketState.TRANSITIONAL_BULLISH
    assert confirm_event.state == MarketState.UPTREND_CONFIRMED
    assert confirm_event.structure_label == StructureLabel.HL
    assert bos_event.ts < confirm_event.ts

    # Final state for the whole window should be a confirmed uptrend.
    assert events[-1].state == MarketState.UPTREND_CONFIRMED


def test_bearish_mirror_confirms_downtrend_only_after_pullback_lh():
    df, events = _run(BEARISH_CLOSES)

    kinds = [e.evidence["event"] for e in events]
    assert "BEARISH_BOS" in kinds
    assert "DOWNTREND_CONFIRMED" in kinds

    bos_event = next(e for e in events if e.evidence["event"] == "BEARISH_BOS")
    confirm_event = next(e for e in events if e.evidence["event"] == "DOWNTREND_CONFIRMED")

    assert bos_event.state == MarketState.TRANSITIONAL_BEARISH
    assert confirm_event.state == MarketState.DOWNTREND_CONFIRMED
    assert confirm_event.structure_label == StructureLabel.LH
    assert bos_event.ts < confirm_event.ts
    assert events[-1].state == MarketState.DOWNTREND_CONFIRMED


def test_choch_carries_reversal_not_confirmed_note():
    # Extend the bullish sequence with a sharp break back below the latest HL to
    # trigger a Bearish CHOCH, and confirm it's explicitly labeled a warning, not
    # a confirmed reversal.
    closes = BULLISH_CLOSES + [11.0, 10.3, 9.8, 9.5, 9.6, 9.4]
    df, events = _run(closes)

    choch_events = [e for e in events if e.evidence.get("event") == "BEARISH_CHOCH"]
    assert choch_events, "expected a Bearish CHOCH once price breaks the latest HL"
    choch = choch_events[0]
    assert choch.state == MarketState.UPTREND_WARNING
    assert choch.evidence["note"] == "STRUCTURE WARNING — REVERSAL NOT YET CONFIRMED"
    # CHOCH must never itself claim a confirmed downtrend.
    assert choch.state != MarketState.DOWNTREND_CONFIRMED
