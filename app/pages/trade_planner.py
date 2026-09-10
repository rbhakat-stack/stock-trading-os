import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from auth import get_authed_client
from authorization import require_authenticated
from state_sync import sync_account_state_keys
from engine.data_provider.synthetic_provider import SyntheticProvider
from engine.market_state.market_intelligence import build_snapshot
from engine.market_state.time_of_day import MARKET_TZ
from engine.risk.account_state import AccountRiskState, validate_account_state
from engine.risk.formatting import format_drawdown_line
from engine.risk.limits import evaluate_current_policy_breaches
from engine.risk.policy import DEFAULT_RISK_POLICY, RiskPolicy, validate_risk_policy
from engine.risk.positions import (
    AccountPosition, classify_position_row, derive_exposure, derive_open_risk, effective_account_state,
    existing_signed_notional_for_symbol, existing_signed_quantity_for_symbol, normalize_position_input,
    resolve_positions_precedence,
)
from engine.playbooks.engine import detect_conflicts, evaluate_all_playbooks, rank_evaluations
from engine.trade.decision import DECISION_CONDITIONAL, DECISION_QUALIFIED, DECISION_REJECT, DECISION_WAIT
from engine.trade.planner import build_trade_plan
from repository import market_data as md_repo
from repository import positions_repository as pos_repo
from repository import risk_repository as risk_repo
from repository import watchlists as wl_repo

logger = logging.getLogger("trading_os.trade_planner")

TIMEFRAME_MINUTES = {"5min": 5, "15min": 15, "1hour": 60, "1day": 1440}
MULTI_TIMEFRAME_CANDIDATES = {
    "5min": ["15min", "1hour"], "15min": ["1hour", "1day"], "1hour": ["1day"], "1day": [],
}


def _et(ts):
    if ts is None:
        return None
    return pd.Timestamp(ts).tz_convert(MARKET_TZ)


def _format_signed_shares(qty: float) -> str:
    """Pure display formatting of an already-computed signed share count
    (PositionSizeResult.existing_shares_signed / post_trade_shares_signed)
    — never re-derives the number itself, just labels its sign."""
    if qty > 0:
        return f"{qty:g} shares LONG"
    if qty < 0:
        return f"{abs(qty):g} shares SHORT"
    return "0 shares (flat)"


def _render_playbook_evaluation_detail(e) -> None:
    """Renders the WHY THIS PLAYBOOK / PREREQUISITES / TRIGGER / DISQUALIFIERS
    / SOFT CONCERNS / QUALITY BREAKDOWN / ENTRY / INVALIDATION-STOP / TARGETS
    sections (§25) for one engine.playbooks.evaluation.PlaybookEvaluation —
    shared by the PRIMARY PLAYBOOK section and each Alternative Playbooks
    expander so the two can never drift apart in format. Display-only: reads
    the already-computed evaluation, never recomputes anything."""
    st.write(f"**Playbook status:** {e.setup_status}  ·  **Eligibility:** {e.eligibility_status}")

    if e.reasons_for:
        st.markdown("**WHY THIS PLAYBOOK**")
        for r in e.reasons_for:
            st.write(f"- {r}")

    pcol1, pcol2 = st.columns(2)
    with pcol1:
        st.markdown("**PREREQUISITES**")
        for item in e.prerequisites_satisfied:
            st.write(f"✓ {item.label} — {item.observed_value}")
        for item in e.prerequisites_missing:
            st.write(f"✗ {item.label} — expected {item.expected_value}, observed {item.observed_value}")
        if not e.prerequisites_satisfied and not e.prerequisites_missing:
            st.caption("—")

        st.markdown(f"**TRIGGER** ({'satisfied' if e.trigger_status else 'not satisfied'})")
        for item in e.trigger_conditions_satisfied:
            st.write(f"✓ {item.label} — {item.observed_value}")
        for item in e.trigger_conditions_missing:
            st.write(f"✗ {item.label} — expected {item.expected_value}, observed {item.observed_value}")
        if not e.trigger_conditions_satisfied and not e.trigger_conditions_missing:
            st.caption("—")
    with pcol2:
        st.markdown("**DISQUALIFIERS** (hard — reject this playbook)")
        if e.disqualifiers:
            for d in e.disqualifiers:
                suffix = f" ({d.evidence_value})" if d.evidence_value else ""
                st.write(f"- {d.description}{suffix}")
        else:
            st.caption("None triggered.")

        st.markdown("**SOFT CONCERNS** (lower quality, never reject)")
        if e.soft_concerns:
            for sc in e.soft_concerns:
                suffix = f" — {sc.evidence_value}" if sc.evidence_value else ""
                st.write(f"- {sc.description} (severity {sc.severity:.2f}){suffix}")
        else:
            st.caption("None.")

    if e.quality_breakdown is not None:
        st.markdown(f"**QUALITY BREAKDOWN — {e.quality_score}/100 ({e.quality_band})**")
        st.caption("Rule-based quality score — NOT a probability of profit, NOT a win rate.")
        components = e.quality_breakdown.components
        qcols = st.columns(max(len(components), 1))
        for i, (_code, label, earned, mx) in enumerate(components):
            qcols[i % len(qcols)].metric(label, f"{earned}/{mx}")

    ecol1, ecol2, ecol3 = st.columns(3)
    with ecol1:
        st.markdown("**ENTRY**")
        if e.entry_price is not None:
            st.write(f"Price: {e.entry_price:.2f}")
        if e.entry_zone_low is not None and e.entry_zone_high is not None:
            st.write(f"Zone: {e.entry_zone_low:.2f} – {e.entry_zone_high:.2f}")
        st.caption(e.entry_explanation or e.entry_type or "—")
    with ecol2:
        st.markdown("**INVALIDATION / STOP**")
        if e.stop_price is not None:
            st.write(f"Stop: {e.stop_price:.2f}")
        st.caption(e.invalidation_reason or "—")
    with ecol3:
        st.markdown("**TARGETS**")
        if e.target1 is not None:
            rr_label = f"{e.rr1:.2f}" if e.rr1 is not None else "—"
            st.write(f"T1: {e.target1:.2f} (RR {rr_label}) — {e.target1_source}")
        if e.target2 is not None:
            st.write(f"T2: {e.target2:.2f} — {e.target2_source}")
        if e.target1 is None:
            st.caption("No logical target — cannot be QUALIFIED.")

    st.caption(e.manual_review_notes)
    st.caption(f"Statistical validation: {e.statistical_validation_status}")


def _get_provider():
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        from engine.data_provider.alpaca_provider import AlpacaProvider

        return AlpacaProvider(), "alpaca"
    return SyntheticProvider(), "synthetic"


st.title("Trade Planner")
st.caption(
    "Turns market structure (Market Reader) into a risk-bounded trade PLAN — never a buy/sell instruction. "
    "Every output is REJECT, WAIT, CONDITIONAL, or QUALIFIED. NO TRADE is a frequent and expected outcome. "
    "QUALIFIED never places an order — it always requires manual review. See TRADING_OS_DESIGN.md §1."
)

user = require_authenticated()
client = get_authed_client()

# ---------------------------------------------------------------------------
# Load risk policy / account state ONCE into individual widget-bound session-state
# keys (rp_* / acct_*). Every widget below reads/writes those same keys via its
# own `key=`, so the values used for Analyze are always EXACTLY what's on screen
# — never a stale, separately-cached copy that only updates when a Save button
# is clicked. (A prior version kept a single cached RiskPolicy/AccountRiskState
# object that was only overwritten inside the Save button's on-click block;
# Analyze read that cached object directly, so an edited-but-unsaved field —
# e.g. typing a new "Max daily loss (%)" without clicking Save — was silently
# ignored by the kill-switch evaluation while still looking "entered" on
# screen. That's what let a daily-loss-limit breach evaluate against a stale
# policy value and report KILL SWITCH STATE: NORMAL. See the Phase 3
# acceptance-bug writeup for the exact reproduction.)
#
# ATOMIC / SELF-HEALING INITIALIZATION (Round L fix — see the acct_open_risk
# KeyError bug report): the guard below used to be `if "rp_..." not in
# st.session_state` / `if "acct_source" not in st.session_state`, checking
# one of the DATA fields itself, while a for-loop set the other 13-14
# fields one assignment at a time. Streamlit can interrupt a running script
# (a new rerun request arriving mid-execution raises a stop signal at an
# arbitrary point) — if that happened partway through the loop, the
# CHECKED field would already be set (so the guard would never fire again
# for the rest of the session) while later fields in the tuple (e.g.
# "open_risk", 9th of 14) were never assigned, producing a permanent
# `KeyError: 'acct_open_risk'` on every future read. The fix: guard on a
# DEDICATED marker key that is set ONLY as the very last step, after every
# field has already been written from a fully-computed local dict. If a
# rerun is interrupted at any point before the marker is set, the marker
# stays unset, so the NEXT rerun's guard is still true and the ENTIRE
# block retries from scratch (a harmless, idempotent re-assignment of the
# same values) BEFORE any other code in that rerun can possibly read an
# acct_*/rp_* key — never a partially-initialized state.
# ---------------------------------------------------------------------------
_RP_FIELDS = (
    "risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_daily_realized_loss_pct",
    "max_portfolio_heat_pct", "max_symbol_exposure_pct", "max_gross_exposure_pct", "max_net_exposure_pct",
    "max_open_positions", "max_trades_per_day", "min_rr", "max_leverage", "cooldown_after_losses",
    "max_consecutive_losses",
)
_ACCT_FIELDS = (
    "source", "net_liquidation_value", "cash", "buying_power", "realized_pnl_today", "unrealized_pnl",
    "daily_start_equity", "weekly_start_equity", "open_risk", "open_positions", "trades_today",
    "consecutive_losses", "long_exposure_notional", "short_exposure_notional",
)


def _current_risk_policy() -> RiskPolicy:
    return RiskPolicy(**{f: st.session_state[f"rp_{f}"] for f in _RP_FIELDS})


def _current_account_state() -> AccountRiskState:
    return AccountRiskState(
        **{f: st.session_state[f"acct_{f}"] for f in _ACCT_FIELDS}, as_of=datetime.now(timezone.utc),
    )


if not st.session_state.get("_risk_policy_initialized", False):
    try:
        loaded = risk_repo.get_risk_policy(client, user.id)
    except Exception:  # noqa: BLE001 - a saved-settings lookup failure must never block the page
        logger.exception("Failed to load risk policy for user %s", user.id)
        loaded = None
    _rp_values = {f: getattr(loaded or DEFAULT_RISK_POLICY, f) for f in _RP_FIELDS}  # pure computation first
    for _f, _v in _rp_values.items():
        st.session_state[f"rp_{_f}"] = _v
    st.session_state["_risk_policy_initialized"] = True  # set LAST — see the docstring above

if not st.session_state.get("_acct_state_initialized", False):
    try:
        loaded_acct = risk_repo.get_account_risk_state(client, user.id)
    except Exception:  # noqa: BLE001 - a saved-settings lookup failure must never block the page
        logger.exception("Failed to load account state for user %s", user.id)
        loaded_acct = None
    default_acct = AccountRiskState(
        source="MANUAL", net_liquidation_value=100_000.0, cash=100_000.0, buying_power=100_000.0,
        realized_pnl_today=0.0, unrealized_pnl=0.0, daily_start_equity=100_000.0, weekly_start_equity=100_000.0,
        open_risk=0.0, open_positions=0, trades_today=0, consecutive_losses=0, as_of=datetime.now(timezone.utc),
    )
    _acct_values = {f: getattr(loaded_acct or default_acct, f) for f in _ACCT_FIELDS}  # pure computation first
    st.session_state["_acct_raw_snapshot"] = _acct_values  # see state_sync.sync_account_state_keys's docstring
    for _f, _v in _acct_values.items():
        st.session_state[f"acct_{_f}"] = _v
    st.session_state["_acct_state_initialized"] = True  # set LAST — see the docstring above

# UNCONDITIONAL — runs on EVERY rerun (not gated by the one-time marker
# above), before any other code in this script reads an acct_* key. This is
# what actually closes the "acct_open_positions missing" class of bug (see
# app/state_sync.py's module docstring for the full mechanism): Streamlit
# silently removes a widget's session-state entry whenever that widget
# isn't instantiated on a given rerun (acct_open_positions/acct_open_risk/
# acct_long_exposure_notional/acct_short_exposure_notional are each only
# rendered as a widget in the "positions NOT authoritative" branch below —
# see the Account State tab). The one-time marker guard above cannot detect
# or repair that removal, since it only ever fires once per session. This
# call restores any such key from the last known value (never a hardcoded
# 0) EVERY single run, so no code past this point can ever observe one
# missing — regardless of which tab was visited, or in what order.
sync_account_state_keys(st.session_state, _ACCT_FIELDS)
# The explicit contract every direct st.session_state["acct_*"] access below
# relies on — never silently skipped; if this ever fails it means a NEW
# acct_* field was added to _ACCT_FIELDS without state_sync covering it.
assert all(f"acct_{_f}" in st.session_state for _f in _ACCT_FIELDS), (
    "sync_account_state_keys failed to guarantee every _ACCT_FIELDS key exists"
)

# ---------------------------------------------------------------------------
# Positions conversion boundary — pure helpers, no session-state access.
# ---------------------------------------------------------------------------
_POSITIONS_COLUMNS = ["symbol", "quantity_signed", "reference_price", "planned_stop_price", "average_price"]


def _positions_df_from_repo(positions: list[AccountPosition]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": p.symbol, "quantity_signed": p.quantity_signed, "reference_price": p.reference_price,
                "planned_stop_price": p.planned_stop_price, "average_price": p.average_price,
            }
            for p in positions
        ],
        columns=_POSITIONS_COLUMNS,
    )


def _normalize_editor_df(df: pd.DataFrame) -> tuple[list[AccountPosition], list[str]]:
    """The UI-side half of the conversion boundary: unwraps each st.data_editor
    row (a pandas Series of mixed Python/numpy scalar types) into plain
    values and hands them to engine.risk.positions.normalize_position_input
    — the actual validation/conversion logic lives there (pure, pandas-free,
    directly unit testable). Returns (valid_positions, error_messages);
    a row that's entirely blank contributes to neither list."""
    positions: list[AccountPosition] = []
    errors: list[str] = []
    if df is None:
        return positions, errors
    for _, row in df.iterrows():
        position, error = normalize_position_input(
            symbol_raw=row.get("symbol"), quantity_raw=row.get("quantity_signed"),
            reference_price_raw=row.get("reference_price"), planned_stop_raw=row.get("planned_stop_price"),
            average_price_raw=row.get("average_price"),
        )
        if error:
            errors.append(error)
        elif position is not None:
            positions.append(position)
    return positions, errors


def _classify_editor_df(df: pd.DataFrame) -> tuple[list[AccountPosition], list[str], list[str]]:
    """Live-editing-friendly counterpart to _normalize_editor_df (Round K UX
    fix) — separates a row still being typed into (INCOMPLETE, e.g. only
    Symbol entered so far) from a row with a genuine mistake (INVALID, e.g.
    a malformed or zero quantity), using engine.risk.positions.classify_position_row
    for the distinction. Used for LIVE display (positions_tab's inline
    messaging) and for what feeds the positions precedence resolution —
    Save itself always re-validates with the STRICT _normalize_editor_df,
    which does not (and must not) grant an incomplete row a pass at Save
    time.

    Returns (valid_positions, invalid_messages, incomplete_labels). An
    incomplete row contributes to none of the "real" lists — it is
    treated the same as an EMPTY row for every purpose except the soft
    "still being entered" UI hint.
    """
    valid_positions: list[AccountPosition] = []
    invalid_messages: list[str] = []
    incomplete_labels: list[str] = []
    if df is None:
        return valid_positions, invalid_messages, incomplete_labels
    for _, row in df.iterrows():
        symbol_raw = row.get("symbol")
        quantity_raw = row.get("quantity_signed")
        reference_price_raw = row.get("reference_price")
        planned_stop_raw = row.get("planned_stop_price")
        average_price_raw = row.get("average_price")
        state = classify_position_row(symbol_raw, quantity_raw, reference_price_raw, planned_stop_raw, average_price_raw)
        if state == "EMPTY":
            continue
        position, error = normalize_position_input(
            symbol_raw=symbol_raw, quantity_raw=quantity_raw, reference_price_raw=reference_price_raw,
            planned_stop_raw=planned_stop_raw, average_price_raw=average_price_raw,
        )
        if state == "VALID" and position is not None:
            valid_positions.append(position)
        elif state == "INVALID" and error:
            invalid_messages.append(error)
        elif state == "INCOMPLETE" and error:
            incomplete_labels.append(error.split(":", 1)[0])  # normalize_position_input's error always leads with the label
    return valid_positions, invalid_messages, incomplete_labels


# ---------------------------------------------------------------------------
# Positions session-state — ONE authoritative live editor state (Round L
# fix — see the "double entry" bug report).
#
# TWO clearly separate names, per the report's own §8 guidance:
#   st.session_state["positions_editor_seed"] — the DataFrame passed as
#     st.data_editor's `value=`. Set ONLY at session init and immediately
#     after a successful Save (both are genuine "load the known-good
#     persisted state" moments) — NEVER reassigned from the widget's own
#     output during ordinary editing reruns. Reassigning a widget's `value=`
#     from its own just-returned output on every rerun is exactly the
#     pattern that can race with the frontend's in-flight edit state and
#     cause an edit to appear "lost," requiring a second entry.
#   edited_positions_df (a plain local variable inside `with positions_tab:`
#     below, not session-state at all) — the LIVE, THIS-RERUN grid content,
#     read directly from st.data_editor's return value. This is the one
#     and only "what does the grid contain right now" answer for the
#     current script run; every consumer (live display, precedence
#     resolution, Save) uses this SAME local value, never a separately
#     re-fetched or previous-rerun snapshot.
#
# st.session_state["persisted_positions_cache"] — the last known DB state
#     (list[AccountPosition]), refreshed at the same two moments as
#     positions_editor_seed. Lets _load_persisted_positions() answer
#     instantly from memory instead of hitting Supabase on every rerun
#     where the live grid has no valid rows yet (e.g. entering the very
#     first row of a fresh account) — that redundant per-keystroke DB
#     round-trip was measurably slowing exactly the reruns most sensitive
#     to frontend/backend timing, compounding the "double entry" symptom.
#
# Both are initialized together, atomically (see the marker-key pattern
# used for rp_*/acct_* above), so neither can exist without the other.
# ---------------------------------------------------------------------------
if not st.session_state.get("_positions_state_initialized", False):
    try:
        _loaded_positions = pos_repo.list_positions(client, user.id)
    except Exception:  # noqa: BLE001 - a saved-positions lookup failure must never block the page
        logger.exception("Failed to load positions for user %s", user.id)
        _loaded_positions = []
    _positions_editor_seed = _positions_df_from_repo(_loaded_positions)  # pure computation first
    _saved_symbols_init = {p.symbol for p in _loaded_positions}
    st.session_state["positions_editor_seed"] = _positions_editor_seed
    st.session_state["persisted_positions_cache"] = _loaded_positions
    st.session_state["saved_position_symbols"] = _saved_symbols_init
    st.session_state["_positions_state_initialized"] = True  # set LAST


def _load_persisted_positions() -> list[AccountPosition]:
    """Reads the cached last-known-persisted positions — see the module
    docstring above for why this is a cache read, not a fresh DB call, on
    the hot path. Falls back to an actual (rare, safety-net) DB fetch only
    if the cache is somehow absent, which the atomic init above should
    make impossible in practice — never silently returns an empty list in
    that case, since "no positions" and "couldn't determine" must not be
    conflated (fail-closed, per §3 of the Round L report)."""
    if "persisted_positions_cache" in st.session_state:
        return st.session_state["persisted_positions_cache"]
    try:  # pragma: no cover - defensive only; atomic init above prevents this path
        return pos_repo.list_positions(client, user.id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load persisted positions for user %s", user.id)
        return []


plan_tab, policy_tab, account_tab, positions_tab, history_tab = st.tabs(
    ["Trade Plan", "Risk Policy", "Account State", "Positions", "Decision History"]
)

# ---------------------------------------------------------------------------
# Positions tab — rendered FIRST in script order (Round L fix), even though
# it appears 4th in the tab bar. `st.tabs()`'s visual left-to-right order is
# fixed entirely by the labels list passed to it above — rendering into
# `positions_tab`'s container via `with positions_tab:` earlier in the
# script does not move it in the tab bar. Rendering it first means its
# live, THIS-RERUN edited dataframe (`edited_positions_df`) is available to
# every other tab (Account State's derived display, Trade Plan's Analyze)
# with ZERO rerun lag — previously the positions-precedence resolution ran
# from a `st.session_state` snapshot taken BEFORE the data_editor widget
# below had even executed for the current rerun, i.e. one rerun stale
# relative to the actual frontend state. See the Round L bug report §6-§8.
# ---------------------------------------------------------------------------
with positions_tab:
    st.subheader("Manual / Simulated Positions")
    # A flash message surviving the st.rerun() below (a message shown, then
    # immediately followed by a rerun in the same script run, would otherwise
    # flash and vanish before the user could read it — st.session_state
    # carries it across to this next run instead).
    _flash = st.session_state.pop("positions_save_flash", None)
    if _flash:
        (st.success if _flash["ok"] else st.error)(_flash["message"])
    st.warning(
        "**MANUAL / SIMULATED DATA** — there is no live broker connection in this phase. Signed quantity: "
        "positive = LONG, negative = SHORT. Leave planned stop blank if unknown — an open position without a "
        "usable stop makes portfolio heat unreliable and BLOCKS new trades until resolved (see below)."
    )

    # `value=` is seeded ONLY from positions_editor_seed (updated only at
    # init / after a successful Save) — never from this widget's own prior
    # output. The widget's OWN internal state (tracked by Streamlit under
    # key="positions_editor") is what actually drives what's shown across
    # ordinary reruns; edited_positions_df below is simply that state,
    # freshly reconstructed for THIS rerun — the one and only live answer.
    edited_positions_df = st.data_editor(
        st.session_state["positions_editor_seed"], num_rows="dynamic", key="positions_editor", use_container_width=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "quantity_signed": st.column_config.NumberColumn("Signed Quantity", help="+ = LONG, - = SHORT"),
            "reference_price": st.column_config.NumberColumn("Reference Price ($)", min_value=0.01),
            "planned_stop_price": st.column_config.NumberColumn("Planned Stop ($, optional)"),
            "average_price": st.column_config.NumberColumn("Average Price ($, optional)"),
        },
    )
    # Deliberately NOT reassigning any session-state "seed" key from
    # edited_positions_df here — see the module-level docstring above for
    # why that reassign-every-rerun pattern is exactly what this round
    # removes. edited_positions_df (this local variable) is used directly,
    # below and after this `with` block, as the live grid state.

    # Live-editing-friendly classification (Round K fix, still preserved) —
    # a row still being typed into (INCOMPLETE) gets a soft note, never a
    # hard red error; only a row with an actual mistake (INVALID) or a row
    # already saved without a usable stop produces one.
    current_positions, invalid_messages, incomplete_labels = _classify_editor_df(edited_positions_df)
    _saved_symbols = st.session_state.get("saved_position_symbols", set())

    if current_positions:
        _derived_exposure = derive_exposure(current_positions)
        _derived_open_risk = derive_open_risk(current_positions)
        st.caption(
            f"DERIVED FROM POSITIONS: open positions={_derived_exposure.open_positions} · "
            f"long exposure=${_derived_exposure.long_exposure_notional:,.2f} · "
            f"short exposure=${_derived_exposure.short_exposure_notional:,.2f}"
        )
        if _derived_open_risk.complete:
            st.caption(f"DERIVED OPEN RISK: ${_derived_open_risk.open_risk:,.2f} (every position has a usable planned stop)")
        else:
            # A position that's already SAVED without a usable stop is a
            # confirmed, real problem — hard error, exactly as before. A
            # BRAND-NEW row that simply hasn't had its stop typed in yet is
            # not — that's normal mid-entry progress, not a mistake (§4 of
            # the Round K UX bug report). Analyze's own fail-closed
            # open_risk_complete check is computed independently from
            # _resolved_positions and is NEVER weakened by this — see
            # engine/trade/decision.py / kill_switch.py, unaffected by this
            # purely-cosmetic split.
            _saved_missing_stop = [s for s in _derived_open_risk.positions_missing_stop if s in _saved_symbols]
            _unsaved_missing_stop = [s for s in _derived_open_risk.positions_missing_stop if s not in _saved_symbols]
            if _saved_missing_stop:
                st.error(
                    f"OPEN RISK DATA INCOMPLETE — missing a usable planned stop for: "
                    f"{', '.join(_saved_missing_stop)}. New trades are blocked until every open "
                    f"position has a planned stop (a stop must be on the correct side of the reference price: below it "
                    f"for a LONG, above it for a SHORT)."
                )
            if _unsaved_missing_stop:
                st.caption(
                    f"Pending: {', '.join(_unsaved_missing_stop)} has no planned stop yet — open risk will be "
                    f"incomplete (blocking new trades) until saved with one, or you can finish entering it now."
                )
    elif not invalid_messages and not incomplete_labels:
        st.caption("No positions recorded — Account State's manual exposure/open-positions/open-risk fields are used instead.")

    if incomplete_labels:
        st.caption(f"Still being entered: {', '.join(incomplete_labels)}.")

    if invalid_messages:
        st.error("Fix the following before saving:")
        for e in invalid_messages:
            st.write(f"- {e}")

    if st.button("Save Positions", type="primary", disabled=bool(invalid_messages)):
        # Full STRICT validation, computed fresh at the moment of an actual
        # Save click — this is the "explicit commit" point (§2/§10 of the
        # Round K UX bug report), so a row that's merely INCOMPLETE (e.g.
        # only Symbol entered) is correctly treated as blocking here, with
        # its exact message shown, even though it produced no live error
        # while the user was still typing. Never persists anything the live
        # classification above would have called INCOMPLETE or INVALID.
        strict_positions, strict_errors = _normalize_editor_df(edited_positions_df)
        if strict_errors:
            st.error("Cannot save — fix the following first:")
            for e in strict_errors:
                st.write(f"- {e}")
        else:
            try:
                existing_symbols = {p.symbol for p in pos_repo.list_positions(client, user.id)}
                new_symbols = {p.symbol for p in strict_positions}
                # account_positions.symbol has a FOREIGN KEY to symbols(symbol)
                # (see supabase/migrations/0006_..._final_hardening.sql) — every
                # symbol must already exist there before it can be saved as a
                # position. Trade Plan's Analyze path already does this for its
                # OWN single symbol before logging a decision; Save Positions
                # previously never did this for ANY of its symbols, so a brand
                # new ticker typed directly into the grid (never analyzed)
                # violated the FK and failed the ENTIRE batched upsert — see
                # the Phase 3 Positions-save-lifecycle regression report for
                # the exact reproduction/traceback (account_positions_symbol_fkey).
                for _sym in new_symbols:
                    md_repo.upsert_symbol(client, _sym, name=None, exchange=None)
                # Two requests total (one batched upsert, one batched delete)
                # instead of one request per row — minimizes, though cannot
                # fully eliminate, the partial-failure window versus a true
                # single-transaction save, which the current client path
                # doesn't otherwise provide (see the final hardening report).
                pos_repo.upsert_positions(client, user.id, strict_positions)
                pos_repo.delete_positions(client, user.id, list(existing_symbols - new_symbols))
                # Reload from the DB as the new source of truth — this is
                # one of the exactly two moments positions_editor_seed /
                # persisted_positions_cache are allowed to change.
                # `st.data_editor`'s own widget state (keyed "positions_editor")
                # otherwise wins over a reassigned value= on the next rerun, so
                # that key must be cleared too, or the grid would keep showing
                # the just-submitted edits instead of what's actually persisted.
                _reloaded = pos_repo.list_positions(client, user.id)
                st.session_state["positions_editor_seed"] = _positions_df_from_repo(_reloaded)
                st.session_state["persisted_positions_cache"] = _reloaded
                st.session_state["saved_position_symbols"] = {p.symbol for p in _reloaded}
                st.session_state.pop("positions_editor", None)
                st.session_state["positions_save_flash"] = {"ok": True, "message": "Positions saved."}
                st.rerun()
            except Exception:
                logger.exception("Failed to save positions for user %s", user.id)
                # UNLIKE the success path above: NEVER touch positions_editor_seed
                # and NEVER pop the "positions_editor" widget key here. A failed
                # save means the user's in-progress edits were never confirmed —
                # the live editor (whatever is currently on screen) must survive
                # exactly as typed, both visually and for Analyze/current-policy
                # purposes (§6 of the Phase 3 Positions-save-lifecycle report).
                # The OLD behavior reset positions_editor_seed to the (empty, for
                # a first-ever save) reloaded DB state and cleared the widget's
                # own state — which is what actually produced the reported "No
                # positions recorded" message despite six populated rows still
                # being visible, and (via _positions_authoritative flipping back
                # to False) what surfaced the acct_open_positions KeyError.
                # Only the PERSISTED-side cache is refreshed here, to correctly
                # reflect a genuine partial failure (e.g. the upsert half of
                # this two-call save could succeed while the delete half then
                # fails) — never the live editor, which reflects what the user
                # is actively editing, not what's in the database. No rerun is
                # needed: nothing about the live editor changed, so the rest of
                # THIS render already reflects the correct state.
                try:
                    _reloaded = pos_repo.list_positions(client, user.id)
                    st.session_state["persisted_positions_cache"] = _reloaded
                    st.session_state["saved_position_symbols"] = {p.symbol for p in _reloaded}
                except Exception:  # noqa: BLE001 - the error message below still shows even if this reload also fails
                    logger.exception("Failed to reload positions after a failed save for user %s", user.id)
                st.error(
                    "Unable to save positions. No changes were confirmed. Your unsaved editor values are "
                    "still active in this session — fix the underlying issue and click Save Positions again."
                )

# ---------------------------------------------------------------------------
# THE single resolution of "what positions are in effect right now" for this
# rerun — computed ONCE here, immediately after positions_tab (so it uses
# the SAME-RERUN-FRESH current_positions/invalid_messages computed above,
# never a stale prior-rerun snapshot), and reused by every tab below
# (Account State display, Trade Plan Analyze) so there is exactly one
# possible answer, never a second independent computation that could
# silently disagree (see engine.risk.positions.resolve_positions_precedence
# for the precedence rules: valid live/unsaved editor edits win; a malformed
# (INVALID) live row is never partially used — it fails closed to the last
# persisted state instead; no live edits at all also falls back to
# persisted, read from persisted_positions_cache — see _load_persisted_positions).
# ---------------------------------------------------------------------------
_resolved_positions, _positions_fallback_due_to_errors = resolve_positions_precedence(
    current_positions, invalid_messages, _load_persisted_positions,
)
_positions_authoritative = bool(_resolved_positions)
if _positions_fallback_due_to_errors:
    st.warning(
        "Unsaved Positions tab edits have validation errors and were not used — using your last "
        "saved positions instead (for both Analyze and the figures shown on the Account State tab). "
        "Fix and save the Positions tab to use the edited values."
    )

# Shared "what does the account currently look like against policy" value —
# computed ONCE here (from the SAME _resolved_positions used everywhere
# else) so the Risk Policy tab's caption and the Account State tab's warning
# can never show two different effective open-position counts (Phase 3
# policy-visibility bug report §5/§9 — "do not duplicate risk calculations").
_effective_open_positions_for_display = (
    derive_exposure(_resolved_positions).open_positions if _positions_authoritative
    else st.session_state["acct_open_positions"]
)

# ---------------------------------------------------------------------------
# Risk Policy tab (§26)
# ---------------------------------------------------------------------------
with policy_tab:
    st.subheader("Risk Policy")
    st.caption(
        "All risk figures elsewhere are computed as a PERCENTAGE of your net liquidation value — never a "
        "hard-coded dollar amount. These settings are yours to configure within platform safety ceilings. "
        "Values here take effect immediately for Analyze — Save only makes them persist for next time."
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        st.number_input("Risk per trade (%)", min_value=0.01, step=0.1, key="rp_risk_per_trade_pct")
        st.number_input(
            "Max daily loss (%)", min_value=0.01, step=0.1, key="rp_max_daily_loss_pct",
            help="EQUITY DRAWDOWN limit: (NLV - start-of-day equity) / start-of-day equity. Can read 0% even after a "
            "real trading loss if offset by unrealized gains — see Max daily REALIZED loss below for that case.",
        )
        st.number_input(
            "Max daily realized loss (%)", min_value=0.01, step=0.1, key="rp_max_daily_realized_loss_pct",
            help="Realized P&L today / start-of-day equity. A SEPARATE control from Max daily loss above — this one "
            "is never masked by unrealized gains.",
        )
        st.number_input("Max weekly loss (%)", min_value=0.01, step=0.1, key="rp_max_weekly_loss_pct")
        st.number_input("Max portfolio heat (%)", min_value=0.01, step=0.1, key="rp_max_portfolio_heat_pct")
    with c2:
        st.number_input("Max single-symbol exposure (%)", min_value=0.01, step=1.0, key="rp_max_symbol_exposure_pct")
        st.number_input("Max gross exposure (%)", min_value=0.01, step=1.0, key="rp_max_gross_exposure_pct")
        st.number_input("Max net exposure (%)", min_value=0.01, step=1.0, key="rp_max_net_exposure_pct")
        st.number_input("Minimum R:R to qualify", min_value=0.01, step=0.1, key="rp_min_rr")
    with c3:
        st.number_input("Max open positions", min_value=1, step=1, key="rp_max_open_positions")
        if _effective_open_positions_for_display > st.session_state["rp_max_open_positions"]:
            st.caption(f"Current effective open positions: {_effective_open_positions_for_display} — EXCEEDS this limit.")
        elif _effective_open_positions_for_display == st.session_state["rp_max_open_positions"]:
            st.caption(f"Current effective open positions: {_effective_open_positions_for_display} — AT this limit.")
        else:
            st.caption(f"Current effective open positions: {_effective_open_positions_for_display}")
        st.number_input("Max trades per day", min_value=1, step=1, key="rp_max_trades_per_day")
        st.number_input("Max leverage", min_value=0.01, step=0.1, key="rp_max_leverage")
        st.number_input("Cooldown after N losses", min_value=0, step=1, key="rp_cooldown_after_losses")
        st.number_input("Max consecutive losses (lockout)", min_value=1, step=1, key="rp_max_consecutive_losses")

    current_policy = _current_risk_policy()

    if st.button("Save Risk Policy", type="primary"):
        errors = validate_risk_policy(current_policy)
        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                risk_repo.upsert_risk_policy(client, user.id, current_policy)
                st.success("Risk policy saved.")
            except Exception:
                logger.exception("Failed to save risk policy for user %s", user.id)
                st.warning("Unable to save risk policy. Please retry.")

# ---------------------------------------------------------------------------
# Account State tab (§25) — manual/simulated only, clearly labeled
# ---------------------------------------------------------------------------
with account_tab:
    st.subheader("Account State")
    st.warning(
        "**MANUAL / SIMULATED DATA** — there is no live broker connection in this phase. "
        "Figures below are only as accurate as what you enter here. Values take effect immediately for "
        "Analyze — Save only makes them persist for next time."
    )
    # RAW/MANUAL vs EFFECTIVE account state (root-cause fix — see the Round J
    # bug report): once positions are authoritative, _acct_tab_exposure /
    # _acct_tab_open_risk are the SAME derivation Analyze uses (via the
    # single _resolved_positions computed right after positions_tab above —
    # never a second, independently computed value). The manual acct_*
    # widgets below must never display these derived numbers while ALSO
    # being bound (via key=) to the raw manual session-state value — that
    # mismatch (disabled widget, but still showing the stale key= value)
    # was the actual Round J bug. The fix: while positions are
    # authoritative, render a disabled, keyless mirror widget showing the
    # derived value; only bind key="acct_*" (editable) when positions are
    # NOT authoritative. The raw manual acct_* session-state values are
    # NEVER overwritten by this — they remain exactly what the user last
    # entered/saved, ready to reappear the moment positions are deleted.
    _DERIVED_FIELD_CAPTION = "Derived from Positions tab — manual fallback value ignored while positions exist."
    if _positions_authoritative:
        _acct_tab_exposure = derive_exposure(_resolved_positions)
        _acct_tab_open_risk = derive_open_risk(_resolved_positions)

    a1, a2, a3 = st.columns(3)
    with a1:
        st.selectbox("Source", options=["MANUAL", "PAPER", "BROKER"], key="acct_source")
        st.number_input("Net liquidation value ($)", min_value=0.0, step=100.0, key="acct_net_liquidation_value")
        st.number_input("Cash ($)", min_value=0.0, step=100.0, key="acct_cash")
        st.number_input("Buying power ($)", min_value=0.0, step=100.0, key="acct_buying_power")
    with a2:
        st.number_input(
            "Equity at start of day ($)", min_value=0.0, step=100.0, key="acct_daily_start_equity",
            help="MANUAL / EXTERNALLY RESET — this system has no session-rollover concept. You must update this "
            "yourself at the start of each trading day; it will not reset automatically.",
        )
        st.number_input(
            "Equity at start of week ($)", min_value=0.0, step=100.0, key="acct_weekly_start_equity",
            help="MANUAL / EXTERNALLY RESET — same as above, but weekly. Not automatically reset.",
        )
        st.number_input("Realized P&L today ($)", step=10.0, key="acct_realized_pnl_today")
        st.number_input("Unrealized P&L ($)", step=10.0, key="acct_unrealized_pnl")
    with a3:
        if _positions_authoritative:
            if _acct_tab_open_risk.complete:
                st.number_input(
                    "Open risk — sum of $ planned loss to stops ($)",
                    value=round(_acct_tab_open_risk.open_risk, 2), disabled=True, format="%.2f",
                    help="DERIVED from the Positions tab's planned stops.",
                )
                st.caption(_DERIVED_FIELD_CAPTION)
            else:
                # Never substitute 0 (or any number) for unknown risk — a
                # disabled numeric field showing $0.00 would look like a
                # valid derived answer. See derive_open_risk's own docstring.
                st.text_input(
                    "Open risk — sum of $ planned loss to stops ($)", value="Unavailable — incomplete", disabled=True,
                )
                st.caption(
                    "Derived open risk unavailable — one or more positions lacks a usable planned stop. "
                    "See the Positions tab."
                )
        else:
            st.number_input(
                "Open risk — sum of $ planned loss to stops ($)", min_value=0.0, step=10.0, key="acct_open_risk",
                help="Used only when no positions are recorded on the Positions tab — once positions exist, open risk "
                "is DERIVED from their planned stops instead and this field is ignored.",
            )
        if _positions_authoritative:
            st.number_input(
                "Open positions", value=_acct_tab_exposure.open_positions, disabled=True,
                help="DERIVED from the Positions tab (count of non-zero saved/live positions).",
            )
            st.caption(_DERIVED_FIELD_CAPTION)
        else:
            st.number_input(
                "Open positions", min_value=0, step=1, key="acct_open_positions",
                help="Used only when no positions are recorded on the Positions tab — once positions exist, this count "
                "is DERIVED from them instead and this field is ignored.",
            )
        if _effective_open_positions_for_display > current_policy.max_open_positions:
            st.error(
                f"OPEN POSITION LIMIT EXCEEDED — current: {_effective_open_positions_for_display} / "
                f"limit: {current_policy.max_open_positions}. New risk-increasing positions are blocked."
            )
        elif _effective_open_positions_for_display == current_policy.max_open_positions:
            st.warning(
                f"OPEN POSITION LIMIT REACHED — current: {_effective_open_positions_for_display} / "
                f"limit: {current_policy.max_open_positions}. No additional risk-increasing position allowed."
            )
        st.number_input(
            "Trades today", min_value=0, step=1, key="acct_trades_today",
            help="MANUAL count of new trade entries opened today. Phase 3 has no execution engine, so this is "
            "never automatically incremented — you must update it yourself.",
        )
        st.number_input(
            "Consecutive losses", min_value=0, step=1, key="acct_consecutive_losses",
            help="MANUAL / EXTERNALLY RESET — not automatically tracked or reset.",
        )

    st.markdown(
        "**Exposure (fallback only)** — used ONLY when no positions are recorded on the Positions tab. Once "
        "positions exist there, long/short exposure, open positions, and (when every position has a valid planned "
        "stop) open risk are all DERIVED from them instead, and these fields are ignored."
    )
    if _positions_authoritative:
        st.info(
            f"**Positions tab has {len(_resolved_positions)} saved position(s) — Positions data is "
            f"authoritative.** The manual fields below (and Open positions / Open risk above) are fallback only "
            f"and are being IGNORED for Analyze and for the figures shown at the bottom of this tab."
        )
    e1, e2 = st.columns(2)
    with e1:
        if _positions_authoritative:
            st.number_input(
                "Long exposure notional ($)", value=round(_acct_tab_exposure.long_exposure_notional, 2),
                disabled=True, format="%.2f",
            )
            st.caption(_DERIVED_FIELD_CAPTION)
        else:
            st.number_input("Long exposure notional ($)", min_value=0.0, step=100.0, key="acct_long_exposure_notional")
    with e2:
        if _positions_authoritative:
            st.number_input(
                "Short exposure notional ($)", value=round(_acct_tab_exposure.short_exposure_notional, 2),
                disabled=True, format="%.2f",
            )
            st.caption(_DERIVED_FIELD_CAPTION)
        else:
            st.number_input("Short exposure notional ($)", min_value=0.0, step=100.0, key="acct_short_exposure_notional")

    current_account = _current_account_state()
    account_state_errors = validate_account_state(current_account)
    if account_state_errors:
        for e in account_state_errors:
            st.error(e)

    # THE single canonical overlay — the exact same effective_account_state()
    # call (same _resolved_positions input) that Trade Plan Analyze uses, so
    # this tab can never show a number that then contradicts what Analyze
    # computes (the Round J display-consistency invariant).
    display_account, _display_open_risk_complete = effective_account_state(current_account, _resolved_positions)
    if _positions_authoritative:
        st.caption("Figures below are DERIVED FROM POSITIONS (the manual fallback fields above are ignored).")

    st.caption(format_drawdown_line("Daily drawdown", display_account.current_daily_drawdown_pct, current_policy.max_daily_loss_pct))
    st.caption(format_drawdown_line("Realized loss today", display_account.realized_loss_pct, current_policy.max_daily_realized_loss_pct))
    st.caption(format_drawdown_line("Weekly drawdown", display_account.current_weekly_drawdown_pct, current_policy.max_weekly_loss_pct))
    if _positions_authoritative and not _display_open_risk_complete:
        # Never derive a heat PERCENTAGE from a substituted/incomplete open
        # risk — same fail-closed principle as the Trade Plan tab's
        # "DATA INCOMPLETE" heat display.
        st.caption(
            f"Portfolio heat: DATA INCOMPLETE (limit: {current_policy.max_portfolio_heat_pct:.3f}%) — one or "
            f"more positions lacks a usable planned stop; see the Positions tab."
        )
    else:
        st.caption(f"Portfolio heat: {display_account.portfolio_heat_pct:.3f}% (limit: {current_policy.max_portfolio_heat_pct:.3f}%)")
    st.caption(f"Gross exposure: {display_account.gross_exposure_pct:.3f}% (limit: {current_policy.max_gross_exposure_pct:.3f}%)")
    st.caption(
        f"Net exposure: {display_account.net_exposure_pct:.3f}% "
        f"({'net long' if display_account.net_exposure_notional > 0 else 'net short' if display_account.net_exposure_notional < 0 else 'flat'}, "
        f"limit: {current_policy.max_net_exposure_pct:.3f}%)"
    )
    st.caption(f"Current leverage: {display_account.current_leverage:.3f}x (limit: {current_policy.max_leverage:.3f}x)")

    # Extended current-policy-breach visibility (§10 of the Phase 3
    # policy-visibility report) — reuses the exact same values already
    # computed above for this tab, via the one canonical, enforcement-free
    # evaluate_current_policy_breaches function. MAX_POSITIONS_REACHED is
    # excluded here since it's already shown, with REACHED/EXCEEDED wording
    # tailored to the field above, right next to Open positions.
    _account_breaches = [
        b for b in evaluate_current_policy_breaches(
            display_account, current_policy, open_risk_complete=_display_open_risk_complete, positions=_resolved_positions,
        )
        if b.code != "MAX_POSITIONS_REACHED"
    ]
    if _account_breaches:
        st.markdown("**OTHER CURRENT POLICY BREACHES:**")
        for _b in _account_breaches:
            st.error(_b.message)

    if st.button("Save Account State", type="primary"):
        try:
            risk_repo.upsert_account_risk_state(client, user.id, current_account)
            st.success("Account state saved.")
        except Exception:
            logger.exception("Failed to save account state for user %s", user.id)
            st.warning("Unable to save account state. Please retry.")

# ---------------------------------------------------------------------------
# Trade Plan tab
# ---------------------------------------------------------------------------
with plan_tab:
    watchlist = wl_repo.get_or_create_default_watchlist(client, user.id)
    symbols = wl_repo.list_watchlist_items(client, watchlist["id"])
    if not symbols:
        md_repo.upsert_symbol(client, "SPY", name="SPDR S&P 500 ETF Trust", exchange="ARCA", asset_type="ETF")
        wl_repo.add_watchlist_item(client, watchlist["id"], "SPY")
        symbols = ["SPY"]

    row1 = st.columns([2, 1, 1, 1])
    with row1[0]:
        symbol = st.selectbox("Symbol", options=symbols, index=0, key="tp_symbol")
    with row1[1]:
        timeframe = st.selectbox("Timeframe", options=list(TIMEFRAME_MINUTES), index=0, key="tp_timeframe")
    with row1[2]:
        lookback_days = st.number_input("Lookback (days)", min_value=1, max_value=90, value=10, key="tp_lookback")
    with row1[3]:
        analyze = st.button("Analyze", type="primary")

    provider, provider_name = _get_provider()
    data_source_label = "SYNTHETIC DEMO" if provider_name == "synthetic" else "REAL MARKET DATA (Alpaca)"
    st.caption(f"**DATA SOURCE:** {data_source_label}")

    if analyze:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(lookback_days))
        with st.spinner("Fetching bars and building trade plan..."):
            df = provider.get_ohlcv(symbol, timeframe, start, end)
            higher_tf_data = {}
            for tf in MULTI_TIMEFRAME_CANDIDATES.get(timeframe, []):
                try:
                    higher_tf_data[tf] = provider.get_ohlcv(symbol, tf, start, end)
                except Exception:  # noqa: BLE001 - multi-timeframe context is enrichment, never fatal
                    continue

            market_snapshot = build_snapshot(
                symbol=symbol, timeframe=timeframe, df=df, data_source=provider_name,
                timeframe_minutes=TIMEFRAME_MINUTES[timeframe], atr_period=14, opening_range_minutes=30,
                higher_timeframe_data=higher_tf_data or None, now=pd.Timestamp(end),
            )
            current_price = float(df["close"].iloc[-1]) if len(df) else 0.0

            # Positions are authoritative for exposure/open-positions/open-risk
            # once any are recorded — never silently blend the manual fallback
            # fields with derived ones (§6 of the final hardening audit). Uses
            # the SAME _resolved_positions computed once right after
            # positions_tab, and the SAME effective_account_state() overlay
            # Account State's display uses — never a second, possibly-divergent
            # resolution (see the display-consistency invariant tests).
            base_account = _current_account_state()
            live_positions = _resolved_positions
            effective_account, open_risk_complete = effective_account_state(base_account, live_positions)
            if live_positions:
                existing_symbol_notional_signed = existing_signed_notional_for_symbol(live_positions, symbol)
                existing_symbol_shares_signed = existing_signed_quantity_for_symbol(live_positions, symbol)
            else:
                existing_symbol_notional_signed = 0.0
                existing_symbol_shares_signed = 0.0

            _risk_policy_for_analysis = _current_risk_policy()
            plan = build_trade_plan(
                market_snapshot, current_price=current_price,
                risk_policy=_risk_policy_for_analysis, account=effective_account,
                existing_symbol_notional_signed=existing_symbol_notional_signed,
                existing_symbol_shares_signed=existing_symbol_shares_signed,
                open_risk_complete=open_risk_complete,
            )

            # Phase 4 (§16/§43): evaluate every enabled playbook against this
            # SAME already-built market_snapshot — never a second market-data
            # fetch or a second snapshot build per playbook. Purely a
            # display/explainability layer: it is never read by build_trade_plan
            # above and never changes plan/decision, which are fully computed
            # using ONLY the Phase 3 engine (see engine/playbooks/__init__.py's
            # architectural boundary note).
            playbook_evaluations = evaluate_all_playbooks(
                market_snapshot, current_price, min_rr=_risk_policy_for_analysis.min_rr, client=client,
            )
            # The PRIMARY PLAYBOOK is whichever evaluation's playbook_id
            # matches plan.candidate.setup_type (Phase-3-computed, unchanged)
            # — never independently selected — so the UI can never show a
            # "primary" playbook that disagrees with what the actual Phase 3
            # decision was computed for.
            primary_playbook_eval = None
            if plan.candidate is not None:
                primary_playbook_eval = next(
                    (e for e in playbook_evaluations if e.playbook_id == plan.candidate.setup_type), None,
                )
            playbook_conflict = detect_conflicts(playbook_evaluations)

        try:
            md_repo.upsert_symbol(client, symbol, name=None, exchange=None)
            risk_repo.insert_trade_decision(
                client, user.id, symbol, timeframe, plan,
                playbook_id=primary_playbook_eval.playbook_id if primary_playbook_eval else None,
                playbook_version=primary_playbook_eval.playbook_version if primary_playbook_eval else None,
                playbook_family=primary_playbook_eval.family if primary_playbook_eval else None,
            )
            logged = True
        except Exception:  # noqa: BLE001 - never show a raw traceback; the plan itself already computed fine
            logger.exception("Failed to log trade decision for %s/%s", symbol, timeframe)
            logged = False

        st.session_state["trade_plan"] = {
            "plan": plan, "symbol": symbol, "timeframe": timeframe, "logged": logged,
            "playbook_evaluations": playbook_evaluations, "primary_playbook_eval": primary_playbook_eval,
            "playbook_conflict": playbook_conflict,
        }

    state = st.session_state.get("trade_plan")
    if not state or state["symbol"] != symbol or state["timeframe"] != timeframe:
        st.info("Click **Analyze** to build a trade plan for this symbol/timeframe.")
        st.stop()

    plan = state["plan"]
    playbook_evaluations = state.get("playbook_evaluations") or []
    primary_playbook_eval = state.get("primary_playbook_eval")
    playbook_conflict = state.get("playbook_conflict")
    if not state["logged"]:
        st.warning("This plan could not be logged to your decision history. The analysis below is still valid.")

    st.caption(f"**AS OF:** {_et(plan.as_of)} ET" if plan.as_of else "**AS OF:** —")

    decision = plan.decision.decision
    if decision == DECISION_REJECT:
        st.error(f"**DECISION: NO TRADE (REJECT)**  \nReasons: {', '.join(plan.decision.no_trade_reasons) or '—'}")
    elif decision == DECISION_WAIT:
        st.info("**DECISION: WAIT** — a setup is forming but has not triggered yet.")
    elif decision == DECISION_CONDITIONAL:
        st.warning("**DECISION: CONDITIONAL** — a valid setup exists, but with soft concerns. Review carefully.")
    elif decision == DECISION_QUALIFIED:
        st.success("**DECISION: QUALIFIED**")
        st.error("**MANUAL REVIEW REQUIRED — this system never places or recommends placing an order.**")

    if playbook_conflict is not None:
        st.warning(
            f"**PLAYBOOK CONFLICT** — {playbook_conflict.reason} "
            f"LONG: {playbook_conflict.long_candidate.name} (TRIGGERED, quality "
            f"{playbook_conflict.long_candidate.quality_score}/100). "
            f"SHORT: {playbook_conflict.short_candidate.name} (TRIGGERED, quality "
            f"{playbook_conflict.short_candidate.quality_score}/100). "
            f"Neither side has been silently discarded — review both before proceeding."
        )

    if plan.candidate is not None:
        c = plan.candidate
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**SETUP:** {c.setup_type.replace('_', ' ')}")
            st.write(f"**DIRECTION:** {c.direction}")
            st.write(f"**STATUS:** {c.status}")
            if c.entry_zone_low is not None and c.entry_zone_high is not None:
                st.write(f"**ENTRY ZONE:** {c.entry_zone_low:.2f} – {c.entry_zone_high:.2f} ({c.entry_method})")
        with col2:
            if plan.invalidation:
                st.write(f"**STOP (structural invalidation):** {plan.invalidation.stop_price:.2f}")
                st.caption(plan.invalidation.reason)
            if plan.targets:
                for i, t in enumerate(plan.targets, start=1):
                    st.write(f"**TARGET {i}:** {t.price:.2f}  (R-multiple: {t.r_multiple:.2f}, {t.reason})")

        if c.reasons_for:
            st.write("**REASONS FOR:**")
            for r in c.reasons_for:
                st.write(f"- {r}")
        if c.reasons_against:
            st.write("**REASONS AGAINST / SOFT CONCERNS:**")
            for r in c.reasons_against:
                st.write(f"- {r}")
        if c.conditions_to_wait_for:
            st.write("**CONDITIONS TO WAIT FOR:**")
            for r in c.conditions_to_wait_for:
                st.write(f"- {r}")

    # ---------------------------------------------------------------------
    # Phase 4 (§25-27) — PRIMARY PLAYBOOK + Alternative Playbooks. Purely a
    # display layer over playbook_evaluations, already computed once at
    # Analyze time (see the analyze block above) — nothing here recomputes
    # anything or feeds back into plan/decision above.
    # ---------------------------------------------------------------------
    if primary_playbook_eval is not None:
        st.subheader(
            f"PRIMARY PLAYBOOK: {primary_playbook_eval.name} "
            f"(v{primary_playbook_eval.playbook_version} · {primary_playbook_eval.family.replace('_', ' ')})"
        )
        # §26 — the playbook's own lifecycle status is a SEPARATE concept
        # from the Trade Decision above; a TRIGGERED playbook can still be
        # REJECTed by a Phase 3 account/risk gate (e.g. MAX_POSITIONS_REACHED)
        # — both must remain simultaneously visible, never conflated.
        st.caption(
            f"Playbook status **{primary_playbook_eval.setup_status}** is separate from Trade Decision "
            f"**{decision}** above — e.g. a TRIGGERED playbook can still be REJECTed by an account/risk gate; "
            "that is expected, not a bug."
        )
        _render_playbook_evaluation_detail(primary_playbook_eval)
    elif plan.candidate is not None:
        st.caption("No matching Phase 4 playbook evaluation was found for this setup type.")

    st.subheader("Alternative Playbooks")
    if playbook_evaluations:
        alternatives = [
            e for e in rank_evaluations(playbook_evaluations)
            if primary_playbook_eval is None or e.playbook_id != primary_playbook_eval.playbook_id
        ]
        st.caption(f"{len(alternatives)} other playbook(s) evaluated against this same snapshot.")
        for e in alternatives:
            quality_suffix = f" · quality {e.quality_score}/100" if e.quality_score is not None else ""
            with st.expander(f"{e.name} — {e.direction} · {e.setup_status}{quality_suffix}"):
                _render_playbook_evaluation_detail(e)
    else:
        st.caption("No playbook evaluations available for this analysis.")

    if plan.quality is not None:
        st.subheader("Trade Quality Score")
        st.write(f"**SCORE:** {plan.quality.score}/100 ({plan.quality.band})")
        st.caption(plan.quality.disclaimer)
        with st.expander("Score components"):
            st.json(plan.quality.components)

    if plan.decision.evidence.get("validation_errors"):
        st.error("**INVALID CONFIGURATION — fix before analysis can proceed:**")
        for e in plan.decision.evidence["validation_errors"]:
            st.write(f"- {e}")

    classification = plan.decision.evidence.get("trade_risk_classification")
    if classification:
        st.caption(f"Trade classification: **{classification.replace('_', ' ')}**")

    if plan.decision.position_size is not None:
        ps = plan.decision.position_size
        st.subheader("Position Sizing")
        pcol1, pcol2, pcol3, pcol4 = st.columns(4)
        # "Additional" — not the total resulting position — since an
        # existing position in this symbol may already exist; see EXISTING /
        # POST-TRADE POSITION below for the actual signed totals.
        pcol1.metric("Additional shares", ps.shares)
        pcol2.metric("Position value", f"${ps.position_value:,.2f}")
        pcol3.metric("Capital at risk", f"${ps.capital_at_risk:,.2f}")
        pcol4.metric("Binding constraint", ps.binding_constraint.replace("_", " "))

        if ps.existing_shares_signed != 0:
            excol1, excol2 = st.columns(2)
            excol1.metric("Existing position", _format_signed_shares(ps.existing_shares_signed))
            excol2.metric("Post-trade position", _format_signed_shares(ps.post_trade_shares_signed))

        # WHAT/WHY/EVIDENCE explainability (§25) for the constraint that
        # actually determined the final size — every other candidate's
        # remaining capacity is shown too, so it's clear why THIS one won.
        with st.expander(f"Why {ps.shares} additional shares? — binding constraint: {ps.binding_constraint.replace('_', ' ')}"):
            candidates_shares = ps.evidence.get("candidates_shares", {})
            st.write(f"**WHAT:** position size was capped at {ps.shares} additional shares by `{ps.binding_constraint}`.")
            st.write("**EVIDENCE:** maximum additional shares allowed by each independent constraint:")
            st.json(candidates_shares)

            # Symbol-exposure-specific reconciliation (§ UI clarity round) —
            # only when THAT constraint is actually what bound, using the
            # exact numbers compute_position_size itself produced (never
            # re-derived here) — see engine/risk/sizing.py::PositionSizeResult.
            if ps.binding_constraint == "symbol_exposure":
                st.write(f"**WHY {plan.symbol} EXPOSURE BOUND THIS TRADE:**")
                st.write(f"- Existing {plan.symbol} exposure: ${ps.existing_symbol_exposure_value:,.2f}")
                st.write(f"- Maximum allowed: ${ps.max_symbol_exposure_value:,.2f}")
                st.write(f"- Remaining capacity: ${ps.symbol_exposure_remaining_value:,.2f}")
                st.write(f"- Unrestricted proposed size (risk-per-trade budget alone): ${ps.unrestricted_risk_based_value:,.2f}")
                st.write(f"- Final allowed additional position: ${ps.position_value:,.2f}")
                st.write(f"- Additional shares allowed: {ps.shares}")
                st.write(f"- Post-trade {plan.symbol} exposure: ${ps.post_trade_symbol_exposure_value:,.2f}")
                st.write(f"- Post-trade {plan.symbol} exposure %: {ps.post_trade_symbol_exposure_pct:.3f}%")
                st.write(f"- Limit: {plan.risk_policy.max_symbol_exposure_pct:.3f}%")

    st.subheader("Risk & Kill Switch")
    kcol1, kcol2 = st.columns(2)
    with kcol1:
        if plan.kill_switch.state == "NORMAL":
            st.write(f"**KILL SWITCH STATE:** {plan.kill_switch.state}")
        else:
            st.error(f"**KILL SWITCH STATE:** {plan.kill_switch.state}")
        if plan.kill_switch.triggers:
            st.write(f"**TRIGGERS:** {', '.join(plan.kill_switch.triggers)}")
    with kcol2:
        acct_now = plan.account
        open_risk_incomplete = plan.kill_switch.evidence.get("open_risk_complete") is False
        current_heat_display = "DATA INCOMPLETE" if open_risk_incomplete else f"{acct_now.portfolio_heat_pct:.3f}%"
        st.write(f"**CURRENT PORTFOLIO HEAT:** {current_heat_display} (limit: {plan.risk_policy.max_portfolio_heat_pct:.3f}%)")
        if plan.decision.position_size is not None:
            post_trade_heat_display = (
                "DATA INCOMPLETE" if open_risk_incomplete
                else f"{plan.decision.position_size.post_trade_portfolio_heat_pct:.3f}%"
            )
            st.write(f"**POST-TRADE PORTFOLIO HEAT:** {post_trade_heat_display} (limit: {plan.risk_policy.max_portfolio_heat_pct:.3f}%)")
        st.write(f"**{format_drawdown_line('DAILY DRAWDOWN', acct_now.current_daily_drawdown_pct, plan.risk_policy.max_daily_loss_pct)}**")
        st.write(f"**{format_drawdown_line('REALIZED LOSS TODAY', acct_now.realized_loss_pct, plan.risk_policy.max_daily_realized_loss_pct)}**")
        st.write(f"**{format_drawdown_line('WEEKLY DRAWDOWN', acct_now.current_weekly_drawdown_pct, plan.risk_policy.max_weekly_loss_pct)}**")
        st.write(f"**GROSS EXPOSURE:** {acct_now.gross_exposure_pct:.3f}% (limit: {plan.risk_policy.max_gross_exposure_pct:.3f}%)")
        st.write(f"**NET EXPOSURE:** {acct_now.net_exposure_pct:.3f}% (limit: {plan.risk_policy.max_net_exposure_pct:.3f}%)")
        st.write(f"**LEVERAGE:** {acct_now.current_leverage:.3f}x (limit: {plan.risk_policy.max_leverage:.3f}x)")

    # CURRENT ACCOUNT POLICY BREACH visibility (Phase 3 policy-visibility
    # bug report) — deliberately SEPARATE from KILL SWITCH STATE above: a
    # breach of an operational, trade-entry-count gate like max_open_positions
    # is not a kill-switch trigger at all (see engine/risk/kill_switch.py —
    # it never references max_open_positions/max_trades_per_day), so forcing
    # KILL SWITCH STATE away from NORMAL for it would misrepresent the
    # architecture. This reflects CURRENT account state against policy —
    # shown regardless of DECISION (REJECT/WAIT/CONDITIONAL/QUALIFIED),
    # since e.g. a FORMING candidate returns WAIT before the decision engine
    # ever reaches its own max-positions check, but the account may already
    # be in breach independent of that candidate.
    _tp_breaches = evaluate_current_policy_breaches(
        plan.account, plan.risk_policy,
        open_risk_complete=plan.kill_switch.evidence.get("open_risk_complete", True),
        # SAME _resolved_positions used everywhere else on this page (Account
        # State, Analyze) — never a separately fetched/recomputed list. See
        # engine/risk/limits.py::evaluate_current_policy_breaches's docstring.
        positions=_resolved_positions,
    )
    if _tp_breaches:
        st.markdown(
            "**CURRENT ACCOUNT POLICY BREACHES** — the account's standing state right now, independent of "
            "Kill Switch State above and of this candidate's own decision:"
        )
        for _b in _tp_breaches:
            st.error(_b.message)

    if plan.kill_switch.evidence.get("open_risk_complete") is False:
        st.error(
            "Portfolio heat could not be reliably computed — one or more open positions is missing a usable "
            "planned stop (see the Positions tab). New trades are blocked until this is resolved."
        )

    if "SYMBOL_EXPOSURE_EXCEEDED" in plan.decision.no_trade_reasons:
        st.warning(
            f"This trade would push exposure in {plan.symbol} beyond the configured maximum single-symbol "
            f"exposure of {plan.risk_policy.max_symbol_exposure_pct:.3f}%. Position size floored to 0."
        )
    if "NET_EXPOSURE_EXCEEDED" in plan.decision.no_trade_reasons:
        st.warning(
            f"This trade would push net exposure beyond the configured maximum of "
            f"{plan.risk_policy.max_net_exposure_pct:.3f}%. Position size floored to 0."
        )
    if "LEVERAGE_LIMIT_EXCEEDED" in plan.decision.no_trade_reasons:
        st.warning(
            f"This trade would push leverage beyond the configured maximum of "
            f"{plan.risk_policy.max_leverage:.3f}x. Position size floored to 0."
        )
    if "INSUFFICIENT_BUYING_POWER" in plan.decision.no_trade_reasons:
        st.warning("Buying power is insufficient to open even 1 share of this trade.")
    if "RISK_BUDGET_EXCEEDED" in plan.decision.no_trade_reasons:
        st.warning("The stop is too far from entry to fit even 1 share within the risk-per-trade budget.")

    # Never rely on the user inferring a limit breach from the drawdown number
    # alone — spell out exactly which threshold was crossed and by how much.
    # 3-decimal precision here matters: a 2-decimal display can round a value
    # still inside the limit (e.g. -0.999%) to something that visually looks
    # identical to a breach (-1.00%) — see the Phase 3 UI-precision bug report.
    if "DAILY_LOSS_LIMIT_REACHED" in plan.kill_switch.triggers:
        st.warning(
            f"Daily drawdown {plan.account.current_daily_drawdown_pct:.3f}% exceeds the configured maximum "
            f"daily loss of {plan.risk_policy.max_daily_loss_pct:.3f}%. New trades are blocked."
        )
    if "DAILY_REALIZED_LOSS_LIMIT_REACHED" in plan.kill_switch.triggers:
        st.warning(
            f"Realized loss today {plan.account.realized_loss_pct:.3f}% exceeds the configured maximum daily "
            f"realized loss of {plan.risk_policy.max_daily_realized_loss_pct:.3f}%. New trades are blocked "
            f"regardless of unrealized gains."
        )
    if "WEEKLY_LOSS_LIMIT_REACHED" in plan.kill_switch.triggers:
        st.warning(
            f"Weekly drawdown {plan.account.current_weekly_drawdown_pct:.3f}% exceeds the configured maximum "
            f"weekly loss of {plan.risk_policy.max_weekly_loss_pct:.3f}%. New trades are blocked."
        )

    st.caption(
        f"Statistical validation: {plan.statistical_validation} · Event risk: {plan.event_risk} · "
        f"Liquidity: {plan.liquidity}"
    )

    with st.expander("Full evidence"):
        st.json(plan.evidence)

# ---------------------------------------------------------------------------
# Decision History tab
# ---------------------------------------------------------------------------
with history_tab:
    st.subheader("Decision History")
    try:
        rows = risk_repo.list_trade_decisions(client, user.id, limit=50)
    except Exception:
        logger.exception("Failed to load trade decision history for user %s", user.id)
        rows = None

    if rows is None:
        st.warning("Unable to load decision history. Please retry.")
    elif not rows:
        st.info("No trade plans analyzed yet.")
    else:
        hist_df = pd.DataFrame(
            [
                {
                    "created_at": r.get("created_at"), "symbol": r.get("symbol"), "timeframe": r.get("timeframe"),
                    "setup_type": r.get("setup_type"), "direction": r.get("direction"), "decision": r.get("decision"),
                    "quality_score": r.get("quality_score"), "quality_band": r.get("quality_band"),
                    "rr1": r.get("rr1"), "position_size": r.get("position_size"),
                }
                for r in rows
            ]
        )
        st.dataframe(hist_df, use_container_width=True)
