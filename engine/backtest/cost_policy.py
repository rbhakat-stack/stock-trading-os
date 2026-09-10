"""Phase 5.2V §10/§11 — TransactionCostPolicy: a versioned, extensible
contract for Phase 5.3 to apply and label NAMED cost scenarios without ever
overwriting a stored gross_R. This module computes no statistic and invents
no "correct" slippage number — see the Phase 5.2V report for why.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransactionCostPolicy:
    cost_policy_id: str
    cost_policy_version: str
    description: str
    assumptions: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cost_policy_id or not self.cost_policy_version:
            raise ValueError("TransactionCostPolicy requires an explicit cost_policy_id and cost_policy_version")


ZERO_COST_RESEARCH_POLICY = TransactionCostPolicy(
    cost_policy_id="ZERO_COST_RESEARCH",
    cost_policy_version="v1",
    description=(
        "No transaction costs modeled — gross_R == net_R under this policy. Research/mechanical-validation "
        "use only. NEVER presented as a broker-measured or realistic cost assumption."
    ),
    assumptions={"commission_per_share": 0.0, "spread_model": "NONE", "slippage_model": "NONE"},
)


def apply_cost_policy(gross_R: float | None, policy: TransactionCostPolicy) -> float | None:
    """Phase 5.2V implements ONLY the zero-cost identity — matching what
    `engine.backtest.outcomes` already does for `HistoricalTradeOutcome.
    net_R`. This is the ONE place Phase 5.3 will add real, named modeled
    cost scenarios (§11 — 'cost sensitivity': the same outcomes evaluated
    under several explicit cost assumptions, to see whether an edge
    survives friction) without ever touching a stored `gross_R` value
    (§10 — 'Do not modify historical gross_R'). An unrecognized policy
    fails closed rather than silently guessing a cost number.
    """
    if gross_R is None:
        return None
    if (
        policy.cost_policy_id == ZERO_COST_RESEARCH_POLICY.cost_policy_id
        and policy.cost_policy_version == ZERO_COST_RESEARCH_POLICY.cost_policy_version
    ):
        return gross_R
    raise NotImplementedError(
        f"cost policy {policy.cost_policy_id!r} v{policy.cost_policy_version!r} is not yet implemented — "
        "Phase 5.3 will add named modeled cost scenarios here; no policy may silently assume a cost number."
    )
