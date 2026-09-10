"""Repository functions for Phase 3 risk policy / account state / trade
decision persistence — see TRADING_OS_DESIGN.md-style philosophy notes in
supabase/migrations/0004_phase3_risk_trade_planner.sql for what's persisted
vs. recomputed.

These use the caller's authenticated (RLS-scoped) client — every table here
is owner-scoped, consistent with the existing watchlists pattern.
"""
from __future__ import annotations

from datetime import datetime

from supabase import Client

from engine.risk.account_state import AccountRiskState
from engine.risk.policy import RiskPolicy
from engine.trade.planner import TradePlanningSnapshot

_POLICY_FIELDS = (
    "risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_portfolio_heat_pct",
    "max_symbol_exposure_pct", "max_gross_exposure_pct", "max_net_exposure_pct", "max_open_positions",
    "max_trades_per_day", "min_rr", "max_leverage", "cooldown_after_losses", "max_consecutive_losses",
    "max_daily_realized_loss_pct",
)

_ACCOUNT_FIELDS = (
    "source", "net_liquidation_value", "cash", "buying_power", "realized_pnl_today", "unrealized_pnl",
    "daily_start_equity", "weekly_start_equity", "open_risk", "open_positions", "trades_today",
    "consecutive_losses", "long_exposure_notional", "short_exposure_notional",
)


def get_risk_policy(client: Client, user_id: str) -> RiskPolicy | None:
    res = client.table("risk_policies").select("*").eq("user_id", user_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        return None
    row = rows[0]
    return RiskPolicy(**{f: row[f] for f in _POLICY_FIELDS})


def upsert_risk_policy(client: Client, user_id: str, policy: RiskPolicy) -> None:
    row = {f: getattr(policy, f) for f in _POLICY_FIELDS}
    row["user_id"] = user_id
    client.table("risk_policies").upsert(row, on_conflict="user_id").execute()


def get_account_risk_state(client: Client, user_id: str) -> AccountRiskState | None:
    res = client.table("account_risk_states").select("*").eq("user_id", user_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        return None
    row = rows[0]
    return AccountRiskState(
        **{f: row[f] for f in _ACCOUNT_FIELDS},
        as_of=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else datetime.utcnow(),
    )


def upsert_account_risk_state(client: Client, user_id: str, account: AccountRiskState) -> None:
    row = {f: getattr(account, f) for f in _ACCOUNT_FIELDS}
    row["user_id"] = user_id
    client.table("account_risk_states").upsert(row, on_conflict="user_id").execute()


def build_trade_decision_row(
    user_id: str, symbol: str, timeframe: str, plan: TradePlanningSnapshot,
    playbook_id: str | None = None, playbook_version: str | None = None, playbook_family: str | None = None,
) -> dict:
    """Pure mapping from a TradePlanningSnapshot to the trade_decisions row
    shape — separated from insert_trade_decision so persistence semantics
    (which field survives for which decision outcome) are unit-testable
    without a live Supabase call. See the Phase 3 Decision History
    persistence audit: the general rule is "persist anything genuinely known
    at decision time, independent of final outcome" — quality/rr/entry/stop/
    target all come from plan-level fields (always set once a candidate is
    TRIGGERED, regardless of what the decision engine then does with it),
    while no_trade_reasons/position_size are genuinely decision-scoped (no
    equivalent plan-level field exists — sizing may legitimately never run).

    Phase 4 (§24/§46): playbook_id/playbook_version/playbook_family are
    OPTIONAL, default None — every pre-Phase-4 call site (and every existing
    Phase 3 test) keeps working unchanged, and existing persisted rows with
    these columns NULL remain valid forever. Only app/pages/trade_planner.py's
    Analyze flow, once it looks up the matching PlaybookEvaluation for
    plan.candidate.setup_type, passes them."""
    decision = plan.decision
    candidate = plan.candidate
    targets = plan.targets
    position = decision.position_size

    return {
        "user_id": user_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "setup_type": candidate.setup_type if candidate else None,
        "direction": candidate.direction if candidate else "NONE",
        "decision": decision.decision,
        # Read from plan.quality (the planner-level field, always set for any
        # TRIGGERED candidate — see engine/trade/planner.py::build_trade_plan),
        # NOT decision.quality (only echoed back on TradeDecisionResult for
        # the final QUALIFIED/CONDITIONAL return path). A TRIGGERED candidate
        # rejected by an account/policy gate (e.g. MAX_POSITIONS_REACHED)
        # returns early from make_trade_decision without ever setting
        # decision.quality, even though quality was already fully computed —
        # using decision.quality here silently dropped it. See the Phase 3
        # Decision History persistence audit.
        "quality_score": plan.quality.score if plan.quality else None,
        "quality_band": plan.quality.band if plan.quality else None,
        "entry_zone_low": candidate.entry_zone_low if candidate else None,
        "entry_zone_high": candidate.entry_zone_high if candidate else None,
        "entry_price": plan.entry_price,
        "stop": plan.invalidation.stop_price if plan.invalidation else None,
        "target_1": targets[0].price if len(targets) > 0 else None,
        "target_2": targets[1].price if len(targets) > 1 else None,
        "rr1": targets[0].r_multiple if len(targets) > 0 else None,
        "rr2": targets[1].r_multiple if len(targets) > 1 else None,
        "position_size": position.shares if position else None,
        "position_value": position.position_value if position else None,
        "capital_at_risk": position.capital_at_risk if position else None,
        "portfolio_heat_before": round(plan.account.portfolio_heat_pct, 3),
        "portfolio_heat_after": (
            round(((plan.account.open_risk + position.capital_at_risk) / plan.account.net_liquidation_value) * 100, 3)
            if position and plan.account.net_liquidation_value > 0
            else None
        ),
        "no_trade_reasons": decision.no_trade_reasons,
        "reasons_for": decision.reasons_for,
        "reasons_against": decision.reasons_against,
        "evidence": {
            "conditions_to_wait_for": decision.conditions_to_wait_for,
            "kill_switch_state": plan.kill_switch.state,
            "kill_switch_triggers": plan.kill_switch.triggers,
            "statistical_validation": plan.statistical_validation,
            "event_risk": plan.event_risk,
            "liquidity": plan.liquidity,
            "candidate_evidence": candidate.evidence if candidate else None,
        },
        "as_of": plan.as_of.isoformat() if plan.as_of else None,
        "playbook_id": playbook_id,
        "playbook_version": playbook_version,
        "playbook_family": playbook_family,
    }


def insert_trade_decision(
    client: Client, user_id: str, symbol: str, timeframe: str, plan: TradePlanningSnapshot,
    playbook_id: str | None = None, playbook_version: str | None = None, playbook_family: str | None = None,
) -> None:
    row = build_trade_decision_row(user_id, symbol, timeframe, plan, playbook_id, playbook_version, playbook_family)
    try:
        client.table("trade_decisions").insert(row).execute()
    except Exception:
        # migration 0007 may not be applied yet (playbook_id/_version/_family
        # columns don't exist) — retry once without those three keys rather
        # than losing the whole decision log entry. Never silently swallow
        # any OTHER failure; re-raise if the retry also fails.
        if playbook_id is None and playbook_version is None and playbook_family is None:
            raise
        fallback_row = {k: v for k, v in row.items() if k not in ("playbook_id", "playbook_version", "playbook_family")}
        client.table("trade_decisions").insert(fallback_row).execute()


def list_trade_decisions(client: Client, user_id: str, limit: int = 50) -> list[dict]:
    res = (
        client.table("trade_decisions")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []
