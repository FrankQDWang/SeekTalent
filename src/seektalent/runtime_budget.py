from __future__ import annotations

from seektalent.models import RuntimeBudgetState, RuntimeSearchBudget


MIN_ROUND_BUDGET = 5
MAX_ROUND_BUDGET = 12


def resolve_runtime_search_budget(
    runtime_search_budget: RuntimeSearchBudget,
    requested_round_budget: int | None,
) -> RuntimeSearchBudget:
    initial_round_budget = runtime_search_budget.initial_round_budget
    if requested_round_budget is not None:
        initial_round_budget = requested_round_budget
    initial_round_budget = min(
        MAX_ROUND_BUDGET,
        max(MIN_ROUND_BUDGET, initial_round_budget),
    )
    return runtime_search_budget.model_copy(
        update={"initial_round_budget": initial_round_budget}
    )


def build_runtime_budget_state(
    *,
    initial_round_budget: int,
    runtime_round_index: int,
    remaining_budget: int,
) -> RuntimeBudgetState:
    normalized_initial_budget = max(1, initial_round_budget)
    phase_progress = min(
        1.0,
        max(0.0, runtime_round_index / max(1, normalized_initial_budget - 1)),
    )
    used_rounds = min(
        normalized_initial_budget,
        max(0, normalized_initial_budget - remaining_budget),
    )
    used_ratio = used_rounds / normalized_initial_budget
    return RuntimeBudgetState(
        initial_round_budget=initial_round_budget,
        runtime_round_index=runtime_round_index,
        remaining_budget=remaining_budget,
        used_ratio=used_ratio,
        remaining_ratio=max(0.0, min(1.0, remaining_budget / normalized_initial_budget)),
        phase_progress=phase_progress,
        search_phase=_search_phase(phase_progress),
        near_budget_end=used_ratio >= 0.8,
    )


def _search_phase(phase_progress: float) -> str:
    if phase_progress < 0.34:
        return "explore"
    if phase_progress < 0.67:
        return "balance"
    return "harvest"


__all__ = [
    "MAX_ROUND_BUDGET",
    "MIN_ROUND_BUDGET",
    "build_runtime_budget_state",
    "resolve_runtime_search_budget",
]
