from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from agents import Agent, AsyncOpenAI, ModelSettings, OpenAIChatCompletionsModel, Runner, function_tool
from agents.exceptions import ModelBehaviorError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from seektalent.config import AppSettings
from seektalent.llm import (
    ResolvedTextModelConfig,
    build_provider_request_policy,
    resolve_stage_model_config,
    resolve_structured_output_mode,
)
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEvent


TRUNCATED_SUFFIX = "...[truncated]"
MAX_CONTEXT_SUMMARY_CHARS = 2000
MAX_USER_TEXT_CHARS = 4000
MAX_EVENT_PAYLOAD_JSON_CHARS = 2000
MAX_RECENT_EVENTS = 20

WorkbenchV2Intent = Literal[
    "chat",
    "extract_requirements",
    "update_requirements",
    "confirm_requirements",
    "get_runtime_status",
    "get_runtime_results",
    "read_memory",
    "write_memory",
]


class WorkbenchV2RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1)
    jd: str = Field(min_length=1)
    notes: str | None

    @field_validator("jobTitle", "jd", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("notes", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)


class WorkbenchV2RequirementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selectedItemIds: list[str]
    deselectedItemIds: list[str]
    otherNotes: str | None

    @field_validator("selectedItemIds", "deselectedItemIds", mode="before")
    @classmethod
    def strip_item_ids(cls, value: object) -> object:
        if isinstance(value, list):
            return [_strip_string(item) for item in value]
        return value

    @field_validator("selectedItemIds", "deselectedItemIds")
    @classmethod
    def reject_blank_item_ids(cls, value: list[str]) -> list[str]:
        if any(item == "" for item in value):
            raise ValueError("item ids must not be blank")
        return value

    @field_validator("otherNotes", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def validate_change_ids(self) -> "WorkbenchV2RequirementPatch":
        if len(set(self.selectedItemIds)) != len(self.selectedItemIds):
            raise ValueError("selectedItemIds must not contain duplicates")
        if len(set(self.deselectedItemIds)) != len(self.deselectedItemIds):
            raise ValueError("deselectedItemIds must not contain duplicates")
        if set(self.selectedItemIds) & set(self.deselectedItemIds):
            raise ValueError("selectedItemIds and deselectedItemIds must not overlap")
        if self.selectedItemIds or self.deselectedItemIds or self.otherNotes:
            return self
        raise ValueError("requirementPatch must include at least one real change")


class WorkbenchV2MemoryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)

    @field_validator("query", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)


class WorkbenchV2MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("source", "content", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)


class WorkbenchV2AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: WorkbenchV2Intent
    message: str = Field(min_length=1, max_length=2000)
    needsClarification: bool
    clarifyingQuestion: str | None
    runtimeInput: WorkbenchV2RuntimeInput | None
    requirementPatch: WorkbenchV2RequirementPatch | None
    memoryRead: WorkbenchV2MemoryRead | None
    memoryWrite: WorkbenchV2MemoryWrite | None

    @field_validator("message", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("clarifyingQuestion", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "WorkbenchV2AgentOutput":
        def reject_payloads(*payload_names: str) -> None:
            present = [name for name in payload_names if getattr(self, name) is not None]
            if present:
                raise ValueError(f"{self.intent} must not include {', '.join(present)}")

        if self.needsClarification:
            if not self.clarifyingQuestion:
                raise ValueError("clarifyingQuestion is required when needsClarification is true")
            if any((self.runtimeInput, self.requirementPatch, self.memoryRead, self.memoryWrite)):
                raise ValueError("action payloads must be absent when needsClarification is true")
            return self
        if self.clarifyingQuestion is not None:
            raise ValueError("clarifyingQuestion is only allowed when needsClarification is true")

        if self.intent in {"chat", "confirm_requirements", "get_runtime_status", "get_runtime_results"}:
            reject_payloads("runtimeInput", "requirementPatch", "memoryRead", "memoryWrite")
        elif self.intent == "extract_requirements":
            if self.runtimeInput is None:
                raise ValueError("runtimeInput is required for extract_requirements")
            reject_payloads("requirementPatch", "memoryRead", "memoryWrite")
        elif self.intent == "update_requirements":
            if (self.requirementPatch is None) == (self.runtimeInput is None):
                raise ValueError("exactly one of requirementPatch or runtimeInput is required for update_requirements")
            reject_payloads("memoryRead", "memoryWrite")
        elif self.intent == "read_memory":
            if self.memoryRead is None:
                raise ValueError("memoryRead is required for read_memory")
            reject_payloads("runtimeInput", "requirementPatch", "memoryWrite")
        elif self.intent == "write_memory":
            if self.memoryWrite is None:
                raise ValueError("memoryWrite is required for write_memory")
            reject_payloads("runtimeInput", "requirementPatch", "memoryRead")
        else:
            raise ValueError(f"Unsupported Workbench v2 intent: {self.intent}")
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
        try:
            result = await runner.run(agent, prompt)
        except ModelBehaviorError as exc:
            if not _is_schema_model_behavior_error(exc):
                raise
            return await _run_schema_retry(runner=runner, agent=agent, original_prompt=prompt, validation_error=str(exc))
        try:
            return _validate_agent_result(result)
        except ValidationError as exc:
            return await _run_schema_retry(runner=runner, agent=agent, original_prompt=prompt, validation_error=str(exc))


def _build_agent(config: ResolvedTextModelConfig) -> Agent:
    _validate_strict_openai_config(config)
    return Agent(
        name="SeekTalent Workbench v2 Agent",
        model=_build_openai_chat_model(config),
        model_settings=ModelSettings(
            extra_body=build_provider_request_policy(config).extra_body,
            parallel_tool_calls=False,
        ),
        instructions=_system_prompt(),
        tools=_workbench_v2_tools(),
        output_type=WorkbenchV2AgentOutput,
    )


def _workbench_v2_tools() -> list[object]:
    return [
        _tool_extract_requirements,
        _tool_update_requirements,
        _tool_confirm_requirements,
        _tool_start_runtime,
        _tool_get_runtime_status,
        _tool_get_runtime_results,
        _tool_read_memory,
        _tool_write_memory,
    ]


@function_tool(
    name_override="extract_requirements",
    description_override="Normalize a new JD or recruiting request into runtime input fields.",
    strict_mode=True,
)
def _tool_extract_requirements(jobTitle: str, jd: str, notes: str | None = None) -> dict[str, object]:
    return {"intent": "extract_requirements", "runtimeInput": {"jobTitle": jobTitle, "jd": jd, "notes": notes}}


@function_tool(
    name_override="update_requirements",
    description_override="Update the active requirement form or record supplemental requirements for the next round.",
    strict_mode=True,
)
def _tool_update_requirements(
    selectedItemIds: list[str] | None = None,
    deselectedItemIds: list[str] | None = None,
    otherNotes: str | None = None,
) -> dict[str, object]:
    return {
        "intent": "update_requirements",
        "requirementPatch": {
            "selectedItemIds": selectedItemIds or [],
            "deselectedItemIds": deselectedItemIds or [],
            "otherNotes": otherNotes,
        },
    }


@function_tool(
    name_override="confirm_requirements",
    description_override="Confirm the current requirement form after the user explicitly approves it.",
    strict_mode=True,
)
def _tool_confirm_requirements() -> dict[str, object]:
    return {"intent": "confirm_requirements"}


@function_tool(
    name_override="start_runtime",
    description_override="Start the recruiting runtime only after requirements have been explicitly confirmed.",
    strict_mode=True,
)
def _tool_start_runtime() -> dict[str, object]:
    return {"intent": "confirm_requirements"}


@function_tool(
    name_override="get_runtime_status",
    description_override="Read the current active recruiting run status when the user explicitly asks about progress.",
    strict_mode=True,
)
def _tool_get_runtime_status() -> dict[str, object]:
    return {"intent": "get_runtime_status"}


@function_tool(
    name_override="get_runtime_results",
    description_override="Read active recruiting run results or candidate details when the user explicitly asks for them.",
    strict_mode=True,
)
def _tool_get_runtime_results() -> dict[str, object]:
    return {"intent": "get_runtime_results"}


@function_tool(
    name_override="read_memory",
    description_override="Read long-term memory only when the user explicitly asks or memory context is necessary.",
    strict_mode=True,
)
def _tool_read_memory(query: str) -> dict[str, object]:
    return {"intent": "read_memory", "memoryRead": {"query": query}}


@function_tool(
    name_override="write_memory",
    description_override="Write long-term memory only when the user explicitly provides a source for the memory.",
    strict_mode=True,
)
def _tool_write_memory(source: str, content: str) -> dict[str, object]:
    return {"intent": "write_memory", "memoryWrite": {"source": source, "content": content}}


def _validate_agent_result(result: object) -> WorkbenchV2AgentOutput:
    return WorkbenchV2AgentOutput.model_validate(getattr(result, "final_output", result))


def _is_schema_model_behavior_error(exc: ModelBehaviorError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "structured output",
            "invalid json",
            "json when parsing",
            "schema",
            "validation error",
            "typeadapter",
            "parsing",
        )
    )


async def _run_schema_retry(
    *,
    runner: WorkbenchV2AgentRunner,
    agent: Agent,
    original_prompt: str,
    validation_error: str,
) -> WorkbenchV2AgentOutput:
    retry_prompt = _render_schema_retry_prompt(original_prompt=original_prompt, validation_error=validation_error)
    retry_result = await runner.run(agent, retry_prompt)
    return _validate_agent_result(retry_result)


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _strip_optional_string(value: object) -> object:
    if value is None:
        return None
    value = _strip_string(value)
    if value == "":
        return None
    return value


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - len(TRUNCATED_SUFFIX)] + TRUNCATED_SUFFIX


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
        "contextSummary": _truncate_text(context_summary or "", MAX_CONTEXT_SUMMARY_CHARS),
        "recentEvents": [_render_event(event) for event in list(recent_events)[-MAX_RECENT_EVENTS:]],
        "currentUserText": _truncate_text(user_text, MAX_USER_TEXT_CHARS),
    }
    return "\n".join(
        [
            "[WORKBENCH_V2_TURN_INPUT_START]",
            json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            "[WORKBENCH_V2_TURN_INPUT_END]",
        ]
    )


def _render_schema_retry_prompt(*, original_prompt: str, validation_error: str) -> str:
    return "\n".join(
        [
            "Previous structured output did not satisfy the required schema.",
            "Return exactly one valid WorkbenchV2AgentOutput object for the original turn input below.",
            "When there is no active requirement_form, a new JD or recruitment need must use "
            "extract_requirements with runtimeInput.",
            "Use update_requirements with requirementPatch only for edits to an existing active requirement_form.",
            "Never return an empty requirementPatch.",
            f"Schema validation error: {_truncate_text(validation_error, 1000)}",
            original_prompt,
        ]
    )


def _render_event(event: WorkbenchV2TranscriptEvent) -> dict[str, object]:
    payload = event.model_dump(mode="json")
    event_payload = payload.pop("payload")
    payload["payloadJson"] = _truncate_text(
        json.dumps(event_payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")),
        MAX_EVENT_PAYLOAD_JSON_CHARS,
    )
    return payload
