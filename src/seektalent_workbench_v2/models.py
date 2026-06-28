from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WORKBENCH_V2_SCHEMA_VERSION = "agent.workbench.v2"
WORKBENCH_V2_LIST_SCHEMA_VERSION = "agent.workbench.v2.list"
WORKBENCH_V2_EVENTS_SCHEMA_VERSION = "agent.workbench.v2.events"

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
WorkbenchV2SurfaceStatus = Literal["pending", "running", "completed", "partial", "blocked", "failed", "cancelled"]


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

    @field_validator("payload")
    @classmethod
    def payload_must_be_json_serializable(cls, payload: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON-serializable") from exc
        return payload


class WorkbenchV2TranscriptEvent(WorkbenchV2TranscriptEventInput):
    id: str
    conversation_id: str
    step: int
    created_at: str


class WorkbenchV2ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: WorkbenchV2Conversation
    events: list[WorkbenchV2TranscriptEvent] = Field(default_factory=list)


class WorkbenchV2ConversationPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversationId: str
    title: str
    runtimeState: WorkbenchV2RuntimeState = "idle"
    runtimeRunId: str | None = None
    createdAt: str
    updatedAt: str


class WorkbenchV2ConversationListSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversationId: str
    title: str
    status: WorkbenchV2RuntimeState
    updatedAt: str


class WorkbenchV2RuntimeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: WorkbenchV2RuntimeState
    runtimeRunId: str | None = None


class WorkbenchV2GraphNodeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodeId: str
    kind: str
    label: str
    summary: str | None = None
    roundNo: int | None = None
    laneType: str | None = None
    phase: str | None = None
    stage: str | None = None
    status: WorkbenchV2SurfaceStatus
    sourceKind: str | None = "all"
    activityId: str | None = None
    messageId: str | None = None


class WorkbenchV2GraphEdgeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edgeId: str
    fromNodeId: str
    toNodeId: str
    label: str | None = None


class WorkbenchV2StrategyGraphView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[WorkbenchV2GraphNodeView] = Field(default_factory=list)
    edges: list[WorkbenchV2GraphEdgeView] = Field(default_factory=list)


class WorkbenchV2ThinkingProcessCardView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Literal["关键词", "observation", "反思和下一轮变更"]
    text: str
    terms: list[str] = Field(default_factory=list)


class WorkbenchV2ThinkingProcessRoundView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roundNo: int
    status: WorkbenchV2SurfaceStatus
    cards: list[WorkbenchV2ThinkingProcessCardView] = Field(default_factory=list)


class WorkbenchV2ThinkingProcessView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activeRoundNo: int | None = None
    rounds: list[WorkbenchV2ThinkingProcessRoundView] = Field(default_factory=list)


class WorkbenchV2CandidateSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    rank: int
    displayName: str
    headline: str | None = None
    company: str | None = None
    location: str | None = None
    education: str | None = None
    experienceYears: int | None = None
    age: int | None = None
    gender: str | None = None
    activeStatus: str | None = None
    jobStatus: str | None = None
    sourceKinds: list[Literal["cts", "liepin"]] = Field(default_factory=list)
    matchScore: int | None = Field(default=None, ge=0, le=100)
    matchSummary: str | None = None
    status: str
    detailAvailability: Literal["available", "redacted", "approval_required", "unavailable"] = "unavailable"
    accessState: Literal["allowed", "redacted", "approval_required", "denied"] = "denied"
    evidenceLevel: Literal["summary", "detail", "final", "unknown"] = "unknown"


class WorkbenchV2CandidateDetailSectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    items: list[str] = Field(default_factory=list)


class WorkbenchV2CandidateDetailView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    displayName: str
    headline: str | None = None
    company: str | None = None
    location: str | None = None
    education: str | None = None
    experienceYears: int | None = None
    age: int | None = None
    gender: str | None = None
    activeStatus: str | None = None
    jobStatus: str | None = None
    sourceKinds: list[Literal["cts", "liepin"]] = Field(default_factory=list)
    matchScore: int | None = Field(default=None, ge=0, le=100)
    sections: list[WorkbenchV2CandidateDetailSectionView] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    detailAvailability: Literal["available", "redacted", "approval_required", "unavailable"]
    accessState: Literal["allowed", "redacted", "approval_required", "denied"]
    evidenceLevel: Literal["summary", "detail", "final", "unknown"]
    reasonCode: str | None = None


class WorkbenchV2TranscriptEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    step: int
    type: WorkbenchV2EventType
    role: WorkbenchV2Role
    status: WorkbenchV2EventStatus
    payload: dict[str, object] = Field(default_factory=dict)
    createdAt: str


class WorkbenchV2ConversationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.v2"] = WORKBENCH_V2_SCHEMA_VERSION
    conversation: WorkbenchV2ConversationPublic
    transcriptEvents: list[WorkbenchV2TranscriptEventView] = Field(default_factory=list)
    requirementForm: dict[str, object] | None = None
    runtime: WorkbenchV2RuntimeView | None = None
    strategyGraph: WorkbenchV2StrategyGraphView = Field(default_factory=WorkbenchV2StrategyGraphView)
    thinkingProcess: WorkbenchV2ThinkingProcessView = Field(default_factory=WorkbenchV2ThinkingProcessView)
    candidates: list[WorkbenchV2CandidateSummaryView] = Field(default_factory=list)


class WorkbenchV2ConversationEventsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.v2.events"] = WORKBENCH_V2_EVENTS_SCHEMA_VERSION
    conversationId: str
    afterStep: int
    latestStep: int
    events: list[WorkbenchV2TranscriptEventView] = Field(default_factory=list)


class WorkbenchV2ConversationListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.v2.list"] = WORKBENCH_V2_LIST_SCHEMA_VERSION
    conversations: list[WorkbenchV2ConversationListSummary] = Field(default_factory=list)
