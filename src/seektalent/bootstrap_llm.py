from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelRetry

from seektalent.llm_config import build_llm_binding
from seektalent.models import (
    BootstrapKeywordDraft,
    BootstrapRoutingResult,
    DomainKnowledgePack,
    LLMCallAudit,
    RequirementExtractionDraft,
    RequirementSheet,
    SearchInputTruth,
    stable_deduplicate,
)
from seektalent.prompt_surfaces import (
    OUTPUT_RETRIES,
    RETRIES,
    STRICT_MODEL_SETTINGS,
    build_bootstrap_keyword_generation_prompt_surface,
    build_llm_call_audit,
    build_requirement_extraction_prompt_surface,
)
from seektalent.prompts import load_prompt

REQUIREMENT_EXTRACTION_PROMPT = load_prompt("bootstrap_requirement_extraction.md")
BOOTSTRAP_KEYWORD_GENERATION_PROMPT = load_prompt("bootstrap_keyword_generation.md")

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


async def request_requirement_extraction_draft(
    input_truth: SearchInputTruth,
    *,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[RequirementExtractionDraft, LLMCallAudit]:
    prompt_surface = build_requirement_extraction_prompt_surface(
        input_truth,
        instructions_text=REQUIREMENT_EXTRACTION_PROMPT,
    )
    binding = build_llm_binding(
        RequirementExtractionDraft,
        callpoint="requirement_extraction",
        model=model,
        env_file=env_file,
    )
    test_outputs = _test_model_outputs(RequirementExtractionDraft, model=model)
    if test_outputs is not None:
        return test_outputs[0], build_llm_call_audit(
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
        instructions=REQUIREMENT_EXTRACTION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return RequirementExtractionDraft.model_validate(result.output), build_llm_call_audit(
        model=model,
        prompt_surface=prompt_surface,
        validator_retry_count=0,
        output_mode=binding.audit_output_mode,
        model_name=binding.audit_model_name,
    )


async def request_bootstrap_keyword_draft(
    requirement_sheet: RequirementSheet,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
    *,
    max_seed_terms: int,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[BootstrapKeywordDraft, LLMCallAudit]:
    prompt_surface = build_bootstrap_keyword_generation_prompt_surface(
        requirement_sheet,
        routing_result,
        selected_knowledge_packs,
        instructions_text=BOOTSTRAP_KEYWORD_GENERATION_PROMPT,
    )
    binding = build_llm_binding(
        BootstrapKeywordDraft,
        callpoint="bootstrap_keyword_generation",
        model=model,
        env_file=env_file,
    )
    test_outputs = _test_model_outputs(BootstrapKeywordDraft, model=model)
    if test_outputs is not None:
        validator_retry_count = 0
        for index, draft in enumerate(test_outputs):
            try:
                return _validate_bootstrap_keyword_draft(
                    draft,
                    routing_result=routing_result,
                    selected_knowledge_packs=selected_knowledge_packs,
                    max_seed_terms=max_seed_terms,
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
    def _output_validator(draft: BootstrapKeywordDraft) -> BootstrapKeywordDraft:
        nonlocal validator_retry_count
        try:
            return _validate_bootstrap_keyword_draft(
                draft,
                routing_result=routing_result,
                selected_knowledge_packs=selected_knowledge_packs,
                max_seed_terms=max_seed_terms,
            )
        except ModelRetry:
            validator_retry_count += 1
            raise

    result = await active_agent.run(
        prompt_surface.input_text,
        message_history=None,
        instructions=BOOTSTRAP_KEYWORD_GENERATION_PROMPT,
        builtin_tools=(),
        toolsets=(),
        infer_name=False,
    )
    return BootstrapKeywordDraft.model_validate(result.output), build_llm_call_audit(
        model=model,
        prompt_surface=prompt_surface,
        validator_retry_count=validator_retry_count,
        output_mode=binding.audit_output_mode,
        model_name=binding.audit_model_name,
    )


def _validate_bootstrap_keyword_draft(
    draft: BootstrapKeywordDraft,
    *,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
    max_seed_terms: int,
) -> BootstrapKeywordDraft:
    if not 5 <= len(draft.candidate_seeds) <= 8:
        raise ModelRetry("bootstrap candidate_seeds must contain 5-8 items")

    selected_pack_ids = {pack.knowledge_pack_id for pack in selected_knowledge_packs}
    seen_intents = {seed.intent_type for seed in draft.candidate_seeds}
    if "core_precision" not in seen_intents:
        raise ModelRetry("bootstrap candidate_seeds must include core_precision")
    if "relaxed_floor" not in seen_intents:
        raise ModelRetry("bootstrap candidate_seeds must include relaxed_floor")

    normalized_seeds = []
    single_pack_bridge_count = 0
    multi_pack_bridge_count = 0
    for seed in draft.candidate_seeds:
        normalized_keywords = _normalized_keywords(seed.keywords, max_seed_terms)
        if not normalized_keywords:
            raise ModelRetry("bootstrap seed keywords must be materializable")
        source_pack_ids = stable_deduplicate(list(seed.source_knowledge_pack_ids))
        if any(pack_id not in selected_pack_ids for pack_id in source_pack_ids):
            raise ModelRetry("bootstrap seed source_knowledge_pack_ids must be selected packs")
        if routing_result.routing_mode == "generic_fallback":
            if seed.intent_type == "pack_bridge":
                raise ModelRetry("generic fallback cannot emit pack_bridge")
            if source_pack_ids:
                raise ModelRetry("generic fallback cannot reference knowledge packs")
        elif routing_result.routing_mode in {"explicit_pack", "inferred_single_pack"}:
            if seed.intent_type == "pack_bridge" and len(source_pack_ids) != 1:
                raise ModelRetry("single-pack pack_bridge must reference exactly one pack")
        elif routing_result.routing_mode == "inferred_multi_pack":
            if seed.intent_type == "pack_bridge":
                if len(source_pack_ids) not in {1, 2}:
                    raise ModelRetry("multi-pack pack_bridge must reference one or two packs")
                if len(source_pack_ids) == 1:
                    single_pack_bridge_count += 1
                if len(source_pack_ids) == 2:
                    multi_pack_bridge_count += 1
        normalized_seeds.append(seed.model_copy(update={"keywords": normalized_keywords}))

    if routing_result.routing_mode == "generic_fallback" and "vocabulary_bridge" not in seen_intents:
        raise ModelRetry("generic fallback must include vocabulary_bridge")
    if routing_result.routing_mode in {"explicit_pack", "inferred_single_pack"} and "pack_bridge" not in seen_intents:
        raise ModelRetry("single-pack routing must include pack_bridge")
    if routing_result.routing_mode == "inferred_multi_pack":
        if single_pack_bridge_count == 0:
            raise ModelRetry("multi-pack routing must include single-pack pack_bridge")
        if multi_pack_bridge_count == 0:
            raise ModelRetry("multi-pack routing must include two-pack pack_bridge")

    return draft.model_copy(update={"candidate_seeds": normalized_seeds})


def _normalized_keywords(keywords: list[str], max_seed_terms: int) -> list[str]:
    return stable_deduplicate(list(keywords))[:max_seed_terms]


__all__ = [
    "BOOTSTRAP_KEYWORD_GENERATION_PROMPT",
    "OUTPUT_RETRIES",
    "REQUIREMENT_EXTRACTION_PROMPT",
    "RETRIES",
    "STRICT_MODEL_SETTINGS",
    "request_bootstrap_keyword_draft",
    "request_requirement_extraction_draft",
]
