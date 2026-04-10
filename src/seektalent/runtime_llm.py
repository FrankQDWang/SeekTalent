from __future__ import annotations

import json
from hashlib import sha1
from typing import Any

from pydantic_ai import Agent, NativeOutput

from seektalent.bootstrap_llm import OUTPUT_RETRIES, RETRIES, STRICT_MODEL_SETTINGS
from seektalent.models import (
    BranchEvaluationDraft_t,
    FrontierState_t,
    FrontierState_t1,
    LLMCallAuditSnapshot,
    RequirementSheet,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchRunSummaryDraft_t,
    SearchScoringResult_t,
)
from seektalent.prompts import load_prompt


BRANCH_OUTCOME_EVALUATION_PROMPT = load_prompt("branch_outcome_evaluation.md")
SEARCH_RUN_FINALIZATION_PROMPT = load_prompt("search_run_finalization.md")


def _build_agent(output_type: type[BranchEvaluationDraft_t] | type[SearchRunSummaryDraft_t], *, model: Any | None) -> Agent:
    return Agent(
        model,
        output_type=NativeOutput(output_type, strict=True),
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        builtin_tools=(),
        toolsets=(),
        system_prompt=(),
        model_settings=STRICT_MODEL_SETTINGS,
    )


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


def _audit_snapshot(*, model: Any | None, instructions: str) -> LLMCallAuditSnapshot:
    return LLMCallAuditSnapshot(
        output_mode="NativeOutput(strict=True)",
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        validator_retry_count=0,
        model_name=_model_name(model),
        instruction_id_or_hash=sha1(instructions.encode("utf-8")).hexdigest(),
        message_history_mode="fresh",
        tools_enabled=False,
        model_settings_snapshot={**STRICT_MODEL_SETTINGS, "native_output_strict": True},
    )


def _model_name(model: Any | None) -> str:
    if model is None:
        return "default"
    for attr in ("model_name", "name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return type(model).__name__


async def request_branch_evaluation_draft(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t,
    plan: SearchExecutionPlan_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
    *,
    model: Any | None = None,
) -> tuple[BranchEvaluationDraft_t, LLMCallAuditSnapshot]:
    test_output = _test_model_output(BranchEvaluationDraft_t, model=model)
    if test_output is not None:
        return test_output, _audit_snapshot(
            model=model,
            instructions=BRANCH_OUTCOME_EVALUATION_PROMPT,
        )
    parent_node = frontier_state.frontier_nodes.get(
        plan.child_frontier_node_stub.parent_frontier_node_id
    )
    if parent_node is None:
        raise ValueError(
            f"unknown_parent_frontier_node_id: {plan.child_frontier_node_stub.parent_frontier_node_id}"
        )
    active_agent = _build_agent(BranchEvaluationDraft_t, model=model)
    packet = json.dumps(
        {
            "must_have_capabilities": requirement_sheet.must_have_capabilities,
            "parent_frontier_node_id": parent_node.frontier_node_id,
            "previous_node_shortlist_candidate_ids": parent_node.node_shortlist_candidate_ids,
            "donor_frontier_node_id": plan.child_frontier_node_stub.donor_frontier_node_id,
            "knowledge_pack_ids": plan.knowledge_pack_ids,
            "query_terms": plan.query_terms,
            "semantic_hash": plan.semantic_hash,
            "search_page_statistics": execution_result.search_page_statistics.model_dump(
                mode="json"
            ),
            "node_shortlist_candidate_ids": scoring_result.node_shortlist_candidate_ids,
            "top_three_statistics": scoring_result.top_three_statistics.model_dump(
                mode="json"
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    result = await active_agent.run(
        packet,
        message_history=None,
        instructions=BRANCH_OUTCOME_EVALUATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return BranchEvaluationDraft_t.model_validate(result.output), _audit_snapshot(
        model=model,
        instructions=BRANCH_OUTCOME_EVALUATION_PROMPT,
    )


async def request_search_run_summary_draft(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t1,
    stop_reason: str,
    *,
    model: Any | None = None,
) -> tuple[SearchRunSummaryDraft_t, LLMCallAuditSnapshot]:
    test_output = _test_model_output(SearchRunSummaryDraft_t, model=model)
    if test_output is not None:
        return test_output, _audit_snapshot(
            model=model,
            instructions=SEARCH_RUN_FINALIZATION_PROMPT,
        )
    active_agent = _build_agent(SearchRunSummaryDraft_t, model=model)
    packet = json.dumps(
        {
            "role_title": requirement_sheet.role_title,
            "must_have_capabilities": requirement_sheet.must_have_capabilities,
            "hard_constraints": requirement_sheet.hard_constraints.model_dump(mode="json"),
            "ranked_candidates": frontier_state.run_shortlist_candidate_ids,
            "stop_reason": stop_reason,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    result = await active_agent.run(
        packet,
        message_history=None,
        instructions=SEARCH_RUN_FINALIZATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return SearchRunSummaryDraft_t.model_validate(result.output), _audit_snapshot(
        model=model,
        instructions=SEARCH_RUN_FINALIZATION_PROMPT,
    )


__all__ = [
    "BRANCH_OUTCOME_EVALUATION_PROMPT",
    "SEARCH_RUN_FINALIZATION_PROMPT",
    "request_branch_evaluation_draft",
    "request_search_run_summary_draft",
]
