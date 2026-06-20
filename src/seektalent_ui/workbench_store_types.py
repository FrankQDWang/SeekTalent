from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.models import RequirementSheet


DEFAULT_TENANT_ID = "local"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
SECURITY_AUDIT_IP_MAX = 64
SECURITY_AUDIT_USER_AGENT_MAX = 512
SOURCE_CONNECTION_WARNING_MAX = 500
DETAIL_OPEN_LEASE_SECONDS = 600
LIEPIN_DAILY_DETAIL_OPEN_LIMIT = 100
LIEPIN_AUTO_DETAIL_REQUEST_LIMIT = 5
LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD = 55
LIEPIN_BROWSER_LOGIN_REQUIRED_CODE = "liepin_browser_login_required"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE = "liepin_browser_probe_unavailable"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE = "liepin_browser_account_mismatch"
LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE = "请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE = "浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE = "当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。"


@dataclass(frozen=True)
class WorkbenchUser:
    user_id: str
    email: str
    display_name: str
    role: Literal["admin", "member"]
    workspace_id: str


@dataclass(frozen=True)
class WorkbenchSourceRun:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    status: Literal["queued", "blocked", "running", "completed", "failed"]
    auth_state: Literal["not_required", "login_required"]
    warning_code: str | None
    warning_message: str | None
    cards_scanned_count: int = 0
    unique_candidates_count: int = 0
    detail_open_used_count: int = 0
    detail_open_blocked_count: int = 0


@dataclass(frozen=True)
class WorkbenchSourceRunRuntimeLink:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    runtime_run_id: str | None


@dataclass(frozen=True)
class WorkbenchRuntimeSourceLaneLatestState:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    runtime_run_id: str | None
    source_lane_run_id: str
    attempt: int
    event_seq: int
    event_type: str
    status: str | None
    payload: dict[str, object]


RuntimeLinkRepairStatus = Literal["attached", "already_attached", "runtime_link_missing"]
GraphCandidateRecoveryState = Literal["ready", "recoverable_empty"]


@dataclass(frozen=True)
class WorkbenchRuntimeLinkRepairResult:
    status: RuntimeLinkRepairStatus
    graph_candidate_state: GraphCandidateRecoveryState
    runtime_run_id: str | None
    reason: str | None = None


SourceConnectionStatus = Literal[
    "login_required",
    "login_in_progress",
    "verification_required",
    "connected",
    "expired",
    "blocked",
    "disconnected",
]


@dataclass(frozen=True)
class WorkbenchSourceConnection:
    connection_id: str
    source_kind: Literal["liepin"]
    status: SourceConnectionStatus
    warning_code: str | None
    warning_message: str | None
    provider_account_hash: str | None
    compliance_gate_ref: str | None
    created_at: str
    updated_at: str
    connected_at: str | None


@dataclass(frozen=True)
class WorkbenchRequirementReview:
    session_id: str
    status: Literal["draft", "approved"]
    requirement_sheet: RequirementSheet | None
    created_at: str
    updated_at: str
    approved_at: str | None


@dataclass(frozen=True)
class WorkbenchSession:
    session_id: str
    workspace_id: str
    owner_user_id: str
    job_title: str
    jd_text: str
    notes: str
    status: Literal["draft"]
    source_runs: list[WorkbenchSourceRun]
    requirement_review: WorkbenchRequirementReview
    runtime_run_id: str | None = None


@dataclass(frozen=True)
class WorkbenchSourceRunJob:
    job_id: str
    source_run_id: str
    session_id: str
    source_kind: Literal["cts", "liepin"]
    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchRuntimeSourcingJob:
    job_id: str
    session_id: str
    status: Literal["queued", "running", "completed", "failed"]
    source_kinds: tuple[Literal["cts", "liepin"], ...]
    source_run_ids: tuple[str, ...]
    runtime_run_id: str | None
    attempt_count: int
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchEvent:
    global_seq: int
    session_seq: int | None
    session_id: str | None
    source_run_id: str | None
    source_kind: Literal["cts", "liepin"] | None
    event_name: str
    schema_version: str
    idempotency_key: str | None
    payload: dict[str, object]
    occurred_at: str
    created_at: str


@dataclass(frozen=True)
class RuntimeSourceCountProjection:
    source_kind: Literal["cts", "liepin"]
    status: str | None
    warning_code: str | None
    cards_scanned_count: int | None
    unique_candidates_count: int | None
    event_seq: int


CandidateEvidenceLevel = Literal["card", "detail", "final"]
CandidateReviewStatus = Literal["new", "promising", "rejected"]


@dataclass(frozen=True)
class WorkbenchCandidateEvidence:
    evidence_id: str
    review_item_id: str
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    evidence_level: CandidateEvidenceLevel
    provider_candidate_key_hash: str
    runtime_identity_id: str | None
    resume_id: str
    score: int | None
    fit_bucket: str | None
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    created_at: str


@dataclass(frozen=True)
class WorkbenchRuntimeCandidateIdentitySnapshot:
    identity_id: str
    canonical_resume_id: str
    merged_resume_ids: list[str]
    source_evidence_ids: list[str]


@dataclass(frozen=True)
class WorkbenchCandidateReviewItem:
    review_item_id: str
    session_id: str
    status: CandidateReviewStatus
    note: str
    display_name: str
    title: str
    company: str
    location: str
    education: str | None
    experience_years: int | None
    summary: str
    aggregate_score: int | None
    fit_bucket: str | None
    why_selected: str
    source_round: int | None
    source_badges: list[str]
    evidence_level: CandidateEvidenceLevel
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    evidence: list[WorkbenchCandidateEvidence]
    created_at: str
    updated_at: str


DetailOpenMode = Literal["human_confirm", "bypass_confirm"]
DetailOpenRequestStatus = Literal["pending", "approved", "rejected", "bypassed", "blocked", "expired"]
DetailOpenLedgerStatus = Literal["planned", "leased", "opened", "skipped", "blocked", "failed", "maybe_used"]


@dataclass(frozen=True)
class WorkbenchSourceRunPolicy:
    session_id: str
    source_kind: Literal["liepin"]
    detail_open_mode: DetailOpenMode
    updated_at: str


@dataclass(frozen=True)
class WorkbenchProviderAction:
    action_kind: Literal["managed_browser"]
    source_kind: Literal["liepin"]
    connection_id: str
    review_item_id: str
    budget_impact: Literal["none", "reserved"]
    message: str


@dataclass(frozen=True)
class WorkbenchDetailOpenLedger:
    ledger_id: str
    status: DetailOpenLedgerStatus
    budget_day: str
    lease_expires_at: str | None


@dataclass(frozen=True)
class WorkbenchDetailOpenCandidateSnapshot:
    review_item_id: str
    display_name: str
    title: str
    company: str
    location: str
    education: str | None
    experience_years: int | None
    summary: str
    aggregate_score: int | None
    evidence_level: CandidateEvidenceLevel
    source_badges: list[str]
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]


@dataclass(frozen=True)
class WorkbenchDetailOpenRequest:
    request_id: str
    session_id: str
    review_item_id: str
    status: DetailOpenRequestStatus
    detail_open_mode: DetailOpenMode
    decision_note: str | None
    candidate: WorkbenchDetailOpenCandidateSnapshot | None
    blocked_reason: str | None
    ledger: WorkbenchDetailOpenLedger | None
    provider_action: WorkbenchProviderAction | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchSecurityAuditEvent:
    audit_id: int
    actor_user_id: str | None
    actor_role: str | None
    workspace_id: str
    request_ip: str | None
    user_agent: str | None
    target_type: str
    target_id: str | None
    action: str
    result: str
    reason_code: str | None
    metadata: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class WorkbenchSourceRunJobContext:
    job: WorkbenchSourceRunJob
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview


@dataclass(frozen=True)
class WorkbenchRuntimeSourcingJobContext:
    job: WorkbenchRuntimeSourcingJob
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview


@dataclass(frozen=True)
class WorkbenchLiepinDetailOpenJobContext:
    intent_id: str
    idempotency_key: str
    request_id: str
    ledger_id: str
    review_item_id: str
    candidate_evidence_id: str
    candidate_resume_id: str | None
    provider_candidate_key_hash: str
    connection_id: str
    compliance_gate_ref: str
    provider_account_hash: str
    detail_candidates_json: str
    budget_day: str
    lease_expires_at: str | None
    source_run_id: str
    runtime_run_id: str | None
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview
