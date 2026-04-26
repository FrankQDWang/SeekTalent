from __future__ import annotations

from seektalent.models import FinalizeContext, RunState
from seektalent.requirements import build_requirement_digest

from seektalent.runtime.context_views import top_candidates


def build_finalize_context(
    *,
    run_state: RunState,
    rounds_executed: int,
    stop_reason: str,
    run_id: str,
    run_dir: str,
) -> FinalizeContext:
    return FinalizeContext(
        run_id=run_id,
        run_dir=run_dir,
        rounds_executed=rounds_executed,
        stop_reason=stop_reason,
        top_candidates=top_candidates(run_state),
        requirement_digest=build_requirement_digest(run_state.requirement_sheet),
        sent_query_history=run_state.retrieval_state.sent_query_history,
    )
