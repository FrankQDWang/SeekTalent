from __future__ import annotations

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
    output_type: type[OutputT],
    system_prompt: str,
    user_prompt: str,
) -> tuple[OutputT, ProviderUsageSnapshot | None]:
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
    result = await agent.run(user_prompt)
    return result.output, provider_usage_from_result(result)


async def repair_requirement_draft(
    settings: AppSettings,
    prompt: LoadedPrompt,
    input_truth: InputTruth,
    draft: RequirementExtractionDraft,
    reason: str,
) -> tuple[RequirementExtractionDraft, ProviderUsageSnapshot | None]:
    user_prompt = "\n\n".join(
        [
            json_block(
                "REPAIR_REASON",
                {"reason": reason},
            ),
            json_block(
                "SOURCE_PROMPT",
                {
                    "name": prompt.name,
                    "sha256": prompt.sha256,
                    "content": prompt.content,
                },
            ),
            json_block("INPUT_TRUTH", input_truth.model_dump(mode="json")),
            json_block("BROKEN_DRAFT", draft.model_dump(mode="json")),
        ]
    )
    return await _repair_with_model(
        settings,
        output_type=RequirementExtractionDraft,
        system_prompt=(
            "Repair one RequirementExtractionDraft. "
            "Return complete JSON that preserves source intent and fixes the reported issue."
        ),
        user_prompt=user_prompt,
    )


async def repair_controller_decision(
    settings: AppSettings,
    prompt: LoadedPrompt,
    source_user_prompt: str,
    decision: ControllerDecision,
    reason: str,
) -> tuple[ControllerDecision, ProviderUsageSnapshot | None]:
    user_prompt = "\n\n".join(
        [
            json_block(
                "REPAIR_REASON",
                {"reason": reason},
            ),
            json_block(
                "SOURCE_PROMPT",
                {
                    "name": prompt.name,
                    "sha256": prompt.sha256,
                    "content": prompt.content,
                },
            ),
            json_block("SOURCE_USER_PROMPT", {"content": source_user_prompt}),
            json_block("CURRENT_DECISION", decision.model_dump(mode="json")),
        ]
    )
    return await _repair_with_model(
        settings,
        output_type=ControllerDecision,
        system_prompt=(
            "Repair one ControllerDecision. "
            "Return complete JSON that preserves intent and fixes the reported issue."
        ),
        user_prompt=user_prompt,
    )


async def repair_reflection_draft(
    settings: AppSettings,
    prompt: LoadedPrompt,
    source_user_prompt: str,
    draft: ReflectionAdviceDraft,
    reason: str,
) -> tuple[ReflectionAdviceDraft, ProviderUsageSnapshot | None]:
    user_prompt = "\n\n".join(
        [
            json_block(
                "REPAIR_REASON",
                {"reason": reason},
            ),
            json_block("SOURCE_USER_PROMPT", {"content": source_user_prompt}),
            json_block("CURRENT_DRAFT", draft.model_dump(mode="json")),
        ]
    )
    return await _repair_with_model(
        settings,
        output_type=ReflectionAdviceDraft,
        system_prompt=prompt.content,
        user_prompt=user_prompt,
    )
