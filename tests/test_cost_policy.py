"""Phase 5.2V §10/§11/§15 — TransactionCostPolicy versioning tests."""
from __future__ import annotations

import pytest

from engine.backtest.cost_policy import TransactionCostPolicy, ZERO_COST_RESEARCH_POLICY, apply_cost_policy


def test_zero_cost_policy_is_versioned_and_documents_assumptions():
    assert ZERO_COST_RESEARCH_POLICY.cost_policy_id == "ZERO_COST_RESEARCH"
    assert ZERO_COST_RESEARCH_POLICY.cost_policy_version == "v1"
    assert ZERO_COST_RESEARCH_POLICY.assumptions  # non-empty, documented


def test_zero_cost_policy_net_equals_gross():
    assert apply_cost_policy(2.5, ZERO_COST_RESEARCH_POLICY) == 2.5
    assert apply_cost_policy(-1.0, ZERO_COST_RESEARCH_POLICY) == -1.0


def test_none_gross_r_stays_none_under_any_policy():
    assert apply_cost_policy(None, ZERO_COST_RESEARCH_POLICY) is None


def test_unrecognized_cost_policy_fails_closed_never_guesses():
    hypothetical = TransactionCostPolicy(
        cost_policy_id="FIXED_SLIPPAGE_BPS", cost_policy_version="v1", description="not yet implemented",
        assumptions={"slippage_bps": 5},
    )
    with pytest.raises(NotImplementedError):
        apply_cost_policy(2.0, hypothetical)


def test_cost_policy_requires_explicit_id_and_version():
    with pytest.raises(ValueError):
        TransactionCostPolicy(cost_policy_id="", cost_policy_version="v1", description="x")
    with pytest.raises(ValueError):
        TransactionCostPolicy(cost_policy_id="X", cost_policy_version="", description="x")


def test_different_version_of_the_same_cost_policy_id_is_not_silently_zero_cost():
    different_version = TransactionCostPolicy(
        cost_policy_id="ZERO_COST_RESEARCH", cost_policy_version="v2", description="future variant",
    )
    with pytest.raises(NotImplementedError):
        apply_cost_policy(2.0, different_version)
