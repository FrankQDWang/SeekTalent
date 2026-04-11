from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelRetry

from seektalent.frontier_ops import generate_search_controller_decision
from seektalent.llm_config import build_llm_binding
from seektalent.models import (
    LLMCallAudit,
    RewriteFitnessWeights,
    SearchControllerContext_t,
    SearchControllerDecisionDraft_t,
    stable_deduplicate,
)
from seektalent.prompt_surfaces import (
    OUTPUT_RETRIES,
    RETRIES,
    STRICT_MODEL_SETTINGS,
    build_controller_prompt_surface,
    build_llm_call_audit,
)
from seektalent.prompts import load_prompt

SEARCH_CONTROLLER_DECISION_PROMPT = load_prompt("search_controller_decision.md")


def _test_model_outputs(model: Any | None) -> list[dict[str, object]] | None:
    if getattr(model, "model_name", None) != "test":
        return None
    payload = getattr(model, "custom_output_args", None)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise ValueError("test_model_requires_custom_output_args")


def _validate_controller_draft(
    draft: SearchControllerDecisionDraft_t,
    context: SearchControllerContext_t,
    rewrite_fitness_weights: RewriteFitnessWeights,
) -> SearchControllerDecisionDraft_t:
    try:
        normalized = generate_search_controller_decision(
            context,
            draft,
            rewrite_fitness_weights,
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    if normalized.action == "stop":
        return draft
    if normalized.selected_operator_name != "crossover_compose":
        query_terms = normalized.operator_args.get("query_terms")
        query_terms = (
            [term for term in query_terms if isinstance(term, str)]
            if isinstance(query_terms, list)
            else []
        )
        if query_terms:
            return draft
        raise ModelRetry("search_cts requires materializable non-empty query_terms")
    donor_frontier_node_id = normalized.operator_args.get("donor_frontier_node_id")
    shared_anchor_terms = normalized.operator_args.get("shared_anchor_terms")
    donor_terms_used = normalized.operator_args.get("donor_terms_used")
    if donor_frontier_node_id is None:
        raise ModelRetry("crossover_compose requires a legal donor_frontier_node_id")
    if not isinstance(shared_anchor_terms, list) or not shared_anchor_terms:
        raise ModelRetry("crossover_compose requires materializable shared_anchor_terms")
    query_terms = stable_deduplicate(
        [term for term in shared_anchor_terms if isinstance(term, str)]
        + (
            [term for term in donor_terms_used if isinstance(term, str)]
            if isinstance(donor_terms_used, list)
            else []
        )
    )
    if query_terms:
        return draft
    raise ModelRetry("crossover_compose requires materializable non-empty query terms")


async def request_search_controller_decision_draft(
    context: SearchControllerContext_t,
    *,
    rewrite_fitness_weights: RewriteFitnessWeights,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[SearchControllerDecisionDraft_t, LLMCallAudit]:
    prompt_surface = build_controller_prompt_surface(
        context,
        instructions_text=SEARCH_CONTROLLER_DECISION_PROMPT,
    )
    binding = build_llm_binding(
        SearchControllerDecisionDraft_t,
        callpoint="search_controller_decision",
        model=model,
        env_file=env_file,
    )
    test_outputs = _test_model_outputs(model)
    if test_outputs is not None:
        validator_retry_count = 0
        for index, payload in enumerate(test_outputs):
            draft = SearchControllerDecisionDraft_t.model_validate(payload)
            try:
                return _validate_controller_draft(
                    draft,
                    context,
                    rewrite_fitness_weights,
                ), build_llm_call_audit(
                    model=model,
                    prompt_surface=prompt_surface,
                    validator_retry_count=validator_retry_count,
                    output_mode=binding.audit_output_mode,
                    model_name=binding.audit_model_name,
                )
            except ModelRetry:
                validator_retry_count += 1
                if validator_retry_count > 1 or index == len(test_outputs) - 1:
                    raise
        raise ValueError("test_model_requires_custom_output_args")

    validator_retry_count = 0
    active_agent = Agent(
        binding.model,
        output_type=binding.output_type,
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        builtin_tools=(),
        toolsets=(),
        system_prompt=(),
        model_settings=STRICT_MODEL_SETTINGS,
    )

    @active_agent.output_validator
    def _output_validator(
        draft: SearchControllerDecisionDraft_t,
    ) -> SearchControllerDecisionDraft_t:
        nonlocal validator_retry_count
        try:
            return _validate_controller_draft(draft, context, rewrite_fitness_weights)
        except ModelRetry:
            validator_retry_count += 1
            raise

    result = await active_agent.run(
        prompt_surface.input_text,
        message_history=None,
        instructions=SEARCH_CONTROLLER_DECISION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return SearchControllerDecisionDraft_t.model_validate(result.output), build_llm_call_audit(
        model=model,
        prompt_surface=prompt_surface,
        validator_retry_count=validator_retry_count,
        output_mode=binding.audit_output_mode,
        model_name=binding.audit_model_name,
    )


__all__ = [
    "SEARCH_CONTROLLER_DECISION_PROMPT",
    "request_search_controller_decision_draft",
]
