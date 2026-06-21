from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import RuntimeConstraint, RunState
from seektalent.protected_attributes import PROTECTED_ATTRIBUTE_FIELDS
from seektalent.runtime.stop_reasons import PUBLIC_STOP_REASON_ALLOWLIST

RUNTIME_CONSTRAINTS_POLICY_VERSION = "runtime-constraints.v1"
NEAR_BUDGET_THRESHOLD_RATIO = 0.8
__all__ = [
    "NEAR_BUDGET_THRESHOLD_RATIO",
    "PROTECTED_ATTRIBUTE_FIELDS",
    "RUNTIME_CONSTRAINTS_POLICY_VERSION",
    "RuntimeConstraintsContractV1",
    "runtime_constraints_from_run_state",
]


class RuntimeConstraintsContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = RUNTIME_CONSTRAINTS_POLICY_VERSION
    max_rounds: int
    min_rounds: int
    search_max_pages_per_round: int
    search_max_attempts_per_round: int
    search_no_progress_limit: int
    scoring_timeout_seconds: float
    prf_probe_phrase_proposal_timeout_seconds: float
    near_budget_threshold_ratio: float = NEAR_BUDGET_THRESHOLD_RATIO
    stop_reason_allowlist: tuple[str, ...] = Field(default=PUBLIC_STOP_REASON_ALLOWLIST)

    @classmethod
    def from_settings(cls, settings) -> "RuntimeConstraintsContractV1":
        return cls(
            max_rounds=settings.max_rounds,
            min_rounds=settings.min_rounds,
            search_max_pages_per_round=settings.search_max_pages_per_round,
            search_max_attempts_per_round=settings.search_max_attempts_per_round,
            search_no_progress_limit=settings.search_no_progress_limit,
            scoring_timeout_seconds=settings.scoring_timeout_seconds,
            prf_probe_phrase_proposal_timeout_seconds=settings.prf_probe_phrase_proposal_timeout_seconds,
        )


def runtime_constraints_from_run_state(run_state: RunState | None) -> tuple[RuntimeConstraint, ...]:
    if run_state is None:
        return ()

    constraints: list[RuntimeConstraint] = []
    seen: set[tuple[str, str, str, bool]] = set()
    for round_state in run_state.round_history:
        for constraint in round_state.retrieval_plan.runtime_only_constraints:
            key = (
                constraint.field,
                constraint.normalized_value.model_dump_json()
                if isinstance(constraint.normalized_value, BaseModel)
                else repr(constraint.normalized_value),
                constraint.source,
                constraint.blocking,
            )
            if key in seen:
                continue
            seen.add(key)
            constraints.append(constraint)
    return tuple(constraints)
