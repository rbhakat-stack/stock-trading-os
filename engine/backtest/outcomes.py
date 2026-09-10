"""Phase 5.2 — deterministic historical trade outcome engine.

Answers exactly one question, for ONE already-known historical trigger:
"if we had followed this historical setup using explicit execution rules,
what happened afterward?" It does NOT decide whether a setup triggers (that
is Phase 4, via engine.playbooks.engine.evaluate_all_playbooks, called
UNCHANGED here — see `_reconfirm_not_invalidated` below) and it does NOT
compute any statistic (win rate, expectancy, ...) — those are Phase 5.3.

ARCHITECTURAL INVARIANT (§1 — NO FUTURE KNOWLEDGE): every setup geometry
field (entry_price, entry_zone_low/high, stop_price, target1/2_price) comes
from the `PlaybookEvaluation` captured AT the historical trigger bar,
unchanged. This module never recomputes entry/stop/target from later bars —
it only asks "did price later trade through these ALREADY-FIXED levels, and
in what order." The one place this module inspects bars strictly after the
trigger for anything beyond price comparison is the LIMIT-entry wait window
(`_simulate_limit_entry`), where it re-invokes `build_snapshot` +
`evaluate_all_playbooks` (both UNCHANGED, exactly as Phase 5.1 does) purely
to ask Phase 4's own FSM "has this specific playbook_id's setup_status
become INVALIDATED yet" — never to re-derive a different entry/stop/target.

No Streamlit dependency. No Supabase dependency. No network calls. No
current account/position state (§22 — Phase 5 evaluates strategy behavior;
Phase 3 evaluates whether the CURRENT user can take a CURRENT trade; these
stay separate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from engine.backtest.calendar import MARKET_TZ
from engine.backtest.replay import check_session_integrity
from engine.market_state.market_intelligence import build_snapshot
from engine.playbooks.engine import evaluate_all_playbooks
from engine.playbooks.evaluation import PlaybookEvaluation
from engine.trade.candidate import EntryMethod

_TIMEFRAME_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1day": 1440}

ZERO_COST_RESEARCH_POLICY_VERSION = "ZERO_COST_RESEARCH_V1"


# ---------------------------------------------------------------------------
# §3 — versioned execution policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionPolicy:
    """The ONE place every execution assumption this engine makes is named,
    versioned, and configurable — never a scattered magic constant. Every
    simulated trade retains `version` (see `HistoricalTradeOutcome.
    execution_policy_version`) so a later policy change can never silently
    change the meaning of an already-computed historical outcome.

    This is also §21's future shared contract: a later Phase 6 live
    recommendation UI must describe "entry active / valid until / entry
    zone / stop / target" using the SAME policy object a backtest used for
    the identical playbook — not a second, independently-drifting notion of
    how long an order stays live.
    """
    version: str
    entry_activation_rule: str
    max_entry_wait_bars: int
    entry_expiry_rule: str
    same_bar_collision_rule: str
    target_policy: str
    intraday_overnight_policy: str
    daily_max_holding_bars: int
    gap_policy: str
    cost_model_version: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("ExecutionPolicy requires an explicit version")
        if self.max_entry_wait_bars <= 0:
            raise ValueError("max_entry_wait_bars must be positive")
        if self.daily_max_holding_bars <= 0:
            raise ValueError("daily_max_holding_bars must be positive")


def default_execution_policy() -> ExecutionPolicy:
    """Phase 5.2 v1 research defaults. `max_entry_wait_bars=10` and
    `daily_max_holding_bars=20` are DELIBERATE, versioned, configurable
    research defaults — NOT empirically validated strategy parameters (§13).
    No Phase 4 playbook definition encodes a machine-readable entry-validity
    window anywhere (checked: `entry_rules` are plain-English text), so per
    §5's explicit fallback order this uses ONE flat policy default rather
    than inventing a different number per playbook.
    """
    return ExecutionPolicy(
        version="v1",
        entry_activation_rule="ENTRY_ACTIVE begins immediately at the bar the playbook TRIGGERED",
        max_entry_wait_bars=10,
        entry_expiry_rule=(
            "a LIMIT/zone entry expires EXPIRED_UNFILLED after max_entry_wait_bars bars, or at the end of the "
            "current session for intraday timeframes (day-order semantics — never carried to the next session)"
        ),
        same_bar_collision_rule="ASSUME_STOP_FIRST",
        target_policy="FULL_EXIT_AT_TARGET1",
        intraday_overnight_policy="NO_OVERNIGHT_HOLD_EXIT_AT_LAST_RTH_BAR_OF_SESSION",
        daily_max_holding_bars=20,
        gap_policy=(
            "market entries fill at actual next-bar open; limit entries/targets fill at open if already "
            "marketable there, else at the limit/target price on intrabar touch; stops always exit at actual "
            "open when gapped through (losses may exceed -1R)"
        ),
        cost_model_version=ZERO_COST_RESEARCH_POLICY_VERSION,
    )


# ---------------------------------------------------------------------------
# §27 — the result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalTradeOutcome:
    occurrence_id: str
    symbol: str
    timeframe: str
    playbook_id: str
    playbook_version: str
    family: str
    direction: str
    trigger_timestamp: datetime

    entry_status: str  # FILLED | INVALIDATED_BEFORE_FILL | EXPIRED_UNFILLED | INVALID_STOP_GEOMETRY
    entry_timestamp: datetime | None
    entry_price: float | None
    entry_zone_low: float | None
    entry_zone_high: float | None

    stop_price: float | None
    target1_price: float | None
    target2_price: float | None

    exit_status: str | None  # TARGET1 | STOP | EXPIRED_EOD | TIMEOUT | END_OF_DATA | None (never filled)
    exit_timestamp: datetime | None
    exit_price: float | None

    initial_risk_per_share: float | None
    gross_R: float | None
    net_R: float | None
    mfe_R: float | None
    mae_R: float | None

    holding_period_bars: int | None
    holding_period_minutes: float | None

    same_bar_collision: bool

    market_regime_at_trigger: str | None
    volatility_regime_at_trigger: str | None
    quality_score_at_trigger: int | None

    execution_policy_version: str
    evidence: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _local_dates(bars_df: pd.DataFrame):
    idx = bars_df.index
    local = idx.tz_convert(MARKET_TZ) if idx.tz is not None else idx.tz_localize(MARKET_TZ)
    return local


def _validate_geometry(direction: str, entry_price: float | None, stop_price: float | None) -> str | None:
    if entry_price is None or stop_price is None:
        return "missing entry or stop price"
    if entry_price <= 0 or stop_price <= 0:
        return "non-positive price"
    if entry_price == stop_price:
        return "entry price equals stop price"
    if direction == "LONG" and stop_price >= entry_price:
        return "wrong-side stop: LONG stop must be below entry"
    if direction == "SHORT" and stop_price <= entry_price:
        return "wrong-side stop: SHORT stop must be above entry"
    if direction not in ("LONG", "SHORT"):
        return f"unrecognized direction: {direction!r}"
    return None


def _compute_r(direction: str, entry_price: float, exit_price: float, initial_risk: float) -> float:
    if direction == "LONG":
        return round((exit_price - entry_price) / initial_risk, 4)
    return round((entry_price - exit_price) / initial_risk, 4)


def _session_appears_complete(bars_df: pd.DataFrame, local_dates, session_date, base_timeframe_minutes: int) -> bool:
    """Reuses Phase 5.1's OWN session-integrity check (§12/26 — 'use the
    existing session/calendar safety logic', never a second tolerance)."""
    day_mask = (local_dates.date == session_date)
    day_df = bars_df.loc[day_mask]
    if day_df.empty:
        return False
    return len(check_session_integrity(day_df, base_timeframe_minutes)) == 0


def _simulate_market_entry(bars_df: pd.DataFrame, trigger_idx: int, is_intraday: bool, local_dates):
    """§4 — market/confirmation-style entries: NEVER fill at the trigger
    bar's own close. Fill at the very next executable bar's open, gap or
    not. Day-order semantics for intraday: an order never carries into the
    next session."""
    n = len(bars_df)
    if trigger_idx + 1 >= n:
        return "EXPIRED_UNFILLED", None, None, False, {"reason": "no bar exists after the trigger bar (end of data)"}
    next_idx = trigger_idx + 1
    if is_intraday and local_dates[next_idx].date() != local_dates[trigger_idx].date():
        return (
            "EXPIRED_UNFILLED", None, None, False,
            {"reason": "session ended before a next bar was available to execute a market entry (day-order semantics)"},
        )
    entry_price = float(bars_df["open"].iloc[next_idx])
    return "FILLED", next_idx, entry_price, False, {"reason": "market-on-confirmation fill at next bar open"}


def _simulate_limit_entry(
    bars_df: pd.DataFrame, trigger_idx: int, direction: str, entry_price: float, stop_price: float,
    is_intraday: bool, max_wait_bars: int, playbook_id: str, symbol: str, timeframe: str,
    atr_period: int, opening_range_minutes: int, local_dates,
):
    """§5/§6/§7/§8/§20 — zone/limit-style entries. Walks forward bar by bar
    (bounded by `max_wait_bars`, and by the current session for intraday
    day-order semantics). Each bar, in order:
      1. gap/intrabar stop-breach BEFORE fill -> INVALIDATED_BEFORE_FILL
         (conservative: if a bar would touch both the zone and the stop
         before a fill is confirmed, assume the adverse path, mirroring
         §8's stop-first doctrine).
      2. gap-through fill at open, or intrabar limit fill at the exact
         limit price (never the bar's more favorable extreme — §6).
      3. otherwise, re-run Phase 4 UNCHANGED (build_snapshot +
         evaluate_all_playbooks) to ask whether THIS playbook_id's own
         setup_status has become INVALIDATED — §20, zero duplicated logic.
    """
    n = len(bars_df)
    trigger_date = local_dates[trigger_idx].date()

    i = trigger_idx + 1
    waited = 0
    while i < n and waited < max_wait_bars:
        if is_intraday and local_dates[i].date() != trigger_date:
            break  # session ended -> falls through to EXPIRED_UNFILLED below (day-order semantics)

        bar = bars_df.iloc[i]
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])

        if direction == "LONG":
            gapped_through_stop = o <= stop_price
            traded_through_stop = l <= stop_price
            gap_fill = o <= entry_price
            intrabar_fill = l <= entry_price
        else:
            gapped_through_stop = o >= stop_price
            traded_through_stop = h >= stop_price
            gap_fill = o >= entry_price
            intrabar_fill = h >= entry_price

        if gapped_through_stop:
            return (
                "INVALIDATED_BEFORE_FILL", None, None, False,
                {"reason": "gapped through the already-computed stop before any fill", "bar_index": int(i)},
            )
        if gap_fill:
            return "FILLED", i, o, False, {"reason": "gap-through fill at open (marketable at session open)", "bar_index": int(i)}
        if traded_through_stop:
            return (
                "INVALIDATED_BEFORE_FILL", None, None, True,
                {
                    "reason": "same bar touched both the entry zone and the stop before a fill was confirmed — "
                              "conservative stop-first assumption (§8), applied to a pending entry",
                    "bar_index": int(i),
                },
            )
        if intrabar_fill:
            return "FILLED", i, entry_price, False, {"reason": "intrabar limit fill at the limit price", "bar_index": int(i)}

        # §20 — Phase-4-authoritative structural invalidation re-check, not a
        # second implementation: reuses build_snapshot + evaluate_all_playbooks
        # UNCHANGED, exactly as Phase 5.1's replay engine does.
        df_visible = bars_df.iloc[: i + 1]
        snapshot = build_snapshot(
            symbol=symbol, timeframe=timeframe, df=df_visible, data_source="phase5.2-outcome-engine",
            timeframe_minutes=_TIMEFRAME_MINUTES[timeframe], atr_period=atr_period,
            opening_range_minutes=opening_range_minutes, now=df_visible.index[-1],
        )
        if snapshot.data_quality_ok:
            current_price = float(bar["close"])
            evals = evaluate_all_playbooks(snapshot, current_price, client=None)
            match = next((e for e in evals if e.playbook_id == playbook_id), None)
            if match is not None and match.setup_status == "INVALIDATED":
                return (
                    "INVALIDATED_BEFORE_FILL", None, None, False,
                    {"reason": "Phase 4 setup_status became INVALIDATED before this entry filled", "bar_index": int(i)},
                )

        waited += 1
        i += 1

    return "EXPIRED_UNFILLED", None, None, False, {"reason": "entry validity window elapsed without a fill"}


def _walk_post_fill(
    bars_df: pd.DataFrame, entry_idx: int, entry_price: float, direction: str, stop_price: float,
    target1_price: float | None, is_intraday: bool, daily_max_holding_bars: int, timeframe_minutes: int,
    local_dates,
):
    """§9/§10/§11/§12/§13/§14/§16 — post-fill walk: gap-through checks first
    (using open), then intrabar touches, then session/holding-limit expiry.
    MFE/MAE are a running max/min computed incrementally over exactly the
    held bars — monotonic by construction (§31)."""
    n = len(bars_df)
    entry_date = local_dates[entry_idx].date()

    mfe = 0.0
    mae = 0.0
    bars_held = 0
    last_idx = entry_idx
    i = entry_idx

    while i < n:
        if is_intraday and local_dates[i].date() != entry_date:
            break

        bar = bars_df.iloc[i]
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])

        if direction == "LONG":
            favorable = max(0.0, h - entry_price)
            adverse = max(0.0, entry_price - l)
        else:
            favorable = max(0.0, entry_price - l)
            adverse = max(0.0, h - entry_price)
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        if direction == "LONG":
            gapped_stop = o <= stop_price
            gapped_target = target1_price is not None and o >= target1_price
        else:
            gapped_stop = o >= stop_price
            gapped_target = target1_price is not None and o <= target1_price

        if gapped_stop and gapped_target:
            return "STOP", i, stop_price, mfe, mae, True, last_idx
        if gapped_stop:
            return "STOP", i, o, mfe, mae, False, last_idx
        if gapped_target:
            return "TARGET1", i, o, mfe, mae, False, last_idx

        if direction == "LONG":
            touched_stop = l <= stop_price
            touched_target = target1_price is not None and h >= target1_price
        else:
            touched_stop = h >= stop_price
            touched_target = target1_price is not None and l <= target1_price

        if touched_stop and touched_target:
            return "STOP", i, stop_price, mfe, mae, True, last_idx
        if touched_stop:
            return "STOP", i, stop_price, mfe, mae, False, last_idx
        if touched_target:
            return "TARGET1", i, target1_price, mfe, mae, False, last_idx

        last_idx = i
        bars_held += 1
        if not is_intraday and bars_held >= daily_max_holding_bars:
            return "TIMEOUT", i, c, mfe, mae, False, last_idx
        i += 1

    exit_price = float(bars_df["close"].iloc[last_idx])
    if is_intraday:
        if _session_appears_complete(bars_df, local_dates, entry_date, timeframe_minutes):
            return "EXPIRED_EOD", last_idx, exit_price, mfe, mae, False, last_idx
        return "END_OF_DATA", last_idx, exit_price, mfe, mae, False, last_idx
    return "END_OF_DATA", last_idx, exit_price, mfe, mae, False, last_idx


# ---------------------------------------------------------------------------
# §28 — the pure outcome engine entry point
# ---------------------------------------------------------------------------


def simulate_trade_outcome(
    evaluation: PlaybookEvaluation,
    symbol: str,
    timeframe: str,
    trigger_timestamp: datetime,
    bars_df: pd.DataFrame,
    execution_policy: ExecutionPolicy,
    occurrence_id: str,
    market_regime_at_trigger: str | None = None,
    volatility_regime_at_trigger: str | None = None,
    atr_period: int = 14,
    opening_range_minutes: int = 30,
) -> HistoricalTradeOutcome:
    """Pure, deterministic, reproducible: no database, no Streamlit, no
    network, no current account state. `evaluation` must be the FULL Phase 4
    `PlaybookEvaluation` captured AT `trigger_timestamp` (§1 — every setup
    geometry field comes from there, never recomputed). `bars_df` must be
    the full historical bar series `trigger_timestamp` is a member of (the
    same RTH-filtered, split-adjusted series Phase 5.1P policy already
    requires for real data) — this function only ever reads bars at or
    after `trigger_timestamp`'s position; earlier bars are never touched.
    """
    if timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    is_intraday = _TIMEFRAME_MINUTES[timeframe] < _TIMEFRAME_MINUTES["1day"]

    stop_price = evaluation.invalidation.stop_price if evaluation.invalidation else None
    target1_price = evaluation.targets[0].price if evaluation.targets else None
    target2_price = evaluation.targets[1].price if len(evaluation.targets) > 1 else None

    base = dict(
        occurrence_id=occurrence_id, symbol=symbol, timeframe=timeframe,
        playbook_id=evaluation.playbook_id, playbook_version=evaluation.playbook_version,
        family=evaluation.family, direction=evaluation.direction, trigger_timestamp=trigger_timestamp,
        entry_zone_low=evaluation.entry_zone_low, entry_zone_high=evaluation.entry_zone_high,
        stop_price=stop_price, target1_price=target1_price, target2_price=target2_price,
        market_regime_at_trigger=market_regime_at_trigger, volatility_regime_at_trigger=volatility_regime_at_trigger,
        quality_score_at_trigger=evaluation.quality_score, execution_policy_version=execution_policy.version,
    )

    # §15 — fail closed for invalid geometry BEFORE attempting any simulation.
    geometry_issue = _validate_geometry(evaluation.direction, evaluation.entry_price, stop_price)
    if geometry_issue is not None:
        return HistoricalTradeOutcome(
            **base, entry_status="INVALID_STOP_GEOMETRY", entry_timestamp=None, entry_price=None,
            exit_status=None, exit_timestamp=None, exit_price=None, initial_risk_per_share=None,
            gross_R=None, net_R=None, mfe_R=None, mae_R=None, holding_period_bars=None,
            holding_period_minutes=None, same_bar_collision=False, evidence={"geometry_issue": geometry_issue},
        )

    if trigger_timestamp not in bars_df.index:
        raise ValueError(
            f"trigger_timestamp {trigger_timestamp} is not a member of bars_df's index — the caller must supply "
            "the exact historical bar series the trigger was observed in"
        )
    trigger_idx = int(bars_df.index.get_loc(trigger_timestamp))
    local_dates = _local_dates(bars_df)

    entry_method = evaluation.candidate.entry_method if evaluation.candidate is not None else None

    if entry_method == EntryMethod.MARKET_ON_CONFIRMATION.value:
        entry_status, entry_idx, entry_price, same_bar_collision, entry_evidence = _simulate_market_entry(
            bars_df, trigger_idx, is_intraday, local_dates,
        )
    elif entry_method == EntryMethod.LIMIT.value:
        if evaluation.entry_price is None:
            return HistoricalTradeOutcome(
                **base, entry_status="INVALID_STOP_GEOMETRY", entry_timestamp=None, entry_price=None,
                exit_status=None, exit_timestamp=None, exit_price=None, initial_risk_per_share=None,
                gross_R=None, net_R=None, mfe_R=None, mae_R=None, holding_period_bars=None,
                holding_period_minutes=None, same_bar_collision=False,
                evidence={"geometry_issue": "LIMIT entry_method with no entry_price captured at trigger"},
            )
        entry_status, entry_idx, entry_price, same_bar_collision, entry_evidence = _simulate_limit_entry(
            bars_df, trigger_idx, evaluation.direction, evaluation.entry_price, stop_price, is_intraday,
            execution_policy.max_entry_wait_bars, evaluation.playbook_id, symbol, timeframe, atr_period,
            opening_range_minutes, local_dates,
        )
    else:
        raise NotImplementedError(
            f"entry_method {entry_method!r} is not used by any currently-implementable playbook and is not yet "
            "supported by the Phase 5.2 outcome engine (only LIMIT and MARKET_ON_CONFIRMATION are handled) — "
            "see the Phase 5.2 report's Known Limitations rather than guessing at execution semantics."
        )

    if entry_status != "FILLED":
        return HistoricalTradeOutcome(
            **base, entry_status=entry_status, entry_timestamp=None, entry_price=None,
            exit_status=None, exit_timestamp=None, exit_price=None, initial_risk_per_share=None,
            gross_R=None, net_R=None, mfe_R=None, mae_R=None, holding_period_bars=None,
            holding_period_minutes=None, same_bar_collision=same_bar_collision, evidence=entry_evidence,
        )

    initial_risk = abs(entry_price - stop_price)
    if initial_risk <= 0:
        return HistoricalTradeOutcome(
            **base, entry_status="INVALID_STOP_GEOMETRY", entry_timestamp=bars_df.index[entry_idx],
            entry_price=entry_price, exit_status=None, exit_timestamp=None, exit_price=None,
            initial_risk_per_share=None, gross_R=None, net_R=None, mfe_R=None, mae_R=None,
            holding_period_bars=None, holding_period_minutes=None, same_bar_collision=same_bar_collision,
            evidence={**entry_evidence, "geometry_issue": "actual fill price collapsed initial risk to <= 0"},
        )

    exit_status, exit_idx, exit_price, mfe, mae, exit_same_bar_collision, _last_idx = _walk_post_fill(
        bars_df, entry_idx, entry_price, evaluation.direction, stop_price, target1_price, is_intraday,
        execution_policy.daily_max_holding_bars, _TIMEFRAME_MINUTES[timeframe], local_dates,
    )

    gross_R = _compute_r(evaluation.direction, entry_price, exit_price, initial_risk)
    net_R = gross_R if execution_policy.cost_model_version == ZERO_COST_RESEARCH_POLICY_VERSION else None
    mfe_R = round(mfe / initial_risk, 4)
    mae_R = round(mae / initial_risk, 4)

    entry_ts = bars_df.index[entry_idx]
    exit_ts = bars_df.index[exit_idx]
    holding_bars = exit_idx - entry_idx
    holding_minutes = (
        holding_bars * _TIMEFRAME_MINUTES[timeframe] if is_intraday
        else (exit_ts - entry_ts).total_seconds() / 60.0
    )

    evidence = {**entry_evidence, "exit_reason_detail": exit_status}
    if exit_status == "END_OF_DATA":
        evidence["note"] = (
            "position still open when available historical data ended; exit_price/gross_R are mark-to-last-close, "
            "not a realized exit — see Phase 5.2 report §14/§P for statistical-denominator exclusion guidance"
        )

    return HistoricalTradeOutcome(
        **base, entry_status="FILLED", entry_timestamp=entry_ts, entry_price=entry_price,
        exit_status=exit_status, exit_timestamp=exit_ts, exit_price=exit_price,
        initial_risk_per_share=round(initial_risk, 4), gross_R=gross_R, net_R=net_R, mfe_R=mfe_R, mae_R=mae_R,
        holding_period_bars=holding_bars, holding_period_minutes=holding_minutes,
        same_bar_collision=exit_same_bar_collision, evidence=evidence,
    )
