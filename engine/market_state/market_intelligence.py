"""High-level Market Intelligence orchestrator (§27).

Ties every Phase 2 engine module into one `MarketIntelligenceSnapshot`. This is
the only module that composes the others — each individual engine module
(bos_choch, breakouts, consolidation, volume, volatility_regime,
opening_range, timeframe, trend_quality, support_resistance) stays
independently testable and importable on its own; none of them import this
one.

Persistence choices (§25): this orchestrator computes everything on demand,
every call — it never reads or writes the database itself. The caller
(app/pages/market_reader.py) persists only what migration 0003 actually adds
tables for (breakout events, opening-range events, enriched S/R zones).
Trend quality, consolidation/compression, volume/RVOL, volatility regime, and
multi-timeframe alignment are treated as view-time computations, not
historical facts to store yet — see the migration file for the full rationale.

This module never outputs a trading signal (§1/§28) — only market state,
structural events, context, quality, and warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..data_integrity.checks import check_bars, has_failure
from ..features.volatility import atr
from .bos_choch import BosEvaluation, ChochEvaluation, choch_banner_message, evaluate_bos, evaluate_latest_choch
from .breakouts import BreakoutEvaluation, classify_breakout
from .consolidation import ConsolidationEvaluation, evaluate_consolidation
from .opening_range import OpeningRangeEvaluation, compute_opening_range
from .structure import label_structure
from .support_resistance import EnrichedZone, build_zones, enrich_zones
from .swings import detect_swings
from .time_of_day import classify_time_of_day
from .timeframe import MultiTimeframeAlignment, compute_multi_timeframe_alignment
from .trend import classify_trend
from .trend_quality import TrendQualityEvaluation, classify_trend_quality
from .types import MarketState, MarketStateEvent, SwingPoint
from .volatility_regime import VolatilityClassification, classify_volatility
from .volume import RvolResult, average_volume, classify_volume_level, compute_rvol

ALGORITHM_VERSION = "market-intelligence-v1"


@dataclass(frozen=True)
class MarketIntelligenceSnapshot:
    symbol: str
    timeframe: str
    as_of: datetime | None
    data_source: str

    market_state: str | None
    trend_quality: TrendQualityEvaluation | None

    latest_bos: BosEvaluation | None
    latest_choch: ChochEvaluation | None

    support_zones: list[EnrichedZone]
    resistance_zones: list[EnrichedZone]

    consolidation: ConsolidationEvaluation | None
    breakout: BreakoutEvaluation | None

    volume_level: str
    rvol: RvolResult | None

    atr_value: float | None
    volatility: VolatilityClassification | None

    opening_range: OpeningRangeEvaluation | None
    multi_timeframe_alignment: MultiTimeframeAlignment | None
    time_of_day: str | None

    warnings: list[str] = field(default_factory=list)
    data_quality_ok: bool = True
    swing_points: list[SwingPoint] = field(default_factory=list)
    events: list[MarketStateEvent] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def latest_structural_event(self) -> MarketStateEvent | None:
        """The chronologically latest structural event of ANY type (BOS, CHOCH,
        a confirmed-from-CHOCH transition, a plain structure update, ...) — use
        this wherever "latest structural event" is displayed or reasoned about.

        This is deliberately NOT the same thing as `latest_bos`: the FSM
        (trend.classify_trend) only ever emits a literal BULLISH_BOS/BEARISH_BOS
        event when transitioning out of RANGE. A later trend reversal happens
        through the CHOCH pathway (BEARISH_CHOCH -> DOWNTREND_CONFIRMED_FROM_CHOCH),
        which carries a different event label — so `latest_bos` can be
        chronologically stale (e.g. from an much earlier up-leg) relative to
        the market's actual most recent event. Conflating the two produced a
        real bug: the UI showed a stale "BULLISH BOS" as the "Latest Structural
        Event" for a symbol that had since reversed into DOWNTREND_CONFIRMED.
        `latest_bos` remains available separately for "what was the most recent
        BOS and how strong was it," which is a legitimate, narrower question.
        """
        return self.events[-1] if self.events else None


def _failed_snapshot(symbol: str, timeframe: str, data_source: str, issues) -> MarketIntelligenceSnapshot:
    return MarketIntelligenceSnapshot(
        symbol=symbol, timeframe=timeframe, as_of=None, data_source=data_source,
        market_state=None, trend_quality=None, latest_bos=None, latest_choch=None,
        support_zones=[], resistance_zones=[], consolidation=None, breakout=None,
        volume_level="INSUFFICIENT_DATA", rvol=None, atr_value=None, volatility=None,
        opening_range=None, multi_timeframe_alignment=None, time_of_day=None,
        warnings=[f"DATA QUALITY FAILURE: {i.code} - {i.message}" for i in issues if i.severity == "FAILURE"],
        data_quality_ok=False,
        evidence={"algorithm_version": ALGORITHM_VERSION},
    )


def _compute_state_for_timeframe(df: pd.DataFrame | None) -> MarketState | None:
    if df is None or df.empty:
        return None
    points = label_structure(detect_swings(df))
    events = classify_trend(df, points)
    return events[-1].state if events else MarketState.RANGE


def build_snapshot(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    data_source: str,
    timeframe_minutes: int,
    atr_period: int = 14,
    opening_range_minutes: int = 30,
    higher_timeframe_data: dict[str, pd.DataFrame] | None = None,
    now: pd.Timestamp | None = None,
) -> MarketIntelligenceSnapshot:
    """`higher_timeframe_data` maps a timeframe label (e.g. "1hour") to bars the
    CALLER already fetched — this module never fetches data itself, keeping
    engine/ free of data_provider/Streamlit/Supabase imports."""
    issues = check_bars(df, timeframe_minutes=timeframe_minutes, now=now)
    if has_failure(issues):
        return _failed_snapshot(symbol, timeframe, data_source, issues)
    warnings = [f"{i.code}: {i.message}" for i in issues if i.severity == "WARNING"]

    atr_series = atr(df, period=atr_period)
    swing_points = label_structure(detect_swings(df))
    events = classify_trend(df, swing_points)
    latest_state = events[-1].state if events else MarketState.RANGE

    trend_quality = classify_trend_quality(swing_points, latest_state, recent_events=events)

    latest_bos_event = next(
        (e for e in reversed(events) if e.evidence.get("event") in ("BULLISH_BOS", "BEARISH_BOS")), None
    )
    latest_bos = evaluate_bos(df, latest_bos_event, atr_series) if latest_bos_event is not None else None

    latest_choch = evaluate_latest_choch(events)
    if latest_choch is not None:
        message, severity = choch_banner_message(latest_choch)
        if severity == "warning":
            warnings.append(message)

    consolidation = evaluate_consolidation(df, swing_points, atr_series)

    latest_atr_for_zones = float(atr_series.dropna().iloc[-1]) if atr_series.notna().any() else 1.0
    raw_zones = build_zones(swing_points, latest_atr_for_zones)
    enriched = enrich_zones(df, raw_zones, atr_series)
    support_zones = [z for z in enriched if z.zone_type == "SUPPORT"]
    resistance_zones = [z for z in enriched if z.zone_type == "RESISTANCE"]

    # A breakout evaluation of the most recent BOS, reusing the SAME
    # classify_breakout the S/R and opening-range engines use — a BOS is just a
    # breakout whose level happens to be a swing high/low.
    breakout = None
    if latest_bos_event is not None:
        try:
            bar_idx = df.index.get_loc(latest_bos_event.ts)
            direction = "UP" if latest_bos_event.evidence["event"] == "BULLISH_BOS" else "DOWN"
            breakout = classify_breakout(df, latest_bos_event.evidence["broken_level"], direction, bar_idx, atr_series)
        except KeyError:
            breakout = None

    last_idx = len(df) - 1
    current_vol = float(df["volume"].iloc[last_idx])
    avg20 = average_volume(df, 20).iloc[last_idx]
    volume_level = classify_volume_level(current_vol, float(avg20) if pd.notna(avg20) else None)
    rvol = compute_rvol(df, last_idx)

    volatility = classify_volatility(atr_series, last_idx)

    try:
        opening_range = compute_opening_range(df, atr_series, window_minutes=opening_range_minutes)
    except Exception:  # noqa: BLE001 - opening range is enrichment, never fatal to the whole snapshot
        opening_range = None

    multi_tf = None
    if higher_timeframe_data:
        states: dict[str, MarketState | None] = {timeframe: latest_state}
        for tf_label, tf_df in higher_timeframe_data.items():
            states[tf_label] = _compute_state_for_timeframe(tf_df)
        multi_tf = compute_multi_timeframe_alignment(states)

    latest_atr = atr_series.iloc[last_idx]

    return MarketIntelligenceSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        as_of=df.index[-1],
        data_source=data_source,
        market_state=latest_state.value,
        trend_quality=trend_quality,
        latest_bos=latest_bos,
        latest_choch=latest_choch,
        support_zones=support_zones,
        resistance_zones=resistance_zones,
        consolidation=consolidation,
        breakout=breakout,
        volume_level=volume_level,
        rvol=rvol,
        atr_value=float(latest_atr) if pd.notna(latest_atr) else None,
        volatility=volatility,
        opening_range=opening_range,
        multi_timeframe_alignment=multi_tf,
        time_of_day=classify_time_of_day(df.index[-1]),
        warnings=warnings,
        data_quality_ok=True,
        swing_points=swing_points,
        events=events,
        evidence={"algorithm_version": ALGORITHM_VERSION},
    )
