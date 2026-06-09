from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal[
    "queued",
    "starting",
    "running",
    "pause_requested",
    "paused",
    "resume_requested",
    "cancellation_requested",
    "cancelled",
    "completed",
    "failed",
]


class RuntimeRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    agent_conversation_id: str | None = None
    workbench_session_id: str | None = None
    approved_requirement_revision_id: str
    status: RunStatus
    current_stage: str
    current_round: int | None = None
    latest_checkpoint_id: str | None = None
    latest_event_seq: int = 0
    source_ids: list[str] = Field(default_factory=list)
    stop_reason_code: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class RuntimeControlEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    runtime_run_id: str
    event_type: str
    stage: str
    round_no: int | None = None
    source_id: str | None = None
    status: str
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)
    workbench_event_global_seq: int | None = None
    created_at: str


class RuntimeControlEvent(RuntimeControlEventInput):
    event_seq: int


class RuntimeRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    status: RunStatus
    current_stage: str
    current_round: int | None = None
    latest_event_seq: int
    snapshot: dict[str, object] = Field(default_factory=dict)
    updated_at: str


class RuntimeExecutorLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    runtime_run_id: str
    executor_id: str
    attempt_no: int
    status: str
    acquired_at: str
    heartbeat_at: str | None = None
    lease_expires_at: str
    released_at: str | None = None
    reason_code: str | None = None


class RuntimeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    runtime_run_id: str
    stage: str
    round_no: int | None = None
    safe_boundary: str
    run_state: dict[str, object] = Field(default_factory=dict)
    source_plan: dict[str, object] = Field(default_factory=dict)
    pending_commands: list[dict[str, object]] = Field(default_factory=list)
    artifact_manifest_ref: str | None = None
    schema_version: str
    created_at: str


class RuntimeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    runtime_run_id: str
    command_type: str
    payload: dict[str, object] = Field(default_factory=dict)
    status: str
    conflict_group: str
    supersedes_command_id: str | None = None
    superseded_by_command_id: str | None = None
    target_round_no: int | None = None
    idempotency_key: str
    requested_by: str | None = None
    requested_at: str
    applied_at: str | None = None
    rejected_reason_code: str | None = None


class RuntimeDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    runtime_run_id: str
    title: str
    summary: str
    facts: list[dict[str, object]] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    checkpoint_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, object]] = Field(default_factory=list)
    reason_code: str | None = None


class RuntimeFinalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str | None = None
    runtime_run_id: str
    status: str
    summary: str
    facts: list[dict[str, object]] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    source_snapshot_event_seq: int
    latest_snapshot_event_seq: int
    user_instruction: str | None = None
    reason_code: str | None = None
    created_at: str | None = None


class RuntimeControlEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[RuntimeControlEvent] = Field(default_factory=list)
    next_cursor: int
    reason_code: str | None = None
