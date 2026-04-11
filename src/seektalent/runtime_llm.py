from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from seektalent.llm_config import build_llm_binding
from seektalent.models import (
    BranchEvaluationDraft_t,
    FrontierState_t,
    FrontierState_t1,
    LLMCallAudit,
    RequirementSheet,
    RuntimeBudgetState,
    SearchRoundArtifact,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchRunSummaryDraft_t,
    SearchScoringResult_t,
)
from seektalent.prompt_surfaces import (
    OUTPUT_RETRIES,
    RETRIES,
    STRICT_MODEL_SETTINGS,
    build_branch_evaluation_prompt_surface,
    build_llm_call_audit,
    build_search_run_finalization_prompt_surface,
)
from seektalent.prompts import load_prompt

BRANCH_OUTCOME_EVALUATION_PROMPT = load_prompt("branch_outcome_evaluation.md")
SEARCH_RUN_FINALIZATION_PROMPT = load_prompt("search_run_finalization.md")


def _test_model_output(
    output_type: type[BranchEvaluationDraft_t] | type[SearchRunSummaryDraft_t],
    *,
    model: Any | None,
) -> BranchEvaluationDraft_t | SearchRunSummaryDraft_t | None:
    if getattr(model, "model_name", None) != "test":
        return None
    payload = getattr(model, "custom_output_args", None)
    if not isinstance(payload, dict):
        raise ValueError("test_model_requires_custom_output_args")
    return output_type.model_validate(payload)


async def request_branch_evaluation_draft(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t,
    plan: SearchExecutionPlan_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
    runtime_budget_state: RuntimeBudgetState,
    *,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[BranchEvaluationDraft_t, LLMCallAudit]:
    parent_node = frontier_state.frontier_nodes.get(
        plan.child_frontier_node_stub.parent_frontier_node_id
    )
    if parent_node is None:
        raise ValueError(
            f"unknown_parent_frontier_node_id: {plan.child_frontier_node_stub.parent_frontier_node_id}"
        )
    prompt_surface = build_branch_evaluation_prompt_surface(
        requirement_sheet,
        parent_node,
        plan,
        execution_result,
        scoring_result,
        runtime_budget_state,
        instructions_text=BRANCH_OUTCOME_EVALUATION_PROMPT,
    )
    binding = build_llm_binding(
        BranchEvaluationDraft_t,
        callpoint="branch_outcome_evaluation",
        model=model,
        env_file=env_file,
    )
    test_output = _test_model_output(BranchEvaluationDraft_t, model=model)
    if test_output is not None:
        return test_output, build_llm_call_audit(
            model=model,
            prompt_surface=prompt_surface,
            validator_retry_count=0,
            output_mode=binding.audit_output_mode,
            model_name=binding.audit_model_name,
        )
    result = await Agent(
        binding.model,
        output_type=binding.output_type,
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        builtin_tools=(),
        toolsets=(),
        system_prompt=(),
        model_settings=STRICT_MODEL_SETTINGS,
    ).run(
        prompt_surface.input_text,
        message_history=None,
        instructions=BRANCH_OUTCOME_EVALUATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return BranchEvaluationDraft_t.model_validate(result.output), build_llm_call_audit(
        model=model,
        prompt_surface=prompt_surface,
        validator_retry_count=0,
        output_mode=binding.audit_output_mode,
        model_name=binding.audit_model_name,
    )


async def request_search_run_summary_draft(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t1,
    rounds: list[SearchRoundArtifact],
    stop_reason: str,
    *,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[SearchRunSummaryDraft_t, LLMCallAudit]:
    prompt_surface = build_search_run_finalization_prompt_surface(
        requirement_sheet,
        frontier_state,
        rounds,
        stop_reason,
        instructions_text=SEARCH_RUN_FINALIZATION_PROMPT,
    )
    binding = build_llm_binding(
        SearchRunSummaryDraft_t,
        callpoint="search_run_finalization",
        model=model,
        env_file=env_file,
    )
    test_output = _test_model_output(SearchRunSummaryDraft_t, model=model)
    if test_output is not None:
        return test_output, build_llm_call_audit(
            model=model,
            prompt_surface=prompt_surface,
            validator_retry_count=0,
            output_mode=binding.audit_output_mode,
            model_name=binding.audit_model_name,
        )
    result = await Agent(
        binding.model,
        output_type=binding.output_type,
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        builtin_tools=(),
        toolsets=(),
        system_prompt=(),
        model_settings=STRICT_MODEL_SETTINGS,
    ).run(
        prompt_surface.input_text,
        message_history=None,
        instructions=SEARCH_RUN_FINALIZATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return SearchRunSummaryDraft_t.model_validate(result.output), build_llm_call_audit(
        model=model,
        prompt_surface=prompt_surface,
        validator_retry_count=0,
        output_mode=binding.audit_output_mode,
        model_name=binding.audit_model_name,
    )


__all__ = [
    "BRANCH_OUTCOME_EVALUATION_PROMPT",
    "SEARCH_RUN_FINALIZATION_PROMPT",
    "request_branch_evaluation_draft",
    "request_search_run_summary_draft",
]
