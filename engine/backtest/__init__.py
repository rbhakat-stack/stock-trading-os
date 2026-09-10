"""Phase 5.0 — historical data foundation for the future backtesting/
statistical-validation layer. See the Phase 5 architecture & design report
(delivered as a chat report, not a repo file) for the full rationale; this
package implements ONLY the foundation items approved for Phase 5.0:

  - a historical-data-source abstraction (bar_source.py) that keeps the
    replay/outcome/statistics engines (Phase 5.1+, NOT built yet) decoupled
    from Supabase specifically, with hard synthetic/real and
    adjustment-status provenance enforcement,
  - a deterministic, session-aligned OHLCV resampler (resampling.py),
  - a minimal NYSE/Nasdaq trading-calendar (calendar.py) sufficient to tell
    "market closed" apart from "data genuinely missing during an open
    session,"
  - a universe abstraction (universe.py) that distinguishes ALL DISCOVERED
    SYMBOLS from an ELIGIBLE TRADING UNIVERSE without yet implementing any
    Phase 6 eligibility rule,
  - stable, forward-looking data contracts (contracts.py) — BacktestRunSpec
    (which pins an explicit playbook_id+version; a historical date NEVER
    silently selects a version) and StatisticalValidationResult (the shape
    a future Phase 6 will query, without knowing how Phase 5 computed it) —
    neither is implemented/computed yet, only their SHAPE is fixed,
  - a small, explicitly-scoped historical-bar ingestion path (ingestion.py)
    for a handful of symbols at a time, never a full-universe backfill.

ARCHITECTURAL BOUNDARY (do not blur this — mirrors engine/playbooks/__init__.py):
  - engine/backtest/ does NOT implement historical replay, trade-outcome
    simulation, or any statistical calculation yet (Phase 5.1/5.2/5.3,
    explicitly deferred). Nothing here computes a win rate, an R-multiple,
    or an expectancy.
  - engine/backtest/ does NOT implement daily universe scanning, cross-symbol
    recommendation ranking, or any Phase 6 concept. universe.py's
    TradingUniverse is a plain data container + a pluggable eligibility
    filter seam — it contains no market-cap/price/ADV/RVOL filtering logic.
  - engine/backtest/ never imports engine/risk/ or reads AccountRiskState —
    Phase 5 statistical evidence is deliberately account-independent; see
    the design report's §25 for why running Phase 3 gates against historical
    bars would not even be a meaningful operation, let alone a shortcut.
  - Like engine/playbooks/engine.py's `client=None` pattern, modules here
    that need database access take a plain (duck-typed) `client` parameter
    and late-import repository/ functions inside the function body — never
    a module-level `from supabase import Client` — so pure-logic pieces
    (resampling, calendar, provenance guards, universe eligibility) stay
    importable and unit-testable with zero Supabase/Streamlit dependency.

UNRESOLVED, RECORDED FOR PHASE 5.2 (§11 of the Phase 5.0 brief — do not
solve this in Phase 5.0, but do not forget it either):

  A limit/zone-entry candidate must NOT be classified NEVER_FILLED merely
  because the immediately following bar failed to trade into the entry
  zone — that would understate fill rates versus how a live recommendation
  would actually be worked. Phase 5.2's outcome engine must define a
  deterministic, playbook-compatible entry LIFECYCLE:

      TRIGGERED -> ENTRY_ACTIVE -> FILLED | EXPIRED | INVALIDATED

  The allowed entry-validity window (how long ENTRY_ACTIVE may persist
  before EXPIRED) may be playbook-specific or policy/config-driven, but
  whatever Phase 5.2 chooses MUST be the exact same window a future live
  recommendation flow (Phase 6) would use to decide whether a signal is
  still actionable — the task's own non-negotiable: BACKTEST EXECUTION
  RULE must never diverge from LIVE RECOMMENDATION EXECUTION RULE.

APPROVED FUTURE BACKTEST PRINCIPLES (§15 of the Phase 5.0 brief) — policy
decisions recorded now for Phase 5.1/5.2/5.3 to implement, not implemented
by anything in Phase 5.0 itself:

  A. Same-bar stop+target collision: assume STOP FIRST when only OHLC data
     is available; also persist `same_bar_collision=true` on that outcome.
  B. Entry: a signal known at bar close cannot assume a fill at that same
     historical close. Market-style entries use the next available
     executable price. Limit/zone-entry lifecycle: see the unresolved note
     above — finalized in Phase 5.2, not before.
  C. Multiple targets: primary Phase 5 v1 statistics use full exit at
     Target 1. Target 2 remains descriptive/research data only.
  D. Intraday holding: default expiry is session end, unless a playbook
     explicitly defines its own overnight-holding rule.
  E. Daily holding: 20 trading days MAY remain a configurable initial
     research default — explicitly NOT an empirically proven parameter.
  F. Transaction costs: support GROSS and NET statistics side by side.
     Phase 5.0 hardcodes no slippage/commission number anywhere — the
     cost-model interface is created later (Phase 5.2/5.3); numeric
     defaults require explicit approval before any statistic is trusted.
  G. Validation taxonomy: NOT_TESTED / INSUFFICIENT_SAMPLE / NO_EDGE /
     POSITIVE_EDGE (see contracts.py::ValidationStatus) — thresholds
     between these are a VERSIONED POLICY, never a bare magic number.
     POSITIVE_EDGE is evidence, not a recommendation; Phase 6 owns a
     SEPARATE statistical-gate policy for that decision.
  H. Sample size: do not present any fixed number (e.g. 20) as
     scientifically sufficient FOR A RECOMMENDATION — it may serve as a
     configurable descriptive/display threshold in Phase 5.3, while Phase
     6's eventual recommendation gate can and likely should be stricter.
  I. Corporate actions: flag-and-block where adjustment integrity cannot be
     verified (see bar_source.py::require_verified_adjustment). Automatic
     corporate-action reconstruction is deferred indefinitely, not "later
     in Phase 5."
"""
