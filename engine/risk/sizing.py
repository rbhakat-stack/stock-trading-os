"""Position sizing (§12; final-hardening redesign of the exposure constraints
per §2-3, §6, §12-13 of the audit).

    risk_per_share = abs(entry - stop)
    max_trade_loss = net_liquidation_value * risk_per_trade_pct
    raw_size = max_trade_loss / risk_per_share

...then floored to the MOST conservative of several independent constraints,
and floored again to a whole share count. POSITION VALUE (notional) and
CAPITAL AT RISK (planned loss to stop) are always reported separately — they
are never the same number and must never be conflated.

SIGNED EXPOSURE ARITHMETIC (final hardening round): `symbol_exposure`,
`gross_exposure`, `leverage`, and `net_exposure` all now compute their
remaining share budget from SIGNED existing positions, not by blindly adding
the proposed trade's notional to an unsigned aggregate. A trade that OFFSETS
an existing position (e.g. a SHORT against an existing LONG in the same
symbol) naturally gets MORE remaining budget under this arithmetic — never
less — because the post-trade notional it would produce is genuinely smaller.
This is why no separate "risk-reducing bypass" exists here: correct signed
arithmetic already produces the right, more-permissive answer for reducing
trades, and the right, equally-strict answer for increasing ones. See
`_signed_remaining_shares` below — every one of those four constraints is
this same shape, just with different existing-signed-quantity and ceiling
inputs.

portfolio_heat, risk_per_trade, and buying_power are DELIBERATELY NOT made
signed/offset-aware — Phase 3 has no execution engine and no reliable model
of how a partial position reduction changes realized/unrealized P&L or
margin release, so these three stay conservative (always additive) rather
than risk understating capital-at-risk or buying-power consumption for a
reducing trade. This is a documented, deliberate Phase 3 simplification (see
the final hardening report), not an oversight.

BINDING-CONSTRAINT PRECEDENCE: ties are resolved by first-occurrence in this
fixed, documented insertion order — Python's `min(dict, key=...)` already
does this deterministically, so the order candidates are added to the dict
below IS the tie-break precedence:

    1. risk_per_trade
    2. buying_power
    3. symbol_exposure
    4. gross_exposure
    5. net_exposure
    6. portfolio_heat
    7. leverage
    (8. max_shares_override, if supplied — always most conservative when given)

NET EXPOSURE vs LEVERAGE vs GROSS EXPOSURE vs SYMBOL EXPOSURE — four
distinct, independently configurable policy concepts:
  - symbol_exposure %: abs notional in ONE symbol / NLV * 100.
  - gross_exposure %: total abs notional across ALL symbols / NLV * 100.
  - net_exposure %: abs(account-wide signed long-short notional) / NLV * 100.
  - leverage: gross notional / NLV — numerically leverage == gross_exposure_pct
    / 100, but kept as a SEPARATE ceiling (a ratio, not a percentage).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .policy import RiskPolicy


@dataclass(frozen=True)
class PositionSizeResult:
    shares: int
    position_value: float
    capital_at_risk: float
    risk_per_share: float
    binding_constraint: str
    evidence: dict = field(default_factory=dict)
    # --- Display-only explainability fields (never fed into any constraint
    # math above — see the module docstring's binding-constraint list, which
    # this section does not participate in). Added so the UI can show WHY a
    # size was chosen using the engine's own canonical numbers instead of
    # re-deriving them independently. ---
    existing_shares_signed: float = 0.0
    post_trade_shares_signed: float = 0.0
    post_trade_open_risk: float = 0.0
    post_trade_portfolio_heat_pct: float = 0.0
    existing_symbol_exposure_value: float = 0.0
    max_symbol_exposure_value: float = 0.0
    symbol_exposure_remaining_value: float = 0.0
    unrestricted_risk_based_value: float = 0.0
    post_trade_symbol_exposure_value: float = 0.0
    post_trade_symbol_exposure_pct: float = 0.0


def _signed_remaining_notional(direction: str, existing_signed_notional: float, max_allowed_notional: float) -> float:
    """The dollar remaining-capacity half of _signed_remaining_shares below,
    factored out so display code (PositionSizeResult.symbol_exposure_remaining_value)
    can report the EXACT same number the gate itself used, never a
    recomputed/duplicated approximation."""
    max_allowed_notional = max(0.0, max_allowed_notional)
    if direction == "SHORT":
        remaining = existing_signed_notional + max_allowed_notional
    else:  # LONG (default)
        remaining = max_allowed_notional - existing_signed_notional
    return max(0.0, remaining)


def _signed_remaining_shares(direction: str, existing_signed_notional: float, max_allowed_notional: float, entry: float) -> float:
    """Shared shape behind symbol_exposure, gross_exposure, leverage, and
    net_exposure: how many more shares of `direction` fit before the
    resulting signed notional's ABSOLUTE VALUE would exceed
    `max_allowed_notional`. A LONG trade pushes the signed figure up; a
    SHORT trade pushes it down — so the remaining budget is computed toward
    whichever side of zero this trade moves it. Naturally larger (more
    permissive) when the trade offsets an existing opposite-sign position."""
    remaining = _signed_remaining_notional(direction, existing_signed_notional, max_allowed_notional)
    if entry <= 0:
        return 0.0
    return remaining / entry


def compute_position_size(
    entry: float,
    stop: float,
    net_liquidation_value: float,
    risk_policy: RiskPolicy,
    buying_power: float,
    direction: str = "LONG",
    existing_symbol_notional_signed: float = 0.0,  # + = existing LONG in THIS symbol, - = existing SHORT, 0 = flat
    existing_symbol_shares_signed: float = 0.0,  # + = existing LONG shares, - = existing SHORT shares, 0 = flat.
    # Display-only (post_trade_shares_signed below) — never an input to any
    # constraint candidate above; those all key off the dollar notional.
    existing_gross_exposure_value: float = 0.0,  # unsigned total abs notional across ALL symbols (pre-trade)
    existing_net_exposure_value: float = 0.0,  # signed: + = account net long, - = account net short
    current_open_risk: float = 0.0,
    max_shares_override: int | None = None,
) -> PositionSizeResult:
    # Numeric safety: any non-finite input (NaN/inf) must never silently
    # propagate into share-count arithmetic — int(nan) raises, and inf
    # candidates would corrupt the min() comparison. Fail closed instead.
    numeric_inputs = (
        entry, stop, net_liquidation_value, buying_power, existing_symbol_notional_signed,
        existing_symbol_shares_signed, existing_gross_exposure_value, existing_net_exposure_value, current_open_risk,
    )
    if not all(math.isfinite(v) for v in numeric_inputs):
        return PositionSizeResult(
            shares=0, position_value=0.0, capital_at_risk=0.0, risk_per_share=0.0,
            binding_constraint="INVALID_NUMERIC_INPUT",
            evidence={"reason": "one or more numeric inputs is NaN or infinite"},
        )

    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0 or net_liquidation_value <= 0:
        return PositionSizeResult(
            shares=0, position_value=0.0, capital_at_risk=0.0, risk_per_share=round(risk_per_share, 4),
            binding_constraint="INVALID_INPUT",
            evidence={"reason": "entry, stop, and net_liquidation_value must be positive and entry != stop"},
        )

    max_trade_loss = net_liquidation_value * (risk_policy.risk_per_trade_pct / 100)
    candidates: dict[str, float] = {"risk_per_trade": max(0.0, max_trade_loss / risk_per_share)}

    candidates["buying_power"] = max(0.0, buying_power / entry)

    max_symbol_value = net_liquidation_value * (risk_policy.max_symbol_exposure_pct / 100)
    candidates["symbol_exposure"] = _signed_remaining_shares(direction, existing_symbol_notional_signed, max_symbol_value, entry)

    # Gross exposure, evaluated for THIS symbol's contribution only: remove
    # this symbol's pre-trade abs notional from the account-wide gross total,
    # then re-add its post-trade abs notional (computed via the same signed
    # helper) — an offsetting trade shrinks this symbol's contribution, which
    # shrinks gross exposure too, exactly matching §12's worked example.
    pre_trade_symbol_abs = abs(existing_symbol_notional_signed)
    gross_excluding_symbol = max(0.0, existing_gross_exposure_value - pre_trade_symbol_abs)
    max_gross_value = net_liquidation_value * (risk_policy.max_gross_exposure_pct / 100)
    cap_for_symbol_within_gross = max_gross_value - gross_excluding_symbol
    candidates["gross_exposure"] = _signed_remaining_shares(direction, existing_symbol_notional_signed, cap_for_symbol_within_gross, entry)

    max_net_value = net_liquidation_value * (risk_policy.max_net_exposure_pct / 100)
    candidates["net_exposure"] = _signed_remaining_shares(direction, existing_net_exposure_value, max_net_value, entry)

    max_heat_value = net_liquidation_value * (risk_policy.max_portfolio_heat_pct / 100)
    remaining_heat_budget = max(0.0, max_heat_value - current_open_risk)
    candidates["portfolio_heat"] = remaining_heat_budget / risk_per_share

    # Leverage: same "exclude this symbol, then re-add its post-trade abs
    # notional" treatment as gross_exposure above, just against the
    # leverage ceiling (NLV * max_leverage) instead of the gross-% ceiling.
    max_leverage_value = net_liquidation_value * risk_policy.max_leverage
    cap_for_symbol_within_leverage = max_leverage_value - gross_excluding_symbol
    candidates["leverage"] = _signed_remaining_shares(direction, existing_symbol_notional_signed, cap_for_symbol_within_leverage, entry)

    if max_shares_override is not None:
        candidates["max_shares_override"] = float(max_shares_override)

    binding_constraint = min(candidates, key=lambda k: candidates[k])
    shares = max(0, int(candidates[binding_constraint]))  # floor to a whole share, never negative

    position_value = round(shares * entry, 2)
    capital_at_risk = round(shares * risk_per_share, 2)

    # --- Display-only explainability values below (§ UI clarity round) ---
    # None of these feed back into `candidates` / `binding_constraint` /
    # `shares` above — they only re-report numbers the gating logic already
    # computed (or trivial signed sums of them), so the actual sizing
    # decision is completely unchanged.
    signed_shares_change = shares if direction == "LONG" else -shares
    post_trade_shares_signed = existing_symbol_shares_signed + signed_shares_change

    post_trade_open_risk = round(current_open_risk + capital_at_risk, 2)
    post_trade_portfolio_heat_pct = (
        round((post_trade_open_risk / net_liquidation_value) * 100, 3) if net_liquidation_value > 0 else 0.0
    )

    signed_notional_change = position_value if direction == "LONG" else -position_value
    post_trade_symbol_notional_signed = existing_symbol_notional_signed + signed_notional_change
    post_trade_symbol_exposure_value = round(abs(post_trade_symbol_notional_signed), 2)
    post_trade_symbol_exposure_pct = (
        round((post_trade_symbol_exposure_value / net_liquidation_value) * 100, 3) if net_liquidation_value > 0 else 0.0
    )

    return PositionSizeResult(
        shares=shares,
        position_value=position_value,
        capital_at_risk=capital_at_risk,
        risk_per_share=round(risk_per_share, 4),
        binding_constraint=binding_constraint,
        evidence={"candidates_shares": {k: round(v, 3) for k, v in candidates.items()}, "max_trade_loss": round(max_trade_loss, 2)},
        existing_shares_signed=existing_symbol_shares_signed,
        post_trade_shares_signed=post_trade_shares_signed,
        post_trade_open_risk=post_trade_open_risk,
        post_trade_portfolio_heat_pct=post_trade_portfolio_heat_pct,
        existing_symbol_exposure_value=round(abs(existing_symbol_notional_signed), 2),
        max_symbol_exposure_value=round(max_symbol_value, 2),
        symbol_exposure_remaining_value=round(_signed_remaining_notional(direction, existing_symbol_notional_signed, max_symbol_value), 2),
        unrestricted_risk_based_value=round(candidates["risk_per_trade"] * entry, 2),
        post_trade_symbol_exposure_value=post_trade_symbol_exposure_value,
        post_trade_symbol_exposure_pct=post_trade_symbol_exposure_pct,
    )
