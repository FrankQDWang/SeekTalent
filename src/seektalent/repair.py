from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any
from typing import TypeVar, cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import (
    ControllerDecision,
    InputTruth,
    ReflectionAdviceDraft,
    RequirementExtractionDraft,
)
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result

OutputT = TypeVar("OutputT")


async def _repair_with_model(
    settings: AppSettings,
    *,
    prompt_name: str,
    user_payload: dict[str, Any],
    output_type: type[OutputT],
    system_prompt: str,
    user_prompt: str,
) -> tuple[OutputT, ProviderUsageSnapshot | None, dict[str, Any]]:
    model_id = settings.structured_repair_model
    model = build_model(model_id)
    agent = cast(Agent[None, OutputT], Agent(
        model=model,
        output_type=build_output_spec(model_id, model, output_type),
        system_prompt=system_prompt,
        model_settings=build_model_settings(
            settings,
            model_id,
            reasoning_effort=settings.structured_repair_reasoning_effort,
            enable_thinking=False,
        ),
        retries=0,
        output_retries=2,
    ))
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started_clock = perf_counter()
    result = await agent.run(user_prompt)
    output = result.output
    usage = provider_usage_from_result(result)
    return (
        output,
        usage,
        {
            "stage": prompt_name,
            "prompt_name": prompt_name,
            "model_id": model_id,
            "user_payload": user_payload,
            "user_prompt_text": user_prompt,
            "structured_output": output.model_dump(mode="json") if hasattr(output, "model_dump") else output,
            "started_at": started_at,
            "latency_ms": max(1, int((perf_counter() - started_clock) * 1000)),
            "status": "succeeded",
            "retries": 0,
            "output_retries": 2,
            "provider_usage": usage,
        },
    )


async def repair_requirement_draft(
    settings: AppSettings,
    prompt: LoadedPrompt,
    repair_prompt: LoadedPrompt,
    input_truth: InputTruth,
    draft: RequirementExtractionDraft,
    reason: str,
) -> tuple[RequirementExtractionDraft, ProviderUsageSnapshot | None, dict[str, Any]]:
    user_payload = {
        "REPAIR_REASON": {"reason": reason},
        "SOURCE_PROMPT": {
            "name": prompt.name,
            "sha256": prompt.sha256,
            "content": prompt.content,
        },
        "INPUT_TRUTH": input_truth.model_dump(mode="json"),
        "BROKEN_DRAFT": draft.model_dump(mode="json"),
    }
    user_prompt = "\n\n".join([json_block(title, payload) for title, payload in user_payload.items()])
    return await _repair_with_model(
        settings,
        prompt_name=repair_prompt.name,
        user_payload=user_payload,
        output_type=RequirementExtractionDraft,
        system_prompt=repair_prompt.content,
        user_prompt=user_prompt,
    )


async def repair_controller_decision(
    settings: AppSettings,
    prompt: LoadedPrompt,
    repair_prompt: LoadedPrompt,
    source_user_prompt: str,
    decision: ControllerDecision,
    reason: str,
) -> tuple[ControllerDecision, ProviderUsageSnapshot | None, dict[str, Any]]:
    user_payload = {
        "REPAIR_REASON": {"reason": reason},
        "SOURCE_PROMPT": {
            "name": prompt.name,
            "sha256": prompt.sha256,
            "content": prompt.content,
        },
        "SOURCE_USER_PROMPT": {"content": source_user_prompt},
        "CURRENT_DECISION": decision.model_dump(mode="json"),
    }
    user_prompt = "\n\n".join([json_block(title, payload) for title, payload in user_payload.items()])
    return await _repair_with_model(
        settings,
        prompt_name=repair_prompt.name,
        user_payload=user_payload,
        output_type=ControllerDecision,
        system_prompt=repair_prompt.content,
        user_prompt=user_prompt,
    )


async def repair_reflection_draft(
    settings: AppSettings,
    prompt: LoadedPrompt,
    repair_prompt: LoadedPrompt,
    source_user_prompt: str,
    draft: ReflectionAdviceDraft,
    reason: str,
) -> tuple[ReflectionAdviceDraft, ProviderUsageSnapshot | None, dict[str, Any]]:
    user_payload = {
        "REPAIR_REASON": {"reason": reason},
        "SOURCE_PROMPT": {
            "name": prompt.name,
            "sha256": prompt.sha256,
            "content": prompt.content,
        },
        "SOURCE_USER_PROMPT": {"content": source_user_prompt},
        "CURRENT_DRAFT": draft.model_dump(mode="json"),
    }
    user_prompt = "\n\n".join([json_block(title, payload) for title, payload in user_payload.items()])
    return await _repair_with_model(
        settings,
        prompt_name=repair_prompt.name,
        user_payload=user_payload,
        output_type=ReflectionAdviceDraft,
        system_prompt=repair_prompt.content,
        user_prompt=user_prompt,
    )
