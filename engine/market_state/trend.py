"""Trend-confirmation finite state machine (see TRADING_OS_DESIGN.md §8 and the
original brief §7):

    RANGE --close breaks above prior major high (Bullish BOS)--> TRANSITIONAL_BULLISH
    TRANSITIONAL_BULLISH --pullback holds above the prior major low, forms HL--> UPTREND_CONFIRMED
    UPTREND_CONFIRMED --close breaks below latest HL (Bearish CHOCH)--> UPTREND_WARNING
    UPTREND_WARNING --new LL, rebound LH, then new LL--> DOWNTREND_CONFIRMED
    UPTREND_WARNING --price reclaims prior high, new HL forms instead--> UPTREND_CONFIRMED (CHOCH invalidated)
    (mirror image for the bearish side)

Critically, UPTREND_CONFIRMED is never emitted on the breakout bar itself — only
once the subsequent pullback confirms a Higher Low. Until then the state is
TRANSITIONAL_BULLISH, exactly as specified. CHOCH always carries the
"STRUCTURE WARNING — REVERSAL NOT YET CONFIRMED" note in its evidence.

Operates only on MAJOR swing points plus each bar's close; never looks beyond
the `df` it is given.
"""
from __future__ import annotations

import pandas as pd

from .types import MarketState, MarketStateEvent, StructureLabel, SwingPoint, SwingSignificance, SwingType

ALGORITHM_VERSION = "trend-v1"

_REVERSAL_WARNING_NOTE = "STRUCTURE WARNING — REVERSAL NOT YET CONFIRMED"


def _event(ts, state: MarketState, label: StructureLabel, evidence: dict) -> MarketStateEvent:
    return MarketStateEvent(
        ts=ts,
        state=state,
        structure_label=label,
        evidence={**evidence, "algorithm_version": ALGORITHM_VERSION},
    )


def classify_trend(df: pd.DataFrame, swing_points: list[SwingPoint]) -> list[MarketStateEvent]:
    majors = sorted(
        [p for p in swing_points if p.significance == SwingSignificance.MAJOR],
        key=lambda p: p.bar_index,
    )
    events: list[MarketStateEvent] = []

    state = MarketState.RANGE
    last_major_high: SwingPoint | None = None
    last_major_low: SwingPoint | None = None

    low_at_break: SwingPoint | None = None   # SL1 in place when a bullish BOS occurred
    high_at_break: SwingPoint | None = None  # SH1 in place when a bearish BOS occurred
    latest_hl: SwingPoint | None = None      # most recent confirmed Higher Low (CHOCH break level in an uptrend)
    latest_lh: SwingPoint | None = None      # most recent confirmed Lower High (CHOCH break level in a downtrend)

    warning_extreme: SwingPoint | None = None   # first LL after a bearish CHOCH / first HH after a bullish CHOCH
    warning_pullback: SwingPoint | None = None  # the rebound LH / HL that follows warning_extreme

    m_idx = 0
    n_majors = len(majors)
    closes = df["close"]

    for i in range(len(df)):
        ts = df.index[i]
        close = closes.iloc[i]

        # Snapshot references BEFORE applying this bar's newly-confirmed majors, so a
        # breakout bar that is *also* the new extreme point is still checked against
        # the prior (pre-break) level, not the level it is itself about to become.
        pre_high, pre_low = last_major_high, last_major_low

        new_majors_this_bar: list[SwingPoint] = []
        while m_idx < n_majors and majors[m_idx].bar_index <= i:
            p = majors[m_idx]
            new_majors_this_bar.append(p)
            if p.swing_type == SwingType.HIGH:
                last_major_high = p
            else:
                last_major_low = p
            m_idx += 1

        if state == MarketState.RANGE:
            if pre_high is not None and close > pre_high.price:
                low_at_break = pre_low
                state = MarketState.TRANSITIONAL_BULLISH
                events.append(_event(ts, state, StructureLabel.HH, {
                    "event": "BULLISH_BOS", "broken_level": pre_high.price,
                }))
            elif pre_low is not None and close < pre_low.price:
                high_at_break = pre_high
                state = MarketState.TRANSITIONAL_BEARISH
                events.append(_event(ts, state, StructureLabel.LL, {
                    "event": "BEARISH_BOS", "broken_level": pre_low.price,
                }))

        elif state == MarketState.TRANSITIONAL_BULLISH:
            for p in new_majors_this_bar:
                if p.swing_type != SwingType.LOW or low_at_break is None:
                    continue
                if p.price > low_at_break.price:
                    latest_hl = p
                    state = MarketState.UPTREND_CONFIRMED
                    events.append(_event(ts, state, StructureLabel.HL, {"event": "UPTREND_CONFIRMED"}))
                else:
                    state = MarketState.RANGE
                    events.append(_event(ts, state, StructureLabel.NONE, {"event": "BULLISH_BOS_FAILED"}))
                break

        elif state == MarketState.TRANSITIONAL_BEARISH:
            for p in new_majors_this_bar:
                if p.swing_type != SwingType.HIGH or high_at_break is None:
                    continue
                if p.price < high_at_break.price:
                    latest_lh = p
                    state = MarketState.DOWNTREND_CONFIRMED
                    events.append(_event(ts, state, StructureLabel.LH, {"event": "DOWNTREND_CONFIRMED"}))
                else:
                    state = MarketState.RANGE
                    events.append(_event(ts, state, StructureLabel.NONE, {"event": "BEARISH_BOS_FAILED"}))
                break

        elif state == MarketState.UPTREND_CONFIRMED:
            if latest_hl is not None and close < latest_hl.price:
                state = MarketState.UPTREND_WARNING
                warning_extreme = None
                warning_pullback = None
                events.append(_event(ts, state, StructureLabel.NONE, {
                    "event": "BEARISH_CHOCH", "broken_level": latest_hl.price, "note": _REVERSAL_WARNING_NOTE,
                }))
            else:
                for p in new_majors_this_bar:
                    if p.swing_type == SwingType.HIGH:
                        label = StructureLabel.HH if pre_high is None or p.price > pre_high.price else StructureLabel.LH
                        events.append(_event(ts, state, label, {"event": "STRUCTURE_UPDATE"}))
                    elif latest_hl is not None and p.price > latest_hl.price:
                        latest_hl = p
                        events.append(_event(ts, state, StructureLabel.HL, {"event": "STRUCTURE_UPDATE"}))

        elif state == MarketState.UPTREND_WARNING:
            for p in new_majors_this_bar:
                if warning_extreme is None and p.swing_type == SwingType.LOW:
                    warning_extreme = p
                elif warning_extreme is not None and warning_pullback is None and p.swing_type == SwingType.HIGH:
                    warning_pullback = p
                elif (
                    warning_extreme is not None
                    and warning_pullback is not None
                    and p.swing_type == SwingType.LOW
                    and p.price < warning_extreme.price
                ):
                    state = MarketState.DOWNTREND_CONFIRMED
                    latest_lh = warning_pullback
                    events.append(_event(ts, state, StructureLabel.LL, {"event": "DOWNTREND_CONFIRMED_FROM_CHOCH"}))
                    break
            if state == MarketState.UPTREND_WARNING and last_major_high is not None and close > last_major_high.price:
                state = MarketState.UPTREND_CONFIRMED
                events.append(_event(ts, state, StructureLabel.HH, {"event": "CHOCH_INVALIDATED"}))

        elif state == MarketState.DOWNTREND_CONFIRMED:
            if latest_lh is not None and close > latest_lh.price:
                state = MarketState.DOWNTREND_WARNING
                warning_extreme = None
                warning_pullback = None
                events.append(_event(ts, state, StructureLabel.NONE, {
                    "event": "BULLISH_CHOCH", "broken_level": latest_lh.price, "note": _REVERSAL_WARNING_NOTE,
                }))
            else:
                for p in new_majors_this_bar:
                    if p.swing_type == SwingType.LOW:
                        label = StructureLabel.LL if pre_low is None or p.price < pre_low.price else StructureLabel.HL
                        events.append(_event(ts, state, label, {"event": "STRUCTURE_UPDATE"}))
                    elif latest_lh is not None and p.price < latest_lh.price:
                        latest_lh = p
                        events.append(_event(ts, state, StructureLabel.LH, {"event": "STRUCTURE_UPDATE"}))

        elif state == MarketState.DOWNTREND_WARNING:
            for p in new_majors_this_bar:
                if warning_extreme is None and p.swing_type == SwingType.HIGH:
                    warning_extreme = p
                elif warning_extreme is not None and warning_pullback is None and p.swing_type == SwingType.LOW:
                    warning_pullback = p
                elif (
                    warning_extreme is not None
                    and warning_pullback is not None
                    and p.swing_type == SwingType.HIGH
                    and p.price > warning_extreme.price
                ):
                    state = MarketState.UPTREND_CONFIRMED
                    latest_hl = warning_pullback
                    events.append(_event(ts, state, StructureLabel.HH, {"event": "UPTREND_CONFIRMED_FROM_CHOCH"}))
                    break
            if state == MarketState.DOWNTREND_WARNING and last_major_low is not None and close < last_major_low.price:
                state = MarketState.DOWNTREND_CONFIRMED
                events.append(_event(ts, state, StructureLabel.LL, {"event": "CHOCH_INVALIDATED"}))

    return events
