"""Portfolio heat (§14).

Portfolio heat is the sum of PLANNED LOSS TO STOPS across open positions,
divided by current net liquidation value — never notional/position value,
which would conflate "how much is deployed" with "how much can be lost."
"""
from __future__ import annotations


def compute_portfolio_heat_pct(open_risk: float, net_liquidation_value: float) -> float:
    if net_liquidation_value <= 0:
        return 0.0
    return (open_risk / net_liquidation_value) * 100


def compute_post_trade_heat_pct(current_open_risk: float, new_trade_risk: float, net_liquidation_value: float) -> float:
    return compute_portfolio_heat_pct(current_open_risk + new_trade_risk, net_liquidation_value)
