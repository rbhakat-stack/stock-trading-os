"""Display-only formatting helpers for risk figures.

Pure and UI-framework-free (no Streamlit import) so it's directly unit
testable. This module NEVER decides anything — engine/risk/limits.py's
comparisons remain the sole source of truth for whether a limit is breached.
It exists because a 2-decimal display (e.g. -0.999% rounding to -1.00%) can
visually contradict a correct NORMAL kill-switch state when the true value
sits just inside the boundary — see the Phase 3 UI-precision bug report.
"""
from __future__ import annotations

DRAWDOWN_DISPLAY_PRECISION = 3


def format_drawdown_line(label: str, current_pct: float, max_loss_pct: float, precision: int = DRAWDOWN_DISPLAY_PRECISION) -> str:
    """e.g. "DAILY DRAWDOWN: -0.999% (limit: -1.000%, buffer: 0.001%)".

    `buffer` is how much further drawdown is allowed before the limit trips —
    positive while still inside the limit, negative once already breached by
    that amount. Uses the same sign convention as AccountRiskState's drawdown
    properties: 0 when flat, negative when losing.
    """
    threshold_pct = -abs(max_loss_pct)
    buffer_pct = current_pct - threshold_pct
    return (
        f"{label}: {current_pct:.{precision}f}% "
        f"(limit: {threshold_pct:.{precision}f}%, buffer: {buffer_pct:.{precision}f}%)"
    )
