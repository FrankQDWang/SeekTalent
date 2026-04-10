from __future__ import annotations

import json
from hashlib import sha1
from typing import Any

from pydantic_ai import Agent, ModelRetry, NativeOutput

from seektalent.models import (
    BootstrapKeywordDraft,
    BootstrapRoutingResult,
    DomainKnowledgePack,
    LLMCallAuditSnapshot,
    RequirementExtractionDraft,
    RequirementSheet,
    SearchInputTruth,
    stable_deduplicate,
)
from seektalent.prompts import load_prompt

REQUIREMENT_EXTRACTION_PROMPT = load_prompt("bootstrap_requirement_extraction.md")
BOOTSTRAP_KEYWORD_GENERATION_PROMPT = load_prompt("bootstrap_keyword_generation.md")

RETRIES = 0
OUTPUT_RETRIES = 1
STRICT_MODEL_SETTINGS = {
    "allow_text_output": False,
    "allow_image_output": False,
}


def _build_agent(
    output_type: type[RequirementExtractionDraft] | type[BootstrapKeywordDraft],
    *,
    model: Any | None,
) -> Agent:
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


def _test_model_outputs(
    output_type: type[RequirementExtractionDraft] | type[BootstrapKeywordDraft],
    *,
    model: Any | None,
) -> list[RequirementExtractionDraft | BootstrapKeywordDraft] | None:
    if getattr(model, "model_name", None) != "test":
        return None
    payload = getattr(model, "custom_output_args", None)
    if isinstance(payload, dict):
        return [output_type.model_validate(payload)]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return [output_type.model_validate(item) for item in payload]
    raise ValueError("test_model_requires_custom_output_args")


def _audit_snapshot(
    *,
    model: Any | None,
    instructions: str,
    validator_retry_count: int,
) -> LLMCallAuditSnapshot:
    return LLMCallAuditSnapshot(
        output_mode="NativeOutput(strict=True)",
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        validator_retry_count=validator_retry_count,
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


async def request_requirement_extraction_draft(
    input_truth: SearchInputTruth,
    *,
    model: Any | None = None,
) -> tuple[RequirementExtractionDraft, LLMCallAuditSnapshot]:
    test_outputs = _test_model_outputs(RequirementExtractionDraft, model=model)
    if test_outputs is not None:
        return test_outputs[0], _audit_snapshot(
            model=model,
            instructions=REQUIREMENT_EXTRACTION_PROMPT,
            validator_retry_count=0,
        )
    active_agent = _build_agent(RequirementExtractionDraft, model=model)
    result = await active_agent.run(
        input_truth.model_dump_json(),
        message_history=None,
        instructions=REQUIREMENT_EXTRACTION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return RequirementExtractionDraft.model_validate(result.output), _audit_snapshot(
        model=model,
        instructions=REQUIREMENT_EXTRACTION_PROMPT,
        validator_retry_count=0,
    )


async def request_bootstrap_keyword_draft(
    requirement_sheet: RequirementSheet,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
    *,
    model: Any | None = None,
) -> tuple[BootstrapKeywordDraft, LLMCallAuditSnapshot]:
    test_outputs = _test_model_outputs(BootstrapKeywordDraft, model=model)
    if test_outputs is not None:
        validator_retry_count = 0
        for index, draft in enumerate(test_outputs):
            try:
                return _validate_bootstrap_keyword_draft(
                    draft,
                    routing_result=routing_result,
                    selected_knowledge_packs=selected_knowledge_packs,
                ), _audit_snapshot(
                    model=model,
                    instructions=BOOTSTRAP_KEYWORD_GENERATION_PROMPT,
                    validator_retry_count=validator_retry_count,
                )
            except ModelRetry:
                validator_retry_count += 1
                if validator_retry_count > 1 or index == len(test_outputs) - 1:
                    raise
        raise ValueError("test_model_requires_custom_output_args")

    validator_retry_count = 0
    active_agent = _build_agent(BootstrapKeywordDraft, model=model)

    @active_agent.output_validator
    def _output_validator(draft: BootstrapKeywordDraft) -> BootstrapKeywordDraft:
        nonlocal validator_retry_count
        try:
            return _validate_bootstrap_keyword_draft(
                draft,
                routing_result=routing_result,
                selected_knowledge_packs=selected_knowledge_packs,
            )
        except ModelRetry:
            validator_retry_count += 1
            raise

    packet = json.dumps(
        _bootstrap_keyword_generation_packet(
            requirement_sheet,
            routing_result,
            selected_knowledge_packs,
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
    result = await active_agent.run(
        packet,
        message_history=None,
        instructions=BOOTSTRAP_KEYWORD_GENERATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return BootstrapKeywordDraft.model_validate(result.output), _audit_snapshot(
        model=model,
        instructions=BOOTSTRAP_KEYWORD_GENERATION_PROMPT,
        validator_retry_count=validator_retry_count,
    )


def _bootstrap_keyword_generation_packet(
    requirement_sheet: RequirementSheet,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
) -> dict[str, object]:
    return {
        "requirement": {
            "role_title": requirement_sheet.role_title,
            "role_summary": requirement_sheet.role_summary,
            "must_have_capabilities": list(requirement_sheet.must_have_capabilities),
            "preferred_capabilities": list(requirement_sheet.preferred_capabilities),
            "exclusion_signals": list(requirement_sheet.exclusion_signals),
            "hard_constraints": requirement_sheet.hard_constraints.model_dump(mode="json"),
        },
        "routing": {
            "routing_mode": routing_result.routing_mode,
            "selected_knowledge_pack_ids": list(routing_result.selected_knowledge_pack_ids),
        },
        "packs": [
            {
                "knowledge_pack_id": pack.knowledge_pack_id,
                "label": pack.label,
                "domain_summary": pack.routing_text,
                "positive_hints": list(pack.include_keywords),
                "negative_hints": list(pack.exclude_keywords),
            }
            for pack in selected_knowledge_packs
        ],
    }


def _validate_bootstrap_keyword_draft(
    draft: BootstrapKeywordDraft,
    *,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
) -> BootstrapKeywordDraft:
    if not 5 <= len(draft.candidate_seeds) <= 8:
        raise ModelRetry("bootstrap candidate_seeds must contain 5-8 items")

    selected_pack_ids = {pack.knowledge_pack_id for pack in selected_knowledge_packs}
    seen_intents = {seed.intent_type for seed in draft.candidate_seeds}
    if "core_precision" not in seen_intents:
        raise ModelRetry("bootstrap candidate_seeds must include core_precision")
    if "relaxed_floor" not in seen_intents:
        raise ModelRetry("bootstrap candidate_seeds must include relaxed_floor")

    for seed in draft.candidate_seeds:
        if not _normalized_keywords(seed.keywords):
            raise ModelRetry("bootstrap seed keywords must be materializable")
        source_pack_ids = stable_deduplicate(list(seed.source_knowledge_pack_ids))
        if any(pack_id not in selected_pack_ids for pack_id in source_pack_ids):
            raise ModelRetry("bootstrap seed source_knowledge_pack_ids must be selected packs")
        if routing_result.routing_mode == "generic_fallback":
            if seed.intent_type in {"pack_expansion", "cross_pack_bridge"}:
                raise ModelRetry("generic fallback cannot emit pack expansion intents")
            if source_pack_ids:
                raise ModelRetry("generic fallback cannot reference knowledge packs")
        elif routing_result.routing_mode in {"explicit_pack", "inferred_single_pack"}:
            if seed.intent_type == "cross_pack_bridge":
                raise ModelRetry("single-pack routing cannot emit cross_pack_bridge")
            if seed.intent_type == "pack_expansion" and len(source_pack_ids) != 1:
                raise ModelRetry("single-pack pack_expansion must reference exactly one pack")
        elif routing_result.routing_mode == "inferred_multi_pack":
            if seed.intent_type == "cross_pack_bridge" and len(source_pack_ids) != 2:
                raise ModelRetry("multi-pack cross_pack_bridge must reference exactly two packs")

    if routing_result.routing_mode == "generic_fallback" and "generic_expansion" not in seen_intents:
        raise ModelRetry("generic fallback must include generic_expansion")
    if routing_result.routing_mode in {"explicit_pack", "inferred_single_pack"} and "pack_expansion" not in seen_intents:
        raise ModelRetry("single-pack routing must include pack_expansion")
    if routing_result.routing_mode == "inferred_multi_pack":
        if "pack_expansion" not in seen_intents:
            raise ModelRetry("multi-pack routing must include pack_expansion")
        if "cross_pack_bridge" not in seen_intents:
            raise ModelRetry("multi-pack routing must include cross_pack_bridge")

    return draft


def _normalized_keywords(keywords: list[str]) -> list[str]:
    return stable_deduplicate(list(keywords))[:4]


__all__ = [
    "BOOTSTRAP_KEYWORD_GENERATION_PROMPT",
    "REQUIREMENT_EXTRACTION_PROMPT",
    "_bootstrap_keyword_generation_packet",
    "request_bootstrap_keyword_draft",
    "request_requirement_extraction_draft",
]
