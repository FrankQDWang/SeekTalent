from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from seektalent.source_references import SourceReference


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
RunKind = Literal["primary", "rerun", "fork"]
ClaimReason = Literal["queued", "resume_requested"]


class RuntimeRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    run_intent_id: str | None = None
    start_idempotency_key: str | None = None
    run_kind: RunKind = "primary"
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
    schema_version: str = "runtime-control-event/v1"
    visibility: str = "internal"
    idempotency_key: str | None = None
    payload_kind: str = "compact"
    payload_size_bytes: int | None = None
    projection_attempt_count: int = 0
    last_projection_error_code: str | None = None
    projected_at: str | None = None
    workbench_event_global_seq: int | None = None
    created_at: str


class RuntimeControlEvent(RuntimeControlEventInput):
    event_seq: int
    payload_size_bytes: int = 0


class RuntimeStageOutputInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_id: str
    runtime_run_id: str
    stage: str
    node_id: str | None = None
    round_no: int | None = None
    output_kind: str
    schema_version: str
    output: dict[str, object] = Field(default_factory=dict)
    source_event_id: str | None = None
    source_checkpoint_id: str | None = None
    artifact_ref_id: str | None = None
    created_at: str

    @field_validator("node_id")
    @classmethod
    def node_id_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is not None and value == "":
            raise ValueError("node_id must not be empty")
        return value

    @field_validator("round_no")
    @classmethod
    def round_no_must_not_be_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("round_no must not be negative")
        return value


class RuntimeStageOutput(RuntimeStageOutputInput):
    node_key: str
    round_key: int
    payload_hash: str
    payload_size_bytes: int


class RuntimeControlCandidateIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    identity_id: str
    canonical_resume_id: str
    merged_resume_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    equivalent_latest_resume_ids: list[str] = Field(default_factory=list)
    display_source_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_resume_ids: list[str] = Field(default_factory=list)
    incomparable_resume_ids: list[str] = Field(default_factory=list)
    content_version_key: str = ""
    safe_reason_codes: list[str] = Field(default_factory=list)
    display_name: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    summary: str = ""
    score: int | None = None
    fit_bucket: str | None = None
    source_round: int | None = None
    payload_hash: str
    updated_at: str


class RuntimeControlCandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    evidence_id: str
    identity_id: str
    resume_id: str
    source_kind: str
    evidence_level: str
    provider_candidate_key_hash: str
    score: int | None = None
    fit_bucket: str | None = None
    source_references: list[SourceReference] = Field(default_factory=list)
    payload: dict[str, object] = Field(default_factory=dict)
    payload_hash: str
    updated_at: str


class RuntimeControlCandidateFinalizationRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run_id: str
    revision: int
    reason_code: str
    candidate_identity_ids: list[str] = Field(default_factory=list)
    coverage_summary: dict[str, object] = Field(default_factory=dict)
    source_checkpoint_id: str | None = None
    payload_hash: str
    created_at: str


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


class RuntimeWorkerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_run: RuntimeRunRecord
    lease: RuntimeExecutorLease
    claimed_event: RuntimeControlEvent
    claim_reason: ClaimReason


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
