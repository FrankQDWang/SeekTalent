from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from agents import Agent, AsyncOpenAI, ModelSettings, OpenAIChatCompletionsModel, Runner
from pydantic import BaseModel, ConfigDict, Field, model_validator

from seektalent.config import AppSettings
from seektalent.llm import (
    ResolvedTextModelConfig,
    build_provider_request_policy,
    resolve_stage_model_config,
    resolve_structured_output_mode,
)
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEvent


WorkbenchV2Intent = Literal[
    "chat",
    "extract_requirements",
    "update_requirements",
    "confirm_requirements",
    "start_runtime",
    "get_runtime_status",
    "get_runtime_results",
    "read_memory",
    "write_memory",
]


class WorkbenchV2RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1)
    jd: str = Field(min_length=1)
    notes: str | None = None


class WorkbenchV2MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    content: str = Field(min_length=1)


class WorkbenchV2AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: WorkbenchV2Intent
    message: str = Field(min_length=1, max_length=2000)
    needsClarification: bool = False
    clarifyingQuestion: str | None = None
    runtimeInput: WorkbenchV2RuntimeInput | None = None
    requirementPatch: dict[str, object] | None = None
    memoryRead: dict[str, object] | None = None
    memoryWrite: WorkbenchV2MemoryWrite | None = None

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "WorkbenchV2AgentOutput":
        if self.needsClarification and not (self.clarifyingQuestion or "").strip():
            raise ValueError("clarifyingQuestion is required when needsClarification is true")
        if self.intent == "start_runtime" and self.runtimeInput is None:
            raise ValueError("runtimeInput is required for start_runtime")
        return self


class WorkbenchV2AgentRunner(Protocol):
    async def run(self, agent: Agent, prompt: str) -> object: ...


class _DefaultAgentRunner:
    async def run(self, agent: Agent, prompt: str) -> object:
        return await Runner.run(agent, prompt)


class WorkbenchV2AgentLoop(Protocol):
    async def run_turn(
        self,
        *,
        conversation_id: str,
        context_summary: str | None,
        recent_events: Sequence[WorkbenchV2TranscriptEvent],
        user_text: str,
    ) -> WorkbenchV2AgentOutput: ...


@dataclass(frozen=True)
class BailianStrictWorkbenchV2AgentLoop:
    settings: AppSettings
    runner: WorkbenchV2AgentRunner | None = None

    async def run_turn(
        self,
        *,
        conversation_id: str,
        context_summary: str | None,
        recent_events: Sequence[WorkbenchV2TranscriptEvent],
        user_text: str,
    ) -> WorkbenchV2AgentOutput:
        config = resolve_stage_model_config(self.settings, stage="workbench_conversation")
        agent = _build_agent(config)
        prompt = _render_turn_prompt(
            conversation_id=conversation_id,
            context_summary=context_summary,
            recent_events=recent_events,
            user_text=user_text,
        )
        runner = self.runner or _DefaultAgentRunner()
        result = await runner.run(agent, prompt)
        return WorkbenchV2AgentOutput.model_validate(getattr(result, "final_output", result))


def _build_agent(config: ResolvedTextModelConfig) -> Agent:
    _validate_strict_openai_config(config)
    return Agent(
        name="SeekTalent Workbench v2 Agent",
        model=_build_openai_chat_model(config),
        model_settings=ModelSettings(extra_body=build_provider_request_policy(config).extra_body),
        instructions=_system_prompt(),
        tools=[],
        output_type=WorkbenchV2AgentOutput,
    )


def _validate_strict_openai_config(config: ResolvedTextModelConfig) -> None:
    if config.protocol_family != "openai_chat_completions_compatible":
        raise ValueError("Workbench v2 agent requires OpenAI-compatible Bailian chat completions.")
    if config.provider_label != "bailian" or config.endpoint_kind != "bailian_openai_chat_completions":
        raise ValueError("Workbench v2 agent requires the Bailian OpenAI-compatible endpoint.")
    if resolve_structured_output_mode(config) != "native_json_schema":
        raise ValueError("Workbench v2 agent requires native JSON Schema structured output.")
    if not config.api_key:
        raise ValueError("SEEKTALENT_TEXT_LLM_API_KEY is required for Workbench v2 agent turns.")


def _build_openai_chat_model(config: ResolvedTextModelConfig) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(
        model=config.model_id,
        openai_client=AsyncOpenAI(base_url=config.base_url, api_key=config.api_key),
    )


def _system_prompt() -> str:
    return (Path(__file__).resolve().parent / "prompts" / "system.md").read_text(encoding="utf-8")


def _render_turn_prompt(
    *,
    conversation_id: str,
    context_summary: str | None,
    recent_events: Sequence[WorkbenchV2TranscriptEvent],
    user_text: str,
) -> str:
    payload = {
        "conversationId": conversation_id,
        "contextSummary": context_summary or "",
        "recentEvents": [event.model_dump(mode="json") for event in list(recent_events)[-20:]],
        "currentUserText": user_text,
    }
    return "\n".join(
        [
            "[WORKBENCH_V2_TURN_INPUT_START]",
            json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            "[WORKBENCH_V2_TURN_INPUT_END]",
        ]
    )
