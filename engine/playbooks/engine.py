"""Phase 4 playbook engine entry point (§16-18 of the report).

evaluate_all_playbooks() evaluates EVERY enabled, implementable playbook
against ONE already-built MarketIntelligenceSnapshot — never re-fetches or
recomputes market data per playbook (§43: one market context, many playbook
evaluations). select_primary_candidate() then ranks the results
deterministically, and detect_conflicts() flags simultaneous opposing
TRIGGERED candidates (§18) without silently picking one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evaluation import PlaybookEvaluation
from .evaluators.breakout_retest import evaluate_breakout_retest_long, evaluate_breakout_retest_short
from .evaluators.failed_breakout_reversal import (
    evaluate_failed_breakout_reversal_long, evaluate_failed_breakout_reversal_short,
)
from .evaluators.opening_range_breakout import (
    evaluate_opening_range_breakout_long, evaluate_opening_range_breakout_short,
)
from .evaluators.range_mean_reversion import evaluate_range_mean_reversion_long, evaluate_range_mean_reversion_short
from .evaluators.trend_pullback import evaluate_trend_pullback_long, evaluate_trend_pullback_short
from .registry import enabled_definitions
from .taxonomy import PlaybookId, SETUP_STATUS_RANK

_EVALUATORS = {
    PlaybookId.BREAKOUT_RETEST_LONG.value: evaluate_breakout_retest_long,
    PlaybookId.BREAKOUT_RETEST_SHORT.value: evaluate_breakout_retest_short,
    PlaybookId.TREND_PULLBACK_LONG.value: evaluate_trend_pullback_long,
    PlaybookId.TREND_PULLBACK_SHORT.value: evaluate_trend_pullback_short,
    PlaybookId.FAILED_BREAKOUT_REVERSAL_LONG.value: evaluate_failed_breakout_reversal_long,
    PlaybookId.FAILED_BREAKOUT_REVERSAL_SHORT.value: evaluate_failed_breakout_reversal_short,
    PlaybookId.OPENING_RANGE_BREAKOUT_LONG.value: evaluate_opening_range_breakout_long,
    PlaybookId.OPENING_RANGE_BREAKOUT_SHORT.value: evaluate_opening_range_breakout_short,
    PlaybookId.RANGE_MEAN_REVERSION_LONG.value: evaluate_range_mean_reversion_long,
    PlaybookId.RANGE_MEAN_REVERSION_SHORT.value: evaluate_range_mean_reversion_short,
}


def evaluate_all_playbooks(snapshot, current_price: float, min_rr: float = 1.5, client=None) -> list[PlaybookEvaluation]:
    """Evaluates every ENABLED, IMPLEMENTABLE playbook definition against
    `snapshot` — never stops at the first match (§16). A playbook whose
    timeframe isn't supported short-circuits to NOT_ELIGIBLE with
    eligibility_status=NOT_ELIGIBLE_TIMEFRAME BEFORE its evaluator even runs
    (§19) — no evaluator needs to duplicate that check.

    `client` (optional): when given an authenticated Supabase client, a
    SUPER_ADMIN's persisted playbook_configs override (§22) is fetched ONCE
    here and applied as an additional disable-only gate — a playbook the code
    still marks enabled=True can be turned off at runtime this way, but a DB
    row can never re-enable something the code has marked NOT_IMPLEMENTABLE_YET
    or disabled, since only definitions already in enabled_definitions() are
    considered in the first place. Omitting `client` (every existing test,
    and any caller without a live DB session) preserves the pre-existing
    code-defined-only behavior exactly — found and fixed during the Phase 4
    acceptance audit: the SUPER_ADMIN toggle previously only affected what the
    Playbooks page displayed, never what the engine actually evaluated."""
    from .taxonomy import EligibilityStatus, SetupStatus
    from ._shared import not_eligible_evaluation

    overrides: dict = {}
    if client is not None:
        from repository.playbook_repository import get_playbook_overrides
        try:
            overrides = get_playbook_overrides(client)
        except Exception:  # noqa: BLE001 - a config-override lookup failure must never block evaluation
            overrides = {}

    results: list[PlaybookEvaluation] = []
    for definition in enabled_definitions():
        override = overrides.get(definition.playbook_id)
        if override is not None and not override.get("enabled", True):
            continue  # SUPER_ADMIN has disabled this playbook via playbook_configs (§22)
        if snapshot.timeframe not in definition.supported_timeframes:
            from .evidence import EvidenceItem
            tf_item = EvidenceItem(
                code="TIMEFRAME_SUPPORTED", label="Timeframe is supported by this playbook",
                observed_value=snapshot.timeframe, expected_value=", ".join(definition.supported_timeframes),
                passed=False, source="request",
            )
            ev = not_eligible_evaluation(definition, snapshot, (), (tf_item,))
            ev = PlaybookEvaluation(**{**ev.__dict__, "eligibility_status": EligibilityStatus.NOT_ELIGIBLE_TIMEFRAME.value})
            results.append(ev)
            continue
        evaluator = _EVALUATORS[definition.playbook_id]
        results.append(evaluator(snapshot, current_price, min_rr))
    return results


def _tie_break_key(evaluation: PlaybookEvaluation, priority_by_id: dict[str, int]) -> tuple:
    status_rank = SETUP_STATUS_RANK.get(evaluation.setup_status, -1)
    quality = evaluation.quality_score if evaluation.quality_score is not None else -1
    priority = priority_by_id.get(evaluation.playbook_id, 100)
    # Deterministic, stable tie-break (§17): status desc, quality desc,
    # configured priority asc (lower = higher precedence), then playbook_id
    # alphabetically — never raw dict/list ordering.
    return (-status_rank, -quality, priority, evaluation.playbook_id)


def rank_evaluations(evaluations: list[PlaybookEvaluation]) -> list[PlaybookEvaluation]:
    """Deterministic ranking (§17): TRIGGERED > FORMING > INVALIDATED >
    NOT_ELIGIBLE; within the same status, quality_score descending; then
    each playbook's configured `priority` (lower sorts first); then a
    stable alphabetical playbook_id tie-break. Never arbitrary dict/list
    ordering."""
    from .registry import get_definition
    priority_by_id = {e.playbook_id: get_definition(e.playbook_id).priority for e in evaluations}
    return sorted(evaluations, key=lambda e: _tie_break_key(e, priority_by_id))


def select_primary_candidate(evaluations: list[PlaybookEvaluation]) -> PlaybookEvaluation | None:
    ranked = rank_evaluations(evaluations)
    if not ranked:
        return None
    top = ranked[0]
    if top.setup_status == "NOT_ELIGIBLE":
        return None
    return top


@dataclass(frozen=True)
class PlaybookConflict:
    """A contextual warning (§18) — never auto-resolved by silently picking
    one side. `long_candidate`/`short_candidate` are the highest-quality
    TRIGGERED evaluation on each side."""
    long_candidate: PlaybookEvaluation
    short_candidate: PlaybookEvaluation
    reason: str = "Market structure is producing competing valid setups in both directions."
    evidence: dict = field(default_factory=dict)


def detect_conflicts(evaluations: list[PlaybookEvaluation]) -> PlaybookConflict | None:
    """Flags a simultaneous TRIGGERED long AND TRIGGERED short (§18) — e.g. a
    TRIGGERED long continuation alongside a TRIGGERED short reversal.
    Returns None when no conflict exists; never silently discards either
    candidate."""
    triggered = [e for e in evaluations if e.setup_status == "TRIGGERED"]
    longs = [e for e in triggered if e.direction == "LONG"]
    shorts = [e for e in triggered if e.direction == "SHORT"]
    if not longs or not shorts:
        return None
    ranked_longs = rank_evaluations(longs)
    ranked_shorts = rank_evaluations(shorts)
    return PlaybookConflict(
        long_candidate=ranked_longs[0], short_candidate=ranked_shorts[0],
        evidence={"triggered_long_count": len(longs), "triggered_short_count": len(shorts)},
    )
