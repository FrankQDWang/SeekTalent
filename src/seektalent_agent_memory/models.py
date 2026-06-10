from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    workspace_id: str
    memory_enabled: bool = True
    generation_enabled: bool = True
    recall_enabled: bool = True
    review_required: bool = False
    max_rollouts_per_startup: int = 4
    max_rollout_age_days: int = 30
    min_rollout_idle_hours: int = 6
    max_stage1_outputs_for_phase2: int = 20
    max_unused_days: int = 180
    summary_token_budget: int = 1200
    candidate_retention_days: int = 180
    rejected_retention_days: int = 30
    source_excerpt_retention_days: int = 30
    updated_at: str


class MemoryJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    job_key: str
    owner_user_id: str
    workspace_id: str
    status: str
    worker_id: str | None = None
    ownership_token: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    lease_until: str | None = None
    retry_at: str | None = None
    retry_remaining: int = 3
    last_error_code: str | None = None
    input_watermark: str | None = None
    last_success_watermark: str | None = None


class MemoryJobClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    ownership_token: str | None = None
    reason_code: str | None = None


class PrivacyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_text: str
    safe_excerpt: str
    raw_candidate_hash: str
    reason_code: str | None = None
    redacted: bool = False


class Stage1Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    owner_user_id: str
    workspace_id: str
    source_updated_at: str
    raw_memory: str
    rollout_summary: str
    rollout_slug: str | None = None
    generated_at: str
    usage_count: int = 0
    last_usage: str | None = None
    selected_for_phase2: bool = False
    selected_for_phase2_source_updated_at: str | None = None
    privacy_review_json: dict[str, object] = Field(default_factory=dict)
    source_message_ids: list[str] = Field(default_factory=list)
    source_activity_ids: list[str] = Field(default_factory=list)


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    owner_user_id: str
    workspace_id: str
    conversation_id: str
    category: str
    text: str
    safe_excerpt: str
    source_message_ids: list[str] = Field(default_factory=list)
    status: str
    reason_code: str | None = None
    created_at: str
    reviewed_at: str | None = None
    accepted_fact_id: str | None = None
    raw_candidate_hash: str | None = None
    safe_candidate_text: str | None = None
    safe_evidence_excerpt: str | None = None
    privacy_review_json: dict[str, object] = Field(default_factory=dict)
    confidence: float | None = None
    source_stage1_conversation_id: str | None = None
    source_activity_ids: list[str] = Field(default_factory=list)
    expires_at: str | None = None


class MemoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    owner_user_id: str
    workspace_id: str
    category: str
    text: str
    source_candidate_id: str
    source_conversation_ids: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: str
    updated_at: str
    expires_at: str | None = None
    deleted_at: str | None = None
    confidence: float | None = None
    safe_evidence_excerpt: str | None = None
    source_stage1_conversation_ids: list[str] = Field(default_factory=list)
    last_used_at: str | None = None
    usage_count: int = 0


class MemorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str
    owner_user_id: str
    workspace_id: str
    summary_text: str
    fact_ids: list[str] = Field(default_factory=list)
    created_at: str
    invalidated_at: str | None = None
    schema_version: str = "agent.memory.summary.v1"
    summary_kind: str = "consolidated"
    status: str = "active"
    token_estimate: int | None = None
    source_stage1_conversation_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)


class MemoryUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_id: str
    owner_user_id: str
    workspace_id: str
    conversation_id: str
    turn_id: str
    fact_ids: list[str] = Field(default_factory=list)
    created_at: str
    summary_id: str | None = None
    agent_turn_id: str | None = None
    reason_code: str | None = None


class MemoryWorkspaceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    workspace_id: str
    path: str
    content_hash: str
    content: str
    baseline_hash: str | None = None
    updated_at: str


class MemoryClearResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    workspace_id: str
    deleted_fact_count: int
    cleared_at: str


class MemoryRetentionCleanupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    workspace_id: str
    deleted_fact_count: int = 0
    purged_rejected_candidate_count: int = 0
    cleared_fact_excerpt_count: int = 0
    cleared_candidate_excerpt_count: int = 0
    cleaned_at: str


class MemoryCandidateExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(default_factory=list)


class AdvisoryMemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_ids: list[str] = Field(default_factory=list)
    context_text: str
    summary_id: str | None = None
    reason_code: str | None = None


class MemoryPhase1RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimed: int = 0
    succeeded_with_output: int = 0
    succeeded_no_output: int = 0
    failed: int = 0
    reason_code: str | None = None


class MemoryPhase2RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    selected: int = 0
    summary_id: str | None = None
    reason_code: str | None = None
