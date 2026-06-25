from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


WORKBENCH_V2_SCHEMA_VERSION = "agent.workbench.v2"

WorkbenchV2EventType = Literal[
    "user_message",
    "assistant_message",
    "assistant_status",
    "requirement_form",
    "requirement_form_confirmed",
    "runtime_progress",
    "runtime_result",
    "error",
    "context_summary",
]
WorkbenchV2Role = Literal["user", "assistant", "system", "runtime"]
WorkbenchV2EventStatus = Literal["pending", "running", "completed", "failed"]
WorkbenchV2RuntimeState = Literal["idle", "queued", "running", "completed", "failed", "cancelled"]


class WorkbenchV2Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    created_at: str
    updated_at: str
    runtime_run_id: str | None = None
    runtime_state: WorkbenchV2RuntimeState = "idle"
    context_summary: str | None = None


class WorkbenchV2TranscriptEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkbenchV2EventType
    role: WorkbenchV2Role
    payload: dict[str, object] = Field(default_factory=dict)
    status: WorkbenchV2EventStatus = "completed"
    parent_event_id: str | None = None
    dedupe_key: str | None = None


class WorkbenchV2TranscriptEvent(WorkbenchV2TranscriptEventInput):
    id: str
    conversation_id: str
    step: int
    created_at: str


class WorkbenchV2ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: WorkbenchV2Conversation
    events: list[WorkbenchV2TranscriptEvent] = Field(default_factory=list)
