from __future__ import annotations

from seektalent.models import RunState, RuntimeConstraint, ScoringContext
from seektalent.tracing import json_sha256


def build_scoring_context(
    *,
    run_state: RunState,
    round_no: int,
    normalized_resume,
    runtime_only_constraints: list[RuntimeConstraint] | None = None,
) -> ScoringContext:
    return ScoringContext(
        round_no=round_no,
        scoring_policy=run_state.scoring_policy,
        normalized_resume=normalized_resume,
        requirement_sheet_sha256=json_sha256(run_state.requirement_sheet.model_dump(mode="json")),
        runtime_only_constraints=list(runtime_only_constraints or []),
    )
