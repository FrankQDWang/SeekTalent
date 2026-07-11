from __future__ import annotations

from seektalent.models import ReflectionContext, RoundState, RunState

from seektalent.runtime.context_views import dropped_candidates, top_candidates
from seektalent.runtime.query_identity import consumed_non_anchor_term_family_ids


def build_reflection_context(
    *,
    run_state: RunState,
    round_state: RoundState,
) -> ReflectionContext:
    if round_state.search_observation is None:
        raise ValueError("round_state.search_observation is required for reflection context")
    return ReflectionContext(
        round_no=round_state.round_no,
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        current_retrieval_plan=round_state.retrieval_plan,
        search_observation=round_state.search_observation,
        search_attempts=round_state.search_attempts,
        top_candidates=round_state.top_candidates or top_candidates(run_state),
        dropped_candidates=dropped_candidates(run_state, round_state),
        scoring_failures=list(round_state.scoring_failures),
        sent_query_history=run_state.retrieval_state.sent_query_history,
        query_term_pool=run_state.retrieval_state.query_term_pool,
        canonical_intake_summary=run_state.latest_canonical_intake_summary,
        controller_decision=round_state.controller_decision,
        query_outcomes=round_state.query_outcomes[:2],
        consumed_non_anchor_term_family_ids=sorted(
            consumed_non_anchor_term_family_ids(run_state.retrieval_state.query_execution_ledger)
        ),
    )
