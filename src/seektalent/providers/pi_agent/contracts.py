from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


NonEmptyStr = Annotated[str, Field(min_length=1)]

PI_MODEL_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PiBoundaryModel(BaseModel):
    model_config = PI_MODEL_CONFIG


class PiAgentTaskType(StrEnum):
    LIEPIN_SEARCH_CARDS = "liepin.search_cards"
    LIEPIN_READ_CARD_PAGE = "liepin.read_card_page"
    LIEPIN_CLASSIFY_CARD_SUMMARY = "liepin.classify_card_summary"
    LIEPIN_REQUEST_DETAIL_OPEN = "liepin.request_detail_open"
    LIEPIN_OPEN_DETAIL_AFTER_APPROVAL = "liepin.open_detail_after_approval"
    LIEPIN_EXTRACT_DETAIL_RESUME = "liepin.extract_detail_resume"
    LIEPIN_DETECT_LOGIN_OR_RISK_STATE = "liepin.detect_login_or_risk_state"


class PiAgentActionType(StrEnum):
    LIEPIN_NAVIGATE_TO_SEARCH = "liepin.navigate_to_search"
    LIEPIN_SUBMIT_KEYWORD_SEARCH = "liepin.submit_keyword_search"
    LIEPIN_READ_CARD_PAGE = "liepin.read_card_page"
    LIEPIN_TURN_PAGE = "liepin.turn_page"
    LIEPIN_CLASSIFY_CARD_SUMMARY = "liepin.classify_card_summary"
    LIEPIN_REQUEST_DETAIL_OPEN = "liepin.request_detail_open"
    LIEPIN_OPEN_DETAIL_AFTER_APPROVAL = "liepin.open_detail_after_approval"
    LIEPIN_EXTRACT_DETAIL_RESUME = "liepin.extract_detail_resume"
    LIEPIN_DETECT_LOGIN_OR_RISK_STATE = "liepin.detect_login_or_risk_state"


class DetailOpenReasonCode(StrEnum):
    STRONG_CARD_MATCH = "strong_card_match"
    HUMAN_SELECTED = "human_selected"
    RUNTIME_RULE_SELECTED = "runtime_rule_selected"
    MANUAL_REVIEW = "manual_review"
    POLICY_SELECTED = "policy_selected"


class PiAgentResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    PARTIAL = "partial"


class PiAgentFailureCode(StrEnum):
    LOGIN_EXPIRED = "login_expired"
    VERIFICATION_REQUIRED = "verification_required"
    RISK_CONTROL = "risk_control"
    SELECTOR_DRIFT = "selector_drift"
    EXTRACTION_FAILURE = "extraction_failure"
    PAGE_TIMEOUT = "page_timeout"
    DOKOBOT_TOOL_CAPABILITY_UNAVAILABLE = "dokobot_tool_capability_unavailable"
    DETAIL_OPEN_GRANT_MISSING = "detail_open_grant_missing"
    DETAIL_BUDGET_RESERVATION_FAILED = "detail_budget_reservation_failed"
    DETAIL_OPEN_GRANT_EXPIRED = "detail_open_grant_expired"
    DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH = "detail_open_grant_candidate_mismatch"
    DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH = "detail_open_grant_source_run_mismatch"
    DETAIL_OPEN_DUPLICATE = "detail_open_duplicate"
    PROVIDER_CONNECTION_LOCKED = "provider_connection_locked"


class PiAgentCompletionReason(StrEnum):
    PAGE_EXHAUSTED = "page_exhausted"
    ENOUGH_STRONG_CARDS = "enough_strong_cards"
    DETAIL_BUDGET_EXHAUSTED = "detail_budget_exhausted"
    DETAIL_BUDGET_WAITING_FOR_HUMAN = "detail_budget_waiting_for_human"
    COMPLETED = "completed"
    USER_STOPPED = "user_stopped"


class PiBackendMode(StrEnum):
    DISABLED = "disabled"
    PI_DOKOBOT_READ = "pi_dokobot_read"
    PI_BROWSER_ACTION = "pi_browser_action"
    WORKER_COMPAT = "worker_compat"
    FAKE_FIXTURE = "fake_fixture"


class ProtectedArtifactClass(StrEnum):
    SAFE_SUMMARY = "safe_summary_artifact"
    REDACTED_EVIDENCE = "redacted_evidence_artifact"
    PROTECTED_PROVIDER_SNAPSHOT = "protected_provider_snapshot"


def _require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class PiArtifactRef(PiBoundaryModel):
    artifact_class: ProtectedArtifactClass
    artifact_ref: NonEmptyStr
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    redaction_policy_id: NonEmptyStr | None = None
    protection_policy_id: NonEmptyStr | None = None

    @field_validator("artifact_ref")
    @classmethod
    def artifact_ref_must_be_handle(cls, value: str) -> str:
        if value.startswith("/") or "://" in value:
            raise ValueError("artifact_ref must be an internal artifact handle")
        if any(part == ".." for part in value.split("/")):
            raise ValueError("artifact_ref must not contain parent path segments")
        return value

    @model_validator(mode="after")
    def validate_artifact_policy(self) -> PiArtifactRef:
        if self.artifact_class in {
            ProtectedArtifactClass.SAFE_SUMMARY,
            ProtectedArtifactClass.REDACTED_EVIDENCE,
        }:
            if not self.redaction_policy_id:
                raise ValueError("safe and redacted artifacts require redaction_policy_id")
            if self.protection_policy_id:
                raise ValueError("safe and redacted artifacts must not claim protection_policy_id")
        if self.artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
            if self.redaction_policy_id:
                raise ValueError("protected raw snapshots must not claim redaction_policy_id")
            if not self.protection_policy_id:
                raise ValueError("protected raw snapshots require protection_policy_id")
        return self


class DokoBotReadResult(PiBoundaryModel):
    schema_version: Literal["dokobot-read-result-v1"]
    url: AnyUrl
    text_ref: PiArtifactRef | None = None
    chunks_ref: PiArtifactRef | None = None
    session_id: NonEmptyStr | None = None
    vertical_has_more: bool = False
    vertical_stop_reason: Literal["end_of_scroll", "limit_reached", "timeout", "unknown"] = "unknown"
    screens_used: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    stderr_redacted_ref: PiArtifactRef | None = None

    @model_validator(mode="after")
    def validate_read_artifact_classes(self) -> DokoBotReadResult:
        if self.text_ref is not None and self.text_ref.artifact_class != ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
            raise ValueError("text_ref must be protected_provider_snapshot")
        if self.chunks_ref is not None and self.chunks_ref.artifact_class != ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
            raise ValueError("chunks_ref must be protected_provider_snapshot")
        if (
            self.stderr_redacted_ref is not None
            and self.stderr_redacted_ref.artifact_class != ProtectedArtifactClass.REDACTED_EVIDENCE
        ):
            raise ValueError("stderr_redacted_ref must be redacted_evidence_artifact")
        return self


class DetailOpenGrant(PiBoundaryModel):
    schema_version: Literal["detail-open-grant-v1"]
    approval_id: NonEmptyStr
    budget_reservation_id: NonEmptyStr
    candidate_ref: NonEmptyStr
    source_run_id: NonEmptyStr
    provider: Literal["liepin"]
    max_detail_opens: int = Field(default=1, ge=1, le=1)
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    idempotency_key: NonEmptyStr
    grant_signature: str = Field(min_length=1, repr=False)

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, "expires_at")


class PiAgentTaskBase(PiBoundaryModel):
    schema_version: Literal["pi-agent-task-v1"]
    task_type: PiAgentTaskType
    session_id: NonEmptyStr
    source_run_id: NonEmptyStr
    connection_id: NonEmptyStr
    artifact_policy: Literal["protected_snapshots_only"]


class LiepinSearchCardsTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_SEARCH_CARDS]
    query_terms: list[NonEmptyStr] = Field(min_length=1)
    keyword_query: NonEmptyStr
    max_pages: int = Field(ge=1, le=20)
    max_cards: int = Field(ge=1, le=500)
    stop_conditions: list[
        Literal["page_exhausted", "enough_strong_cards", "risk_control", "detail_budget_exhausted"]
    ] = Field(min_length=1)


class LiepinReadCardPageTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_READ_CARD_PAGE]
    current_url: AnyUrl
    page_index: int = Field(ge=1, le=20)


class LiepinClassifyCardSummaryTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY]
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    classification_policy_id: NonEmptyStr


class LiepinRequestDetailOpenTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN]
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    reason_code: DetailOpenReasonCode


class LiepinOpenDetailAfterApprovalTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL]
    candidate_ref: NonEmptyStr
    detail_open_grant: DetailOpenGrant


class LiepinExtractDetailResumeTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME]
    candidate_ref: NonEmptyStr
    detail_snapshot_ref: NonEmptyStr


class LiepinDetectLoginOrRiskStateTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE]
    current_url: AnyUrl


_PiAgentTaskUnion = Annotated[
    LiepinSearchCardsTask
    | LiepinReadCardPageTask
    | LiepinClassifyCardSummaryTask
    | LiepinRequestDetailOpenTask
    | LiepinOpenDetailAfterApprovalTask
    | LiepinExtractDetailResumeTask
    | LiepinDetectLoginOrRiskStateTask,
    Field(discriminator="task_type"),
]


class PiAgentTask(RootModel[_PiAgentTaskUnion]):
    model_config = ConfigDict(hide_input_in_errors=True)

    @property
    def task_type(self) -> PiAgentTaskType:
        return self.root.task_type

    def __getattr__(self, name: str) -> object:
        return getattr(self.root, name)


class NavigateToSearchInput(PiBoundaryModel):
    query_home_url: AnyUrl


class SubmitKeywordSearchInput(PiBoundaryModel):
    keyword_query: NonEmptyStr
    query_terms: list[NonEmptyStr] = Field(min_length=1)


class ReadCardPageInput(PiBoundaryModel):
    page_index: int = Field(ge=1, le=20)


class TurnPageInput(PiBoundaryModel):
    next_page_index: int = Field(ge=1, le=20)


class ClassifyCardSummaryInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    classification_policy_id: NonEmptyStr


class RequestDetailOpenInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    reason_code: DetailOpenReasonCode


class OpenDetailAfterApprovalInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    detail_open_grant: DetailOpenGrant


class ExtractDetailResumeInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    detail_snapshot_ref: NonEmptyStr


class DetectLoginOrRiskStateInput(PiBoundaryModel):
    current_url: AnyUrl


class PiAgentActionBase(PiBoundaryModel):
    schema_version: Literal["pi-agent-action-v1"]
    action_type: PiAgentActionType
    target_url: AnyUrl
    safe_target_descriptor: NonEmptyStr


class LiepinNavigateToSearchAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_NAVIGATE_TO_SEARCH]
    input_payload: NavigateToSearchInput


class LiepinSubmitKeywordSearchAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_SUBMIT_KEYWORD_SEARCH]
    input_payload: SubmitKeywordSearchInput


class LiepinReadCardPageAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_READ_CARD_PAGE]
    input_payload: ReadCardPageInput


class LiepinTurnPageAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_TURN_PAGE]
    input_payload: TurnPageInput


class LiepinClassifyCardSummaryAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_CLASSIFY_CARD_SUMMARY]
    input_payload: ClassifyCardSummaryInput


class LiepinRequestDetailOpenAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_REQUEST_DETAIL_OPEN]
    input_payload: RequestDetailOpenInput


class LiepinOpenDetailAfterApprovalAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL]
    input_payload: OpenDetailAfterApprovalInput


class LiepinExtractDetailResumeAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_EXTRACT_DETAIL_RESUME]
    input_payload: ExtractDetailResumeInput


class LiepinDetectLoginOrRiskStateAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE]
    input_payload: DetectLoginOrRiskStateInput


_PiAgentActionUnion = Annotated[
    LiepinNavigateToSearchAction
    | LiepinSubmitKeywordSearchAction
    | LiepinReadCardPageAction
    | LiepinTurnPageAction
    | LiepinClassifyCardSummaryAction
    | LiepinRequestDetailOpenAction
    | LiepinOpenDetailAfterApprovalAction
    | LiepinExtractDetailResumeAction
    | LiepinDetectLoginOrRiskStateAction,
    Field(discriminator="action_type"),
]


class PiAgentAction(RootModel[_PiAgentActionUnion]):
    model_config = ConfigDict(hide_input_in_errors=True)

    @property
    def action_type(self) -> PiAgentActionType:
        return self.root.action_type

    @property
    def input_payload(self) -> object:
        return self.root.input_payload

    def __getattr__(self, name: str) -> object:
        return getattr(self.root, name)


class PiAgentActionTraceEntry(PiBoundaryModel):
    schema_version: Literal["pi-agent-action-trace-v1"]
    timestamp: datetime
    provider_skill_id: NonEmptyStr
    interaction_id: NonEmptyStr
    source_run_id: NonEmptyStr
    connection_id: NonEmptyStr
    action_sequence: int = Field(ge=1)
    action_type: PiAgentActionType
    backend_mode: PiBackendMode
    capability_version: NonEmptyStr
    safe_target_descriptor: NonEmptyStr
    result_code: Literal["ok", "blocked", "failed", "partial"]
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    redaction_policy_id: NonEmptyStr
    redacted_evidence_ref: NonEmptyStr | None = None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_code: PiAgentFailureCode | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, "timestamp")

    @model_validator(mode="after")
    def validate_trace_consistency(self) -> PiAgentActionTraceEntry:
        if self.result_code == "ok" and self.failure_code is not None:
            raise ValueError("ok trace cannot carry failure_code")
        if self.result_code in {"blocked", "failed"} and self.failure_code is None:
            raise ValueError("blocked/failed trace requires failure_code")
        if bool(self.redacted_evidence_ref) != bool(self.evidence_sha256):
            raise ValueError("redacted_evidence_ref and evidence_sha256 must appear together")
        return self


class PiAgentResult(PiBoundaryModel):
    schema_version: Literal["pi-agent-result-v1"]
    status: PiAgentResultStatus
    stop_reason: PiAgentFailureCode | PiAgentCompletionReason | None = None
    action_trace_ref: PiArtifactRef
    protected_snapshot_refs: list[PiArtifactRef] = Field(default_factory=list)
    safe_summary_refs: list[PiArtifactRef] = Field(default_factory=list)
    cards_seen: int = Field(default=0, ge=0)
    cards_selected: int = Field(default=0, ge=0)
    detail_requests: int = Field(default=0, ge=0)
    details_opened: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_result_contract(self) -> PiAgentResult:
        if self.action_trace_ref.artifact_class != ProtectedArtifactClass.REDACTED_EVIDENCE:
            raise ValueError("action_trace_ref must be redacted_evidence_artifact")
        if self.status in {PiAgentResultStatus.BLOCKED, PiAgentResultStatus.FAILED}:
            if not isinstance(self.stop_reason, PiAgentFailureCode):
                raise ValueError("blocked/failed results require a failure stop_reason")
        if self.status == PiAgentResultStatus.SUCCEEDED:
            if self.stop_reason is not None and not isinstance(self.stop_reason, PiAgentCompletionReason):
                raise ValueError("succeeded results cannot use failure stop_reason")
        if self.status == PiAgentResultStatus.NEEDS_APPROVAL:
            if self.stop_reason != PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN:
                raise ValueError("needs_approval requires detail_budget_waiting_for_human")
        if self.status == PiAgentResultStatus.PARTIAL and self.stop_reason is None:
            raise ValueError("partial results require stop_reason")
        for ref in self.protected_snapshot_refs:
            if ref.artifact_class != ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
                raise ValueError("protected_snapshot_refs must only contain protected_provider_snapshot")
        for ref in self.safe_summary_refs:
            if ref.artifact_class != ProtectedArtifactClass.SAFE_SUMMARY:
                raise ValueError("safe_summary_refs must only contain safe_summary_artifact")
        return self
