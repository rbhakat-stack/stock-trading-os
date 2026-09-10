"""Phase 5.1 — deterministic, no-look-ahead historical replay engine.

Answers exactly one question: "at each historical bar, what would Phase 2
Market Intelligence and Phase 4 Playbook Engine have known at that exact
moment?" It does NOT simulate trade outcomes, compute R-multiples, or
compute any statistic — see engine/backtest/__init__.py's §14/§15 boundary
notes (unchanged from Phase 5.0, still binding).

ARCHITECTURAL INVARIANT (§2 — LIVE RULE == BACKTEST RULE): this module calls
engine.market_state.market_intelligence.build_snapshot and
engine.playbooks.engine.evaluate_all_playbooks UNCHANGED, exactly as
app/pages/trade_planner.py does live. There is no second implementation of
trend/breakout/consolidation/playbook logic anywhere in this file — the
ONLY new logic here is (a) how the `df` passed to those functions is
constructed at each historical cursor (§4's truncation rules) and (b) how
the results are filtered into a transition log (§5).

REFERENCE VS OPTIMIZED (§9): `run_replay(..., use_resample_cache=False)` is
the REFERENCE path — full, from-scratch recomputation, prioritizing
correctness. `use_resample_cache=True` is the OPTIMIZED path: the ONLY
difference is how higher-timeframe inputs are prepared (an
`_IncrementalResampleCache` that reuses already-CLOSED sessions' resampled
bars instead of re-resampling the entire growing history every cursor step)
— `build_snapshot`/`evaluate_all_playbooks` themselves are called
identically either way. See tests/test_backtest_replay_optimized.py for the
proof of exact equivalence between the two paths.

PHASE 2 IS NOT OPTIMIZED HERE (§10): build_snapshot's own internal cost
(swing detection + trend FSM, both O(n) per call with no seam to inject
precomputed state) remains the dominant replay cost and is NOT touched in
this round — see the Phase 5.1 report's §M/§N for the measured bottleneck
and the STOP-before-modifying-Phase-2 disclosure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from engine.data_integrity.checks import check_bars, has_failure
from engine.market_state.market_intelligence import build_snapshot
from engine.playbooks.engine import evaluate_all_playbooks
from engine.playbooks.evaluation import PlaybookEvaluation
from engine.playbooks.registry import get_definition
from engine.playbooks.taxonomy import SetupStatus

from .bar_source import DataProvenance, DataType, RunMode, require_production_safe, require_verified_adjustment
from .calendar import MARKET_TZ, session_bounds
from .contracts import HistoricalPlaybookEvaluation, PlaybookPin, ReplayRunConfig
from .resampling import resample_ohlcv

logger = logging.getLogger("trading_os.backtest.replay")

_TIMEFRAME_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1day": 1440}
DEFAULT_WARMUP_BARS = 100
_HALF_DAY_GAP_TOLERANCE_MINUTES = 30.0


class PlaybookVersionMismatchError(RuntimeError):
    """§3 — raised when a pinned historical playbook version cannot be
    safely resolved against the current engine.playbooks.registry. See
    `resolve_pinned_definition`'s docstring for the architectural gap this
    documents rather than works around."""


class ReplayDataQualityError(RuntimeError):
    """§7 — raised when input bars fail engine.data_integrity.checks.check_bars's
    hard FAILURE gate (malformed OHLC, duplicate/unsorted timestamps, ...).
    Never silently filled or interpolated."""


def resolve_pinned_definition(pin: PlaybookPin):
    """§3 — the ONLY way replay obtains a PlaybookDefinition. Fails closed
    (PlaybookVersionMismatchError) instead of silently substituting the
    registry's current version.

    ARCHITECTURAL GAP (documented here, not worked around): engine/playbooks/
    registry.py stores exactly ONE PlaybookDefinition per playbook_id — its
    `version` field is metadata on that single definition, not part of a
    lookup key (there is no `get_definition(playbook_id, version)`). There
    is currently NO way to resolve a playbook version other than whatever is
    presently registered. Building a second, parallel versioned-definition
    store inside engine/backtest/ to paper over this would itself violate
    §2's "no second implementation of playbook logic" — so this function
    instead REFUSES a mismatched pin outright. Actually closing this gap (a
    versioned definition store in engine/playbooks/registry.py) is Phase 4
    work requiring separate, explicit approval — see the Phase 5.1 report.
    """
    try:
        definition = get_definition(pin.playbook_id)
    except KeyError as exc:
        raise PlaybookVersionMismatchError(
            f"{pin.playbook_id}: not a known playbook_id in the current engine.playbooks.registry."
        ) from exc
    if not definition.implementable:
        raise PlaybookVersionMismatchError(
            f"{pin.playbook_id}: NOT_IMPLEMENTABLE_YET in the current registry "
            f"({definition.not_implementable_reason}) — cannot be replayed."
        )
    if definition.version != pin.playbook_version:
        raise PlaybookVersionMismatchError(
            f"{pin.playbook_id}: requested historical version {pin.playbook_version!r}, but the current "
            f"registry only holds version {definition.version!r}. There is no way to resolve a NON-CURRENT "
            "historical playbook version today — see resolve_pinned_definition's docstring for why this is a "
            "genuine Phase 4 architectural gap, not something Phase 5.1 silently works around."
        )
    return definition


# ---------------------------------------------------------------------------
# §4 — multi-timeframe no-look-ahead safety
# ---------------------------------------------------------------------------


def _drop_incomplete_trailing_bucket(
    resampled: pd.DataFrame, cursor_ts: pd.Timestamp, target_minutes: int, base_minutes: int,
) -> pd.DataFrame:
    """A higher-timeframe bucket is only "closed" (safe to expose) once its
    OWN last base bar is itself visible at `cursor_ts`. Used identically by
    BOTH the reference and cache-assisted paths so this safety rule can
    never diverge between them."""
    if resampled.empty:
        return resampled
    last_bucket_start = resampled.index[-1]
    last_bucket_end = last_bucket_start + pd.Timedelta(minutes=target_minutes)
    if last_bucket_end > cursor_ts + pd.Timedelta(minutes=base_minutes):
        return resampled.iloc[:-1]
    return resampled


def visible_higher_timeframe_data(
    base_df_visible: pd.DataFrame, cursor_ts: pd.Timestamp, higher_timeframes: tuple[str, ...],
    base_timeframe_minutes: int,
) -> dict[str, pd.DataFrame]:
    """§4 (REFERENCE path) — builds each higher-timeframe series using ONLY
    base bars already visible at `cursor_ts`, from scratch every call, and
    drops any still-forming trailing bucket — a partially-formed
    higher-timeframe candle's high/low/close/volume are never exposed, even
    partially. Mirrors how a real market-data provider behaves: you cannot
    request "today's still-open hourly candle," only the last CLOSED one.
    """
    result: dict[str, pd.DataFrame] = {}
    for tf in higher_timeframes:
        resampled = resample_ohlcv(base_df_visible, tf)
        result[tf] = _drop_incomplete_trailing_bucket(
            resampled, cursor_ts, _TIMEFRAME_MINUTES[tf], base_timeframe_minutes,
        )
    return result


class IncrementalResampleCache:
    """§9-B (OPTIMIZED path) — caches already-CLOSED sessions' resampled
    bars across replay steps, re-resampling only the CURRENT (still
    accumulating) session's bars at each cursor step. Bounded, roughly
    constant per-step cost (~1 session's worth of base bars) instead of
    re-resampling the ENTIRE growing history every step.

    PROVABLY equivalent to full recomputation: engine.backtest.resampling
    processes each trading session's bars entirely independently (see Phase
    5.0's test_multi_day_resample_keeps_sessions_independent) — a session's
    resampled buckets can never be affected by a LATER session's bars, so a
    session's resampled output, once computed from its own complete bar set,
    is safe to cache and reuse indefinitely. See
    tests/test_backtest_replay_optimized.py for the direct equivalence
    proof against `visible_higher_timeframe_data`.
    """

    def __init__(self) -> None:
        self._frozen: dict[str, pd.DataFrame] = {}
        self._frozen_dates: set[date] = set()

    def get(
        self, base_df_visible: pd.DataFrame, cursor_ts: pd.Timestamp, higher_timeframes: tuple[str, ...],
        base_timeframe_minutes: int,
    ) -> dict[str, pd.DataFrame]:
        # Preserve the CALLER's original tz for the returned result — matches
        # resample_ohlcv's own convention exactly (it returns output in
        # whatever tz it was given), so the reference and cache-assisted
        # paths can never diverge merely by index tz representation even
        # when the underlying instants are identical.
        original_tz = base_df_visible.index.tz
        local = (
            base_df_visible.tz_convert(MARKET_TZ) if original_tz is not None
            else base_df_visible.tz_localize(MARKET_TZ)
        )
        current_session_date = local.index[-1].date()
        local_dates = local.index.date

        prior_mask = local_dates < current_session_date
        prior_df = local.loc[prior_mask]
        current_session_df = local.loc[~prior_mask]

        prior_dates_present = set(prior_df.index.date) if not prior_df.empty else set()
        newly_closed_dates = prior_dates_present - self._frozen_dates
        if newly_closed_dates:
            newly_closed_mask = pd.Series(prior_df.index.date, index=prior_df.index).isin(newly_closed_dates)
            newly_closed_df = prior_df.loc[newly_closed_mask]
            for tf in higher_timeframes:
                resampled_new = resample_ohlcv(newly_closed_df, tf)
                self._frozen[tf] = (
                    pd.concat([self._frozen[tf], resampled_new]).sort_index()
                    if tf in self._frozen and not self._frozen[tf].empty else resampled_new
                )
            self._frozen_dates |= newly_closed_dates

        result: dict[str, pd.DataFrame] = {}
        for tf in higher_timeframes:
            current_resampled = resample_ohlcv(current_session_df, tf) if not current_session_df.empty else pd.DataFrame()
            frozen_part = self._frozen.get(tf, pd.DataFrame())
            if current_resampled.empty:
                combined = frozen_part
            elif frozen_part.empty:
                combined = current_resampled
            else:
                combined = pd.concat([frozen_part, current_resampled]).sort_index()
            complete = _drop_incomplete_trailing_bucket(
                combined, cursor_ts, _TIMEFRAME_MINUTES[tf], base_timeframe_minutes,
            )
            result[tf] = (
                complete.tz_convert(original_tz) if original_tz is not None and not complete.empty
                else complete.tz_localize(None) if original_tz is None and not complete.empty
                else complete
            )
        return result


# ---------------------------------------------------------------------------
# §8 — half-day / session-integrity fail-closed handling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionIntegrityIssue:
    session_date: date
    last_bar_local: pd.Timestamp
    minutes_before_scheduled_close: float


def check_session_integrity(
    df: pd.DataFrame, base_timeframe_minutes: int, tolerance_minutes: float = _HALF_DAY_GAP_TOLERANCE_MINUTES,
) -> list[SessionIntegrityIssue]:
    """§8 — Phase 5.0's calendar cannot confirm ANY session is a normal full
    session (`SessionBounds.is_full_session_confirmed` is always False,
    since half-days are not modeled at all). Rather than guessing WHICH
    specific days are half-days — which would risk fabricating a session
    close time this system cannot actually verify — this detects the
    observable SYMPTOM instead: a session whose last bar ends meaningfully
    before the scheduled 16:00 close. This catches genuine early closes and
    ordinary severe end-of-day data gaps alike; it does not need to (and
    cannot) tell those two apart — either way, per the fail-closed
    principle, that session must not be silently treated as a normal full
    session.
    """
    if df.empty:
        return []
    local = df.tz_convert(MARKET_TZ) if df.index.tz is not None else df.tz_localize(MARKET_TZ)
    issues: list[SessionIntegrityIssue] = []
    for session_date, day_df in local.groupby(local.index.date):
        bounds = session_bounds(session_date)
        if bounds is None:
            continue  # not even a trading day per the calendar — a separate, already-handled case
        last_bar_start = day_df.index[-1]
        last_bar_covers_until = last_bar_start + pd.Timedelta(minutes=base_timeframe_minutes)
        gap_minutes = (bounds.close_at - last_bar_covers_until).total_seconds() / 60.0
        if gap_minutes > tolerance_minutes:
            issues.append(SessionIntegrityIssue(
                session_date=session_date, last_bar_local=last_bar_start, minutes_before_scheduled_close=gap_minutes,
            ))
    return issues


def exclude_flagged_sessions(df: pd.DataFrame, issues: list[SessionIntegrityIssue]) -> pd.DataFrame:
    if not issues:
        return df
    flagged_dates = {issue.session_date for issue in issues}
    local = df.tz_convert(MARKET_TZ) if df.index.tz is not None else df.tz_localize(MARKET_TZ)
    keep_mask = ~pd.Series(local.index.date, index=df.index).isin(flagged_dates)
    return df.loc[keep_mask]


# ---------------------------------------------------------------------------
# §5/§12 — trigger-transition bookkeeping + the replay result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    config: ReplayRunConfig
    evaluations: tuple[HistoricalPlaybookEvaluation, ...]
    excluded_sessions: tuple[SessionIntegrityIssue, ...] = field(default_factory=tuple)
    data_quality_warnings: tuple[str, ...] = field(default_factory=tuple)
    bars_evaluated: int = 0


def _to_historical_evaluation(
    e: PlaybookEvaluation, *, run_id: str, symbol: str, timeframe: str, timestamp: pd.Timestamp,
    market_regime: str | None, volatility_regime: str | None, is_new_trigger_occurrence: bool,
    data_quality_status: str,
) -> HistoricalPlaybookEvaluation:
    """Reuses PlaybookEvaluation's own already-computed field values —
    never recomputes anything Phase 4 already produced."""
    return HistoricalPlaybookEvaluation(
        run_id=run_id, symbol=symbol, timeframe=timeframe, timestamp=timestamp,
        playbook_id=e.playbook_id, playbook_version=e.playbook_version, family=e.family, direction=e.direction,
        eligibility_status=e.eligibility_status, setup_status=e.setup_status,
        quality_score=e.quality_score, quality_band=e.quality_band,
        entry_price=e.entry_price, entry_zone_low=e.entry_zone_low, entry_zone_high=e.entry_zone_high,
        entry_type=e.entry_type, stop_price=e.stop_price, target1=e.target1, target2=e.target2,
        market_regime=market_regime, volatility_regime=volatility_regime, evidence=dict(e.evidence),
        is_new_trigger_occurrence=is_new_trigger_occurrence, data_quality_status=data_quality_status,
    )


def run_replay(
    bars_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    provenance: DataProvenance,
    run_mode: RunMode,
    playbook_pins: tuple[PlaybookPin, ...],
    run_id: str,
    higher_timeframes: tuple[str, ...] = (),
    min_rr: float = 1.5,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    atr_period: int = 14,
    opening_range_minutes: int = 30,
    allow_unverified_adjustment: bool = False,
    enforce_session_integrity: bool = True,
    use_resample_cache: bool = False,
    use_optimized_market_state: bool = False,
    data_source: str | None = None,
    git_commit_sha: str | None = None,
    code_version_tag: str | None = None,
) -> ReplayResult:
    """The Phase 5.1/5.1P replay entry point. `use_resample_cache=False` and
    `use_optimized_market_state=False` (both default) is the REFERENCE path
    — see the module docstring. Either flag independently set to `True`
    enables its own OPTIMIZED behavior; `use_optimized_market_state=True`
    (Phase 5.1P §Part B) calls `build_snapshot(..., use_optimized_computation=
    True)`, which uses implementation-equivalent (not approximate) faster
    paths for the sub-computations profiling identified as dominant — see
    `engine.market_state.market_intelligence.build_snapshot`'s docstring and
    tests/test_phase51p_optimized_market_state.py for the equivalence proof.
    Every other argument behaves identically regardless of either flag.
    """
    if timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    for tf in higher_timeframes:
        if tf not in _TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported higher_timeframe: {tf!r}")
        if _TIMEFRAME_MINUTES[tf] <= _TIMEFRAME_MINUTES[timeframe]:
            raise ValueError(f"higher_timeframe {tf!r} must be strictly coarser than base timeframe {timeframe!r}")
    timeframe_minutes = _TIMEFRAME_MINUTES[timeframe]

    # §7 — fail-closed provenance gates, reusing Phase 5.0 unchanged.
    require_production_safe(provenance, run_mode)
    require_verified_adjustment(provenance, allow_unverified=allow_unverified_adjustment)

    # §3 — resolve every pin BEFORE doing any work; one bad pin aborts the
    # whole run rather than silently skipping it partway through.
    for pin in playbook_pins:
        resolve_pinned_definition(pin)

    # §7 — malformed OHLC / duplicate / unsorted timestamps fail closed.
    issues = check_bars(bars_df, timeframe_minutes=timeframe_minutes)
    if has_failure(issues):
        failure_text = "; ".join(f"{i.code}: {i.message}" for i in issues if i.severity == "FAILURE")
        raise ReplayDataQualityError(f"{symbol}/{timeframe}: {failure_text}")
    data_quality_warnings = tuple(f"{i.code}: {i.message}" for i in issues if i.severity == "WARNING")

    # §8 — half-day / session-integrity fail-closed handling. Skipped for
    # synthetic data, which is not session-shaped at all (continuous bars,
    # no RTH gaps) and would otherwise flag every single day.
    excluded_sessions: tuple[SessionIntegrityIssue, ...] = ()
    working_df = bars_df
    if enforce_session_integrity and provenance.data_type == DataType.REAL_MARKET_DATA:
        session_issues = check_session_integrity(bars_df, timeframe_minutes)
        if session_issues:
            excluded_sessions = tuple(session_issues)
            working_df = exclude_flagged_sessions(bars_df, session_issues)
            logger.warning(
                "run_replay: excluded %d session(s) with uncertain session integrity (possible half-day or "
                "severe end-of-day data gap) for %s/%s: %s",
                len(session_issues), symbol, timeframe, [i.session_date for i in session_issues],
            )

    config = ReplayRunConfig(
        run_id=run_id, symbols=(symbol,), timeframe=timeframe, higher_timeframes=higher_timeframes,
        date_range_start=bars_df.index[0], date_range_end=bars_df.index[-1],
        historical_provider=provenance.provider, adjustment_status=provenance.adjustment_status.value,
        playbook_pins=playbook_pins, min_rr=min_rr, run_mode=run_mode.value,
        data_quality_policy="engine.data_integrity.checks.check_bars (Phase 1, unchanged)",
        calendar_policy="engine.backtest.calendar (NYSE/Nasdaq RTH; sessions with uncertain integrity excluded, see §8)",
        git_commit_sha=git_commit_sha, code_version_tag=code_version_tag,
    )

    if len(working_df) <= warmup_bars:
        return ReplayResult(
            config=config, evaluations=(), excluded_sessions=excluded_sessions,
            data_quality_warnings=data_quality_warnings, bars_evaluated=0,
        )

    cache = IncrementalResampleCache() if use_resample_cache else None
    evaluations: list[HistoricalPlaybookEvaluation] = []
    previous_status: dict[str, str] = {}
    bars_evaluated = 0

    for i in range(warmup_bars, len(working_df)):
        df_visible = working_df.iloc[: i + 1]
        cursor_ts = df_visible.index[-1]
        current_price = float(df_visible["close"].iloc[-1])

        if higher_timeframes:
            higher_tf_data = (
                cache.get(df_visible, cursor_ts, higher_timeframes, timeframe_minutes) if cache is not None
                else visible_higher_timeframe_data(df_visible, cursor_ts, higher_timeframes, timeframe_minutes)
            )
        else:
            higher_tf_data = {}

        snapshot = build_snapshot(
            symbol=symbol, timeframe=timeframe, df=df_visible, data_source=data_source or provenance.provider,
            timeframe_minutes=timeframe_minutes, atr_period=atr_period, opening_range_minutes=opening_range_minutes,
            higher_timeframe_data=higher_tf_data or None, now=cursor_ts,
            use_optimized_computation=use_optimized_market_state,
        )
        bars_evaluated += 1
        if not snapshot.data_quality_ok:
            # Defensive-only: given check_bars(bars_df) already passed above
            # (any FAILURE aborted the whole run before this loop started)
            # and `now` is always exactly this cursor's own last-bar
            # timestamp (zero staleness by construction — see the "now="
            # argument below), build_snapshot's OWN internal check_bars call
            # on this exact same, already-validated prefix cannot newly fail
            # here. This branch exists only as defense in depth; it emits no
            # HistoricalPlaybookEvaluation row (data_quality_status="OK" is
            # therefore the only value ever actually produced in Phase 5.1 —
            # see the Phase 5.1 report's §Q for why "DATA_QUALITY_FAILURE" is
            # reserved for a future scenario, not dead code by oversight).
            continue

        # §6 — client=None: never depend on today's SUPER_ADMIN playbook_configs
        # DB override; the run's own pins are the only source of truth.
        all_evals = evaluate_all_playbooks(snapshot, current_price, min_rr=min_rr, client=None)
        by_id = {e.playbook_id: e for e in all_evals}

        market_regime = snapshot.market_state
        volatility_regime = snapshot.volatility.level if snapshot.volatility else None

        for pin in playbook_pins:
            evaluation = by_id.get(pin.playbook_id)
            if evaluation is None:
                continue  # e.g. timeframe unsupported for this playbook — evaluate_all_playbooks already handles it
            prior = previous_status.get(pin.playbook_id)
            current = evaluation.setup_status
            if prior == current:
                continue  # §5 — no transition, no new record (TRIGGERED -> TRIGGERED never duplicated)
            is_new_trigger_occurrence = (
                prior != SetupStatus.TRIGGERED.value and current == SetupStatus.TRIGGERED.value
            )
            evaluations.append(_to_historical_evaluation(
                evaluation, run_id=run_id, symbol=symbol, timeframe=timeframe, timestamp=cursor_ts,
                market_regime=market_regime, volatility_regime=volatility_regime,
                is_new_trigger_occurrence=is_new_trigger_occurrence, data_quality_status="OK",
            ))
            previous_status[pin.playbook_id] = current

    return ReplayResult(
        config=config, evaluations=tuple(evaluations), excluded_sessions=excluded_sessions,
        data_quality_warnings=data_quality_warnings, bars_evaluated=bars_evaluated,
    )
