"""Per-symbol position model (§2-3, §6-8, §12-13, §20 of the final Phase 3
hardening audit).

`AccountPosition` is the authoritative source of same-symbol and aggregate
exposure WHEN position data is supplied — see `derive_exposure` and
`derive_open_risk` below, and the module docstring in engine/risk/sizing.py
for how signed post-trade arithmetic uses this.

Signed quantity convention: positive = LONG, negative = SHORT, 0 = flat
(closed). Never store separate "side" + "unsigned quantity" fields — a signed
number is the single source of truth and cannot itself become internally
contradictory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .account_state import AccountRiskState


@dataclass(frozen=True)
class AccountPosition:
    symbol: str
    quantity_signed: float  # + = LONG, - = SHORT, 0 = flat
    reference_price: float  # current/last-known price used to mark the position
    updated_at: datetime
    average_price: float | None = None  # optional — not used in any risk math today
    planned_stop_price: float | None = None  # None = no stop recorded for this position

    @property
    def notional_signed(self) -> float:
        return self.quantity_signed * self.reference_price

    @property
    def notional_abs(self) -> float:
        return abs(self.notional_signed)

    @property
    def is_long(self) -> bool:
        return self.quantity_signed > 0

    @property
    def is_short(self) -> bool:
        return self.quantity_signed < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity_signed == 0

    @property
    def has_valid_stop(self) -> bool:
        """A stop is only usable for risk math if it's a finite, structurally
        sane number on the correct side of the reference price for this
        position's direction — never assumed present, never assumed 0."""
        if self.planned_stop_price is None or not math.isfinite(self.planned_stop_price):
            return False
        if self.is_long:
            return self.planned_stop_price < self.reference_price
        if self.is_short:
            return self.planned_stop_price > self.reference_price
        return False  # a flat position has no meaningful stop

    @property
    def planned_loss_to_stop(self) -> float | None:
        """None (not 0) when the stop is missing/invalid — callers must never
        substitute 0 for "unknown risk"; see derive_open_risk."""
        if self.is_flat:
            return 0.0
        if not self.has_valid_stop:
            return None
        if self.is_long:
            return (self.reference_price - self.planned_stop_price) * self.quantity_signed
        return (self.planned_stop_price - self.reference_price) * abs(self.quantity_signed)


@dataclass(frozen=True)
class DerivedExposure:
    long_exposure_notional: float
    short_exposure_notional: float
    open_positions: int  # count of symbols with quantity_signed != 0


def derive_exposure(positions: list[AccountPosition]) -> DerivedExposure:
    # sum()'s default start is int 0 — with no LONG (or no SHORT) positions,
    # that would silently return an int where the dataclass field is typed
    # float (numerically identical, 0 == 0.0, but the wrong Python type —
    # e.g. it trips Streamlit's number_input format-string type warning).
    # An explicit float start guarantees the correct type unconditionally.
    long_total = sum((p.notional_abs for p in positions if p.is_long), 0.0)
    short_total = sum((p.notional_abs for p in positions if p.is_short), 0.0)
    open_count = sum(1 for p in positions if not p.is_flat)
    return DerivedExposure(long_exposure_notional=long_total, short_exposure_notional=short_total, open_positions=open_count)


@dataclass(frozen=True)
class DerivedOpenRisk:
    open_risk: float  # sum of planned loss to stop across positions WITH a valid stop
    complete: bool  # False if any non-flat position lacks a valid stop
    positions_missing_stop: list[str]  # symbols missing a usable stop, for explainability


def derive_open_risk(positions: list[AccountPosition]) -> DerivedOpenRisk:
    """Never assumes missing-stop risk = 0. A position without a usable stop
    makes the WHOLE aggregate incomplete — see NoTradeReason.OPEN_RISK_DATA_INCOMPLETE
    and the kill-switch fail-closed handling in engine/risk/kill_switch.py."""
    total = 0.0
    missing: list[str] = []
    for p in positions:
        if p.is_flat:
            continue
        loss = p.planned_loss_to_stop
        if loss is None:
            missing.append(p.symbol)
        else:
            total += max(0.0, loss)  # a position already past its stop still counts as its full planned loss, never negative
    return DerivedOpenRisk(open_risk=total, complete=(len(missing) == 0), positions_missing_stop=missing)


def existing_signed_notional_for_symbol(positions: list[AccountPosition], symbol: str) -> float:
    for p in positions:
        if p.symbol == symbol:
            return p.notional_signed
    return 0.0


def existing_signed_quantity_for_symbol(positions: list[AccountPosition], symbol: str) -> float:
    """The existing SIGNED SHARE COUNT for a symbol (+ = LONG, - = SHORT, 0 =
    none) — distinct from existing_signed_notional_for_symbol's dollar
    figure, which is computed against the position's own stored
    reference_price and cannot be safely divided by a different (e.g.
    freshly analyzed) entry price to recover a share count. Used only for
    display (e.g. "existing 80 / post-trade 100 shares"), never for any
    risk-limiting arithmetic — see PositionSizeResult.post_trade_shares_signed."""
    for p in positions:
        if p.symbol == symbol:
            return p.quantity_signed
    return 0.0


RISK_INCREASING = "RISK_INCREASING"
RISK_REDUCING = "RISK_REDUCING"


def classify_trade_risk(existing_qty_signed: float, direction: str, proposed_shares: float) -> str:
    """RISK_REDUCING only when the trade strictly shrinks this symbol's
    position magnitude (including a full close to exactly flat) — anything
    else (opening new, adding same-direction, an exact-size flip, or a
    cross-zero overshoot into a larger opposite position) is RISK_INCREASING.
    Deliberately conservative: ambiguous cases classify as increasing, never
    reducing, so a risk limit can never be bypassed by mislabeling a trade.
    """
    if existing_qty_signed == 0 or proposed_shares <= 0:
        return RISK_INCREASING
    proposed_signed = proposed_shares if direction == "LONG" else -proposed_shares
    post_trade_qty = existing_qty_signed + proposed_signed
    if abs(post_trade_qty) < abs(existing_qty_signed):
        return RISK_REDUCING
    return RISK_INCREASING


def max_reducing_shares(existing_qty_signed: float, direction: str) -> float:
    """The largest share count for `direction` that keeps this trade
    RISK_REDUCING (i.e. does not overshoot past flat) — 0 if the proposed
    direction does not oppose the existing position at all."""
    if existing_qty_signed == 0:
        return 0.0
    if direction == "LONG" and existing_qty_signed < 0:
        return abs(existing_qty_signed)  # buying back a short, up to full cover
    if direction == "SHORT" and existing_qty_signed > 0:
        return existing_qty_signed  # selling an existing long, up to full close
    return 0.0  # same-direction add is never reducing


def _is_blank(value) -> bool:
    """True for None, empty/whitespace-only strings, and NaN — covers every
    "no value entered" shape a UI grid can hand back (Python None, a blank
    text cell, pandas/numpy NaN) without importing pandas here. `value != value`
    is a dependency-free NaN check that works for both plain float('nan') and
    numpy.float64('nan') (NaN is the only value never equal to itself)."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return value != value  # NaN check
    except Exception:  # noqa: BLE001 - an exotic type that can't even be compared is treated as blank
        return False


def normalize_position_input(
    symbol_raw, quantity_raw, reference_price_raw, planned_stop_raw=None, average_price_raw=None,
) -> tuple[AccountPosition | None, str | None]:
    """The single conversion/normalization boundary between an untyped UI
    input row (Streamlit's st.data_editor hands back a mix of Python
    int/float, numpy.int64/float64, pandas NaN, and None — never guaranteed
    to be clean Python primitives) and a valid AccountPosition.

    Returns (position, None) on success, or (None, None) for a row that's
    entirely blank (a data_editor "add row" placeholder — silently skipped,
    not an error), or (None, error_message) for a row that's partially
    filled and therefore invalid — error_message always names the symbol
    (or "Row" if even the symbol is missing) so the UI can show exactly
    which row/field is wrong.

    Every returned AccountPosition field is an ordinary Python primitive
    (str/float/None) — never a pandas/numpy scalar — safe to hand straight
    to a Supabase payload.
    """
    symbol = str(symbol_raw).strip().upper() if not _is_blank(symbol_raw) else ""

    if not symbol and _is_blank(quantity_raw) and _is_blank(reference_price_raw):
        return None, None  # a genuinely empty placeholder row

    label = symbol or "Row"

    if not symbol:
        return None, f"{label}: Symbol is required."

    if _is_blank(quantity_raw):
        return None, f"{symbol}: Signed Quantity is required."
    try:
        quantity = float(quantity_raw)
    except (TypeError, ValueError):
        return None, f"{symbol}: Signed Quantity must be a number."
    if not math.isfinite(quantity):
        return None, f"{symbol}: Signed Quantity must be a finite number."
    if quantity == 0:
        return None, f"{symbol}: Signed Quantity cannot be zero — delete the row instead to close the position."

    if _is_blank(reference_price_raw):
        return None, f"{symbol}: Reference Price is required."
    try:
        reference_price = float(reference_price_raw)
    except (TypeError, ValueError):
        return None, f"{symbol}: Reference Price must be a number."
    if not math.isfinite(reference_price) or reference_price <= 0:
        return None, f"{symbol}: Reference Price must be greater than 0."

    planned_stop = None
    if not _is_blank(planned_stop_raw):
        try:
            planned_stop = float(planned_stop_raw)
        except (TypeError, ValueError):
            return None, f"{symbol}: Planned Stop must be a number."
        if not math.isfinite(planned_stop) or planned_stop <= 0:
            return None, f"{symbol}: Planned Stop must be greater than 0."

    average_price = None
    if not _is_blank(average_price_raw):
        try:
            average_price = float(average_price_raw)
        except (TypeError, ValueError):
            return None, f"{symbol}: Average Price must be a number."
        if not math.isfinite(average_price) or average_price <= 0:
            return None, f"{symbol}: Average Price must be greater than 0."

    position = AccountPosition(
        symbol=symbol, quantity_signed=quantity, reference_price=reference_price,
        planned_stop_price=planned_stop, average_price=average_price, updated_at=datetime.now(timezone.utc),
    )
    return position, None


ROW_EMPTY = "EMPTY"
ROW_INCOMPLETE = "INCOMPLETE"
ROW_VALID = "VALID"
ROW_INVALID = "INVALID"


def classify_position_row(
    symbol_raw, quantity_raw, reference_price_raw, planned_stop_raw=None, average_price_raw=None,
) -> str:
    """UI-messaging-only classification of a raw editor row — display
    concern, never a second persistence/validation path (Save and Analyze
    both continue to go through normalize_position_input exclusively; this
    function only decides HOW to talk to the user about a row while they
    are still typing into it).

    A user filling in a new row's fields one at a time (Symbol, then
    Signed Quantity, then Reference Price, ...) passes through several
    genuinely blank-required-field states before the row is complete —
    those are NOT mistakes and must never be surfaced as hard validation
    errors (see the Round K UX bug report: showing "Signed Quantity is
    required." the instant Symbol is entered, before the user has even
    reached that field, is exactly the bug this function fixes). A row
    where an ALREADY-ENTERED field is malformed (wrong type, zero,
    negative, ...) is a different, genuine mistake worth flagging
    immediately — that distinction is what EMPTY/INCOMPLETE vs
    VALID/INVALID exists to capture:

      ROW_EMPTY       every field blank — the data_editor "add row"
                      placeholder. No message at all.
      ROW_INCOMPLETE  at least one REQUIRED field (symbol, signed
                      quantity, reference price) is still blank, but
                      nothing entered so far is wrong — the normal state
                      of a row mid-entry. No hard error; a soft/no
                      indicator only.
      ROW_VALID       every required field is present and well-formed.
                      Planned stop may still be blank — that's a
                      genuinely optional field, not part of row validity
                      (see the module-level open-risk-completeness
                      handling instead, which is a separate concern).
      ROW_INVALID     a field that DOES have a value is malformed (wrong
                      type, zero quantity, non-positive price, ...) —
                      worth surfacing right away, since it reflects an
                      actual entry mistake rather than an in-progress row.

    Implemented by delegating to normalize_position_input (never
    duplicating its rules) plus a lightweight pre-check of which required
    fields are still blank, so it can tell "blank" and "malformed" apart
    even though normalize_position_input's own return value collapses
    both into a single error string.
    """
    symbol_blank = _is_blank(symbol_raw)
    quantity_blank = _is_blank(quantity_raw)
    price_blank = _is_blank(reference_price_raw)

    if symbol_blank and quantity_blank and price_blank:
        return ROW_EMPTY

    position, error = normalize_position_input(
        symbol_raw, quantity_raw, reference_price_raw, planned_stop_raw, average_price_raw,
    )
    if position is not None:
        return ROW_VALID
    if error is None:
        return ROW_EMPTY  # normalize_position_input's own all-blank shortcut
    if symbol_blank or quantity_blank or price_blank:
        return ROW_INCOMPLETE
    return ROW_INVALID


def resolve_positions_precedence(
    live_positions: list[AccountPosition],
    live_errors: list[str],
    load_persisted,
) -> tuple[list[AccountPosition], bool]:
    """The single canonical source-precedence decision for "which positions
    should Trade Plan Analyze use" (Round I regression fix — a UI call site
    had drifted onto a helper name that no longer existed after the Round H
    Save Positions refactor; this function is now the one place both the
    Positions tab's live editor state and Trade Plan Analyze agree on).

    live_positions / live_errors are whatever normalize_position_input
    produced for the current (possibly unsaved) Positions-tab editor grid —
    an empty list/no errors means nothing has been entered there this
    session (including "the Positions tab was never visited," since that
    also normalizes to no rows). load_persisted is a zero-arg callable that
    fetches the last-saved positions from the repository; it is only called
    when needed, never eagerly.

    Precedence (fail-closed):
      A. live_errors is non-empty (a malformed row exists in the live,
         unsaved grid) -> NEVER use a partial subset of the live positions,
         since that would silently understate exposure/open risk. Fall back
         to load_persisted() instead, and tell the caller so it can warn the
         user their edits were not used. Returns (persisted, True).
      B. live_positions is non-empty and valid -> use them directly, even
         though unsaved — an in-progress edit is meant to take effect
         immediately for Analyze. Returns (live_positions, False).
      C. Neither A nor B applies (nothing has been entered live) ->
         load_persisted() silently — this is the normal case, not an error.
         Returns (persisted, False). If load_persisted() itself returns an
         empty list, callers fall back further to the manual Account State
         fields, exactly as when no positions exist at all.
    """
    if live_errors:
        return load_persisted(), True
    if live_positions:
        return live_positions, False
    return load_persisted(), False


def effective_account_state(raw_account: AccountRiskState, positions: list[AccountPosition]) -> tuple[AccountRiskState, bool]:
    """THE single canonical resolution of "what account state is actually in
    effect" (Round J root-cause fix — see the bug report: a disabled Account
    State field was showing the stale raw manual number instead of this
    derived one, because the derivation was only ever applied to a SEPARATE
    display-only value, not reused consistently). Both Trade Plan Analyze
    and the Account State tab must call this SAME function on the SAME
    resolved position list — never recompute the overlay independently —
    so the two can never silently disagree (see the display-consistency
    invariant tests in tests/test_account_state_effective_values.py).

    If `positions` is empty, returns (raw_account, True) unchanged — the
    manual/fallback fields are authoritative and there is nothing to be
    "incomplete" about. Otherwise overlays the derived long/short exposure
    and open-positions count (always fully derivable) plus open risk (only
    when every position has a usable planned stop — never substituted with
    0 or the stale raw value when incomplete, per derive_open_risk's own
    fail-closed contract) onto `raw_account`.

    Returns (effective_account, open_risk_complete). When open_risk_complete
    is False, effective_account.open_risk is NOT the derived figure (it is
    only ever set from the raw_account.open_risk as a dataclass-validity
    placeholder) — callers must check open_risk_complete before treating
    open_risk-derived numbers (like portfolio_heat_pct) as meaningful, and
    must never display effective_account.open_risk directly in that case.
    """
    if not positions:
        return raw_account, True
    exposure = derive_exposure(positions)
    open_risk = derive_open_risk(positions)
    effective = replace(
        raw_account,
        long_exposure_notional=exposure.long_exposure_notional,
        short_exposure_notional=exposure.short_exposure_notional,
        open_positions=exposure.open_positions,
        open_risk=open_risk.open_risk if open_risk.complete else raw_account.open_risk,
    )
    return effective, open_risk.complete
