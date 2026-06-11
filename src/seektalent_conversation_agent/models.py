from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompactionSummaryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_summary_id: str | None = None
    covered_message_seq_end: int | None = None


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    owner_user_id: str
    workspace_id: str
    status: str = "draft"
    title: str
    is_archived: bool = False
    latest_message_seq: int = 0
    latest_activity_seq: int = 0
    latest_rendered_runtime_event_seq: int = 0
    runtime_run_id: str | None = None
    workbench_session_id: str | None = None
    latest_draft_revision_id: str | None = None
    approved_requirement_revision_id: str | None = None
    final_summary_id: str | None = None
    pending_user_action: str | None = None
    pending_command_count: int = 0
    pending_requirement_review_count: int = 0
    pending_memory_review_count: int = 0
    created_at: str
    updated_at: str
    last_opened_at: str | None = None
    archived_at: str | None = None
    completed_at: str | None = None


class TranscriptMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    conversation_id: str
    message_seq: int
    role: str
    message_type: str
    text: str
    payload: dict[str, object] = Field(default_factory=dict)
    token_count: int | None = None
    model_input_included: bool = True
    source_tool_call_id: str | None = None
    source_runtime_run_id: str | None = None
    source_runtime_event_seq: int | None = None
    created_at: str


class TranscriptActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: str
    conversation_id: str
    activity_seq: int
    activity_key: str
    activity_type: str
    status: str
    title: str
    summary: str
    source_runtime_run_id: str | None = None
    source_event_id_latest: str | None = None
    source_event_seq_start: int | None = None
    source_event_seq_latest: int | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    created_at: str


class ConversationReopenState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str
    status: str
    is_archived: bool
    latest_message_seq: int
    latest_activity_seq: int
    latest_rendered_runtime_event_seq: int
    runtime_run_id: str | None = None
    workbench_session_id: str | None = None
    latest_draft_revision_id: str | None = None
    approved_requirement_revision_id: str | None = None
    final_summary_id: str | None = None
    pending_user_action: str | None = None
    pending_command_count: int = 0
    pending_requirement_review_count: int = 0
    pending_memory_review_count: int = 0
    compaction_summary_cursor: CompactionSummaryCursor = Field(default_factory=CompactionSummaryCursor)
    allowed_actions: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    last_opened_at: str


class ConversationThreadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_reopen_state: ConversationReopenState
    messages: list[TranscriptMessage] = Field(default_factory=list)
    activity_items: list[TranscriptActivityItem] = Field(default_factory=list)


class AgentToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    conversation_id: str
    tool_name: str
    status: str
    args: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    reason_code: str | None = None
    started_at: str
    completed_at: str | None = None


class ContextSummaryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str
    conversation_id: str
    source_message_seq_start: int
    source_message_seq_end: int
    source_activity_seq_start: int | None = None
    source_activity_seq_end: int | None = None
    latest_rendered_runtime_event_seq: int
    summary_text: str
    quality_status: str
    quality_evidence: dict[str, object] = Field(default_factory=dict)
    token_count: int | None = None
    created_at: str


class ContextCompactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compaction_id: str
    conversation_id: str
    status: str
    trigger_reason_code: str
    summary_id: str | None = None
    source_message_seq_start: int | None = None
    source_message_seq_end: int | None = None
    source_activity_seq_start: int | None = None
    source_activity_seq_end: int | None = None
    quality_reason_code: str | None = None
    created_at: str
    completed_at: str | None = None
    failed_reason_code: str | None = None


class ConversationAgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_reopen_state: ConversationReopenState
    messages: list[TranscriptMessage] = Field(default_factory=list)
    activity_items: list[TranscriptActivityItem] = Field(default_factory=list)
    requirement_draft: object | None = None
    final_summary: object | None = None
    compaction: ContextCompactionRecord | None = None
    reason_code: str | None = None
