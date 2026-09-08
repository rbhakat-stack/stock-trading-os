"""Two-stage swing detector (see TRADING_OS_DESIGN.md §10):

1. Candidate generation — cheap fractal-style local extrema over a rolling window.
2. Significance scoring — a weighted, ATR-normalized composite that separates
   MAJOR from MINOR swings. The score and every feature behind it are returned
   in `evidence`, never hidden — this is deliberately not a black box, and a
   trader can override the classification later (see `swing_overrides` in the
   schema) without losing the original algorithmic output.

No-look-ahead guarantee: a swing point at bar index i is only ever emitted once
`right_bars` bars after it already exist in `df`. As long as callers only ever
pass bars "up to now," this function cannot see the future — the same property
the backtest engine (Phase 5) relies on.
"""
from __future__ import annotations

import pandas as pd

from engine.features.volatility import atr
from .types import SwingPoint, SwingSignificance, SwingType

ALGORITHM_VERSION = "swings-v1"


def _find_candidates(df: pd.DataFrame, left: int, right: int) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    for i in range(left, n - right):
        window_high = highs[i - left : i + right + 1]
        if highs[i] == window_high.max():
            candidates.append((i, "HIGH"))
        window_low = lows[i - left : i + right + 1]
        if lows[i] == window_low.min():
            candidates.append((i, "LOW"))
    return candidates


def _dedupe_alternate(df: pd.DataFrame, candidates: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Collapse consecutive same-type candidates, keeping the more extreme one, so
    the resulting sequence strictly alternates HIGH/LOW — swing points are POINTS,
    not every local wiggle (see §4)."""
    if not candidates:
        return []
    cleaned: list[tuple[int, str]] = [candidates[0]]
    for idx, typ in candidates[1:]:
        last_idx, last_typ = cleaned[-1]
        if typ == last_typ:
            if typ == "HIGH":
                if df["high"].iloc[idx] >= df["high"].iloc[last_idx]:
                    cleaned[-1] = (idx, typ)
            else:
                if df["low"].iloc[idx] <= df["low"].iloc[last_idx]:
                    cleaned[-1] = (idx, typ)
        else:
            cleaned.append((idx, typ))
    return cleaned


def _score(
    df: pd.DataFrame,
    cleaned: list[tuple[int, str]],
    atr_series: pd.Series,
    major_threshold: float,
    left_bars: int,
    right_bars: int,
) -> list[SwingPoint]:
    fallback_atr = atr_series.mean()
    points: list[SwingPoint] = []

    for pos, (idx, typ) in enumerate(cleaned):
        price = float(df["high"].iloc[idx] if typ == "HIGH" else df["low"].iloc[idx])

        local_atr = atr_series.iloc[idx]
        if pd.isna(local_atr):
            local_atr = fallback_atr
        if not local_atr or pd.isna(local_atr) or local_atr <= 0:
            local_atr = 1.0

        parts = []
        amp_in_atr = amp_out_atr = None
        if pos > 0:
            prev_idx, prev_typ = cleaned[pos - 1]
            prev_price = df["high"].iloc[prev_idx] if prev_typ == "HIGH" else df["low"].iloc[prev_idx]
            amp_in_atr = abs(price - prev_price) / local_atr
            parts.append(min(amp_in_atr, 4.0) / 4.0)
        if pos < len(cleaned) - 1:
            next_idx, next_typ = cleaned[pos + 1]
            next_price = df["high"].iloc[next_idx] if next_typ == "HIGH" else df["low"].iloc[next_idx]
            amp_out_atr = abs(next_price - price) / local_atr
            parts.append(min(amp_out_atr, 4.0) / 4.0)

        score = round(sum(parts) / len(parts), 4) if parts else 0.0
        significance = SwingSignificance.MAJOR if score >= major_threshold else SwingSignificance.MINOR

        points.append(
            SwingPoint(
                ts=df.index[idx],
                price=price,
                swing_type=SwingType.HIGH if typ == "HIGH" else SwingType.LOW,
                significance=significance,
                score=score,
                bar_index=idx,
                evidence={
                    "amp_in_atr": round(amp_in_atr, 3) if amp_in_atr is not None else None,
                    "amp_out_atr": round(amp_out_atr, 3) if amp_out_atr is not None else None,
                    "left_bars": left_bars,
                    "right_bars": right_bars,
                    "major_threshold": major_threshold,
                    "algorithm_version": ALGORITHM_VERSION,
                },
            )
        )
    return points


def detect_swings(
    df: pd.DataFrame,
    left_bars: int = 3,
    right_bars: int = 3,
    atr_period: int = 14,
    major_threshold: float = 0.55,
) -> list[SwingPoint]:
    """`df` must be sorted ascending by a DatetimeIndex with open/high/low/close/volume columns."""
    if len(df) < left_bars + right_bars + 1:
        return []
    atr_series = atr(df, period=atr_period)
    raw_candidates = _find_candidates(df, left_bars, right_bars)
    cleaned = _dedupe_alternate(df, raw_candidates)
    return _score(df, cleaned, atr_series, major_threshold, left_bars, right_bars)
