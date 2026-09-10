"""Phase 4 playbook taxonomy — the canonical vocabulary for playbook
identity, family, and lifecycle status. See the package docstring for the
architectural boundary this vocabulary respects.
"""
from __future__ import annotations

from enum import Enum


class PlaybookFamily(str, Enum):
    """One family per playbook (§4 of the Phase 4 report) — used for later
    statistics/performance reporting grouping, never for evaluation logic."""
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT = "BREAKOUT"
    FAILED_BREAKOUT_REVERSAL = "FAILED_BREAKOUT_REVERSAL"
    MEAN_REVERSION = "MEAN_REVERSION"
    OPENING_RANGE = "OPENING_RANGE"
    VWAP = "VWAP"
    MOMENTUM_REVERSAL = "MOMENTUM_REVERSAL"


class PlaybookId(str, Enum):
    """Every playbook this Phase considered — implemented AND deferred. A
    deferred id still exists here (with enabled=False and a documented
    reason on its PlaybookDefinition) so the taxonomy is complete and later
    phases don't need to invent new ids when the underlying signal exists."""
    BREAKOUT_RETEST_LONG = "BREAKOUT_RETEST_LONG"
    BREAKOUT_RETEST_SHORT = "BREAKOUT_RETEST_SHORT"
    TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"
    TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"
    FAILED_BREAKOUT_REVERSAL_LONG = "FAILED_BREAKOUT_REVERSAL_LONG"
    FAILED_BREAKOUT_REVERSAL_SHORT = "FAILED_BREAKOUT_REVERSAL_SHORT"
    OPENING_RANGE_BREAKOUT_LONG = "OPENING_RANGE_BREAKOUT_LONG"
    OPENING_RANGE_BREAKOUT_SHORT = "OPENING_RANGE_BREAKOUT_SHORT"
    RANGE_MEAN_REVERSION_LONG = "RANGE_MEAN_REVERSION_LONG"
    RANGE_MEAN_REVERSION_SHORT = "RANGE_MEAN_REVERSION_SHORT"
    VWAP_RECLAIM_LONG = "VWAP_RECLAIM_LONG"
    VWAP_REJECTION_SHORT = "VWAP_REJECTION_SHORT"


class SetupStatus(str, Enum):
    """The PLAYBOOK's own lifecycle status — deliberately a SEPARATE concept
    from engine.trade.decision's DECISION_REJECT/WAIT/CONDITIONAL/QUALIFIED
    (see §26 of the Phase 4 report: "Playbook: TRIGGERED, Trade Decision:
    REJECT" must remain expressible and never conflated)."""
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"


class EligibilityStatus(str, Enum):
    """WHY a playbook is/isn't even being considered for this snapshot —
    finer-grained than SetupStatus.NOT_ELIGIBLE, for explainability."""
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE_REGIME = "NOT_ELIGIBLE_REGIME"
    NOT_ELIGIBLE_TIMEFRAME = "NOT_ELIGIBLE_TIMEFRAME"
    NOT_ELIGIBLE_DATA = "NOT_ELIGIBLE_DATA"
    NOT_IMPLEMENTABLE_YET = "NOT_IMPLEMENTABLE_YET"


# Deterministic status precedence for ranking (§17) — higher first.
SETUP_STATUS_RANK: dict[str, int] = {
    SetupStatus.TRIGGERED.value: 3,
    SetupStatus.FORMING.value: 2,
    SetupStatus.INVALIDATED.value: 1,
    SetupStatus.NOT_ELIGIBLE.value: 0,
}

# Supported timeframe vocabulary — matches app/pages/trade_planner.py's
# TIMEFRAME_MINUTES keys exactly (5min/15min/1hour/1day), never a separate
# invented label set.
SUPPORTED_TIMEFRAME_LABELS = ("5min", "15min", "1hour", "1day")
