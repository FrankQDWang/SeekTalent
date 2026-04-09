from __future__ import annotations

import json
from hashlib import sha1
from typing import Any

from pydantic_ai import Agent, ModelRetry, NativeOutput

from seektalent.bootstrap_llm import OUTPUT_RETRIES, RETRIES, STRICT_MODEL_SETTINGS
from seektalent.frontier_ops import generate_search_controller_decision
from seektalent.models import (
    LLMCallAuditSnapshot,
    SearchControllerContext_t,
    SearchControllerDecisionDraft_t,
)


SEARCH_CONTROLLER_DECISION_INSTRUCTIONS = """
Generate a strict structured controller decision draft for the active frontier node.
Use only the provided controller context.
Do not invent unsupported operators or donor ids outside the provided candidate list.
""".strip()


def _build_agent(*, model: Any | None) -> Agent:
    return Agent(
        model,
        output_type=NativeOutput(SearchControllerDecisionDraft_t, strict=True),
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        builtin_tools=(),
        toolsets=(),
        system_prompt=(),
        model_settings=STRICT_MODEL_SETTINGS,
    )


def _test_model_outputs(model: Any | None) -> list[dict[str, object]] | None:
    if getattr(model, "model_name", None) != "test":
        return None
    payload = getattr(model, "custom_output_args", None)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise ValueError("test_model_requires_custom_output_args")


def _audit_snapshot(
    *,
    model: Any | None,
    validator_retry_count: int,
) -> LLMCallAuditSnapshot:
    return LLMCallAuditSnapshot(
        output_mode="NativeOutput(strict=True)",
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        validator_retry_count=validator_retry_count,
        model_name=_model_name(model),
        instruction_id_or_hash=sha1(
            SEARCH_CONTROLLER_DECISION_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
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


def _validate_controller_draft(
    draft: SearchControllerDecisionDraft_t,
    context: SearchControllerContext_t,
) -> SearchControllerDecisionDraft_t:
    normalized = generate_search_controller_decision(context, draft)
    if normalized.action == "stop":
        return draft
    if normalized.selected_operator_name != "crossover_compose":
        additional_terms = normalized.operator_args.get("additional_terms")
        if isinstance(additional_terms, list) and additional_terms:
            return draft
        raise ModelRetry("search_cts requires non-empty additional_terms after normalization")
    donor_frontier_node_id = normalized.operator_args.get("donor_frontier_node_id")
    shared_anchor_terms = normalized.operator_args.get("shared_anchor_terms")
    donor_terms_used = normalized.operator_args.get("donor_terms_used")
    if donor_frontier_node_id is None:
        raise ModelRetry("crossover_compose requires a legal donor_frontier_node_id")
    if isinstance(shared_anchor_terms, list) and shared_anchor_terms:
        return draft
    if isinstance(donor_terms_used, list) and donor_terms_used:
        return draft
    raise ModelRetry("crossover_compose requires materializable shared_anchor_terms or donor_terms_used")


async def request_search_controller_decision_draft(
    context: SearchControllerContext_t,
    *,
    model: Any | None = None,
) -> tuple[SearchControllerDecisionDraft_t, LLMCallAuditSnapshot]:
    test_outputs = _test_model_outputs(model)
    if test_outputs is not None:
        validator_retry_count = 0
        for index, payload in enumerate(test_outputs):
            draft = SearchControllerDecisionDraft_t.model_validate(payload)
            try:
                return _validate_controller_draft(draft, context), _audit_snapshot(
                    model=model,
                    validator_retry_count=validator_retry_count,
                )
            except ModelRetry:
                validator_retry_count += 1
                if validator_retry_count > 1 or index == len(test_outputs) - 1:
                    raise
        raise ValueError("test_model_requires_custom_output_args")

    validator_retry_count = 0
    active_agent = _build_agent(model=model)

    @active_agent.output_validator
    def _output_validator(
        draft: SearchControllerDecisionDraft_t,
    ) -> SearchControllerDecisionDraft_t:
        nonlocal validator_retry_count
        try:
            return _validate_controller_draft(draft, context)
        except ModelRetry:
            validator_retry_count += 1
            raise

    result = await active_agent.run(
        json.dumps(context.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        message_history=None,
        instructions=SEARCH_CONTROLLER_DECISION_INSTRUCTIONS,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return SearchControllerDecisionDraft_t.model_validate(result.output), _audit_snapshot(
        model=model,
        validator_retry_count=validator_retry_count,
    )


__all__ = [
    "SEARCH_CONTROLLER_DECISION_INSTRUCTIONS",
    "request_search_controller_decision_draft",
]
