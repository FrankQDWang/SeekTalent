from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "completed", "failed"]


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1)
    jdText: str = Field(min_length=1)
    sourcingPreferenceText: str = ""
    provider: Literal["cts", "liepin"] = "cts"
    connectionId: str | None = None
    complianceGateRef: str | None = None


class RunCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: Literal["queued", "running"]


class AgentShortlistCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    externalIdentityId: str
    name: str
    title: str
    company: str
    location: str
    summary: str
    reason: str
    score: float
    sourceRound: int


class ResumeWorkExperienceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    title: str
    duration: str | None = None
    startTime: str | None = None
    endTime: str | None = None
    summary: str | None = None


class ResumeEducationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str
    degree: str
    major: str
    startTime: str | None = None
    endTime: str | None = None


class ResumeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workYear: int | None = None
    currentLocation: str | None = None
    expectedLocation: str | None = None
    jobState: str | None = None
    expectedSalary: str | None = None
    age: int | None = None
    education: list[ResumeEducationItem] = Field(default_factory=list)
    workExperience: list[ResumeWorkExperienceItem] = Field(default_factory=list)
    workSummaries: list[str] = Field(default_factory=list)
    projectNames: list[str] = Field(default_factory=list)


class CandidateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    externalIdentityId: str
    name: str
    title: str
    company: str
    location: str
    summary: str = ""


class CandidateResumeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projection: ResumeProjection


class ResumeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str
    evidenceSpans: list[str] = Field(default_factory=list)
    riskFlags: list[str] = Field(default_factory=list)


class CandidateDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateCard
    resumeView: CandidateResumeView
    aiAnalysis: ResumeAnalysis


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: RunStatus
    errorMessage: str | None = None
    finalShortlist: list[AgentShortlistCandidate] = Field(default_factory=list)


class LiepinRunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: RunStatus
    errorMessage: str | None = None
    counters: dict[str, int] = Field(default_factory=dict)


class LiepinComplianceGateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidatePersonalInfoProcessingBasis: str
    personalInformationProcessor: str
    operatorAuditOwner: str
    accountHolderAuthorized: bool
    humanInitiatedRecruiting: bool
    allowedPurposes: list[str]
    retentionPolicy: Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
    deletionSlaDays: int
    deletionPath: str
    rawPayloadAccessScope: Literal["run_only", "workspace", "admin_only"]
    rawDetailRetentionAllowedAfterDebug: bool
    fixtureExportAllowed: bool
    policyRef: str


class LiepinComplianceGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateRef: str
    tenantId: str
    workspaceId: str
    actorId: str
    status: Literal["pending_account_binding", "approved", "denied", "expired"]
    allowedPurposes: list[str]
    retentionPolicy: Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
    policyRef: str


class LiepinConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complianceGateRef: str


class LiepinComplianceGateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str


class LiepinComplianceGateActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateRef: str
    status: Literal["pending_account_binding", "approved", "denied", "expired"]


class LiepinConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    tenantId: str
    workspaceId: str
    actorId: str
    complianceGateRef: str
    status: str


class LiepinLoginUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    loginUrl: str
    handoffState: Literal["ready_for_browser_login"]


class LiepinRunResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    results: list[dict[str, object]] = Field(default_factory=list)


class WorkbenchBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=1024)
    displayName: str = Field(min_length=1, max_length=128)


class WorkbenchLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class WorkbenchUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userId: str
    email: str
    displayName: str
    role: Literal["admin", "member"]
    workspaceId: str


class WorkbenchWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class WorkbenchBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: WorkbenchUserResponse
    workspace: WorkbenchWorkspaceResponse


class WorkbenchMeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: WorkbenchUserResponse


SourceKind = Literal["cts", "liepin"]
WorkbenchSourceStatus = Literal["queued", "blocked", "running", "completed", "failed"]
WorkbenchAuthState = Literal["not_required", "login_required"]
WorkbenchTriageStatus = Literal["draft", "approved"]
WorkbenchJobStatus = Literal["queued", "running", "completed", "failed"]
WorkbenchCandidateReviewStatus = Literal["new", "promising", "rejected"]
WorkbenchCandidateEvidenceLevel = Literal["card", "detail", "final"]
WorkbenchDetailOpenMode = Literal["human_confirm", "bypass_confirm"]
WorkbenchDetailOpenRequestStatus = Literal["pending", "approved", "rejected", "bypassed", "blocked", "expired"]
WorkbenchDetailOpenLedgerStatus = Literal["planned", "leased", "opened", "skipped", "blocked", "failed", "maybe_used"]
WorkbenchProviderActionBudgetImpact = Literal["none", "reserved"]
WorkbenchSourceConnectionStatus = Literal[
    "login_required",
    "login_in_progress",
    "verification_required",
    "connected",
    "expired",
    "blocked",
    "disconnected",
]


class WorkbenchSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1, max_length=256)
    jdText: str = Field(min_length=1, max_length=20000)
    notes: str = Field(default="", max_length=5000)
    sourceKinds: list[SourceKind] | None = Field(default=None, min_length=1, max_length=2)


class WorkbenchSourceRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRunId: str
    sourceKind: SourceKind
    status: WorkbenchSourceStatus
    authState: WorkbenchAuthState
    warningCode: str | None = None
    warningMessage: str | None = None
    cardsScannedCount: int = 0
    uniqueCandidatesCount: int = 0
    detailOpenUsedCount: int = 0
    detailOpenBlockedCount: int = 0


class WorkbenchRequirementTriageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mustHaves: list[str] = Field(default_factory=list, max_length=50)
    niceToHaves: list[str] = Field(default_factory=list, max_length=50)
    synonyms: list[str] = Field(default_factory=list, max_length=50)
    seniorityFilters: list[str] = Field(default_factory=list, max_length=20)
    exclusions: list[str] = Field(default_factory=list, max_length=50)
    generatedQueryHints: list[str] = Field(default_factory=list, max_length=50)


class WorkbenchSourceRunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: SourceKind
    idempotencyKey: str | None = Field(default=None, max_length=128)


class WorkbenchRequirementTriageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    status: WorkbenchTriageStatus
    mustHaves: list[str]
    niceToHaves: list[str]
    synonyms: list[str]
    seniorityFilters: list[str]
    exclusions: list[str]
    generatedQueryHints: list[str]
    createdAt: str
    updatedAt: str
    approvedAt: str | None = None


class WorkbenchSourceCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRunId: str
    sourceKind: SourceKind
    label: str
    status: WorkbenchSourceStatus
    authState: WorkbenchAuthState
    warningCode: str | None = None
    warningMessage: str | None = None
    cardsScannedCount: int = 0
    uniqueCandidatesCount: int = 0
    detailOpenUsedCount: int = 0
    detailOpenBlockedCount: int = 0
    connectionId: str | None = None
    connectionStatus: WorkbenchSourceConnectionStatus | None = None
    connectionWarningCode: str | None = None
    connectionWarningMessage: str | None = None


class WorkbenchSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    workspaceId: str
    ownerUserId: str
    jobTitle: str
    jdText: str
    notes: str
    status: Literal["draft"]
    requirementTriage: WorkbenchRequirementTriageResponse
    sourceRuns: list[WorkbenchSourceRunResponse]
    sourceCards: list[WorkbenchSourceCardResponse]


class WorkbenchSessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: list[WorkbenchSessionResponse]


class WorkbenchSettingsSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: SourceKind
    label: str
    enabled: bool
    authRequired: bool


class WorkbenchSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspaceId: str
    sources: list[WorkbenchSettingsSourceResponse]


class WorkbenchSourceConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    sourceKind: SourceKind
    label: str
    status: WorkbenchSourceConnectionStatus
    warningCode: str | None = None
    warningMessage: str | None = None
    createdAt: str
    updatedAt: str
    connectedAt: str | None = None


class WorkbenchSourceConnectionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: list[WorkbenchSourceConnectionResponse]


class WorkbenchLiepinLoginHandoffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    sourceKind: Literal["liepin"]
    status: WorkbenchSourceConnectionStatus
    handoffMode: Literal["server_managed_browser"]
    handoffState: Literal["login_in_progress", "relay_pending_worker", "safe_frame_available"]
    safeFrameUrl: str | None = None
    warningCode: str | None = None
    warningMessage: str | None = None


class WorkbenchLiepinLoginRelayInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["click", "type", "key"]
    x: float | None = None
    y: float | None = None
    text: str | None = None
    key: str | None = None


class WorkbenchSourceRunJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    sourceRunId: str
    status: WorkbenchJobStatus
    attemptCount: int
    errorMessage: str | None = None
    createdAt: str
    updatedAt: str


class WorkbenchSourceRunStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    sourceRunId: str
    sourceKind: SourceKind
    status: WorkbenchSourceStatus
    job: WorkbenchSourceRunJobResponse


class WorkbenchSourceRunPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detailOpenMode: WorkbenchDetailOpenMode


class WorkbenchSourceRunPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    sourceKind: Literal["liepin"]
    detailOpenMode: WorkbenchDetailOpenMode
    updatedAt: str


class WorkbenchDetailOpenRequestCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: str | None = Field(default=None, max_length=128)


class WorkbenchDetailOpenRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class WorkbenchDetailOpenLedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ledgerId: str
    status: WorkbenchDetailOpenLedgerStatus
    budgetDay: str
    leaseExpiresAt: str | None = None


class WorkbenchDetailOpenCandidateSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewItemId: str
    displayName: str
    title: str
    company: str
    location: str
    summary: str
    aggregateScore: int | None = None
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    sourceBadges: list[str]
    matchedMustHaves: list[str]
    matchedPreferences: list[str]
    missingRisks: list[str]


class WorkbenchProviderActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionKind: Literal["managed_browser"]
    sourceKind: Literal["liepin"]
    connectionId: str
    reviewItemId: str
    budgetImpact: WorkbenchProviderActionBudgetImpact
    message: str


class WorkbenchDetailOpenRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str
    sessionId: str
    reviewItemId: str
    status: WorkbenchDetailOpenRequestStatus
    detailOpenMode: WorkbenchDetailOpenMode
    decisionNote: str | None = None
    candidate: WorkbenchDetailOpenCandidateSnapshotResponse | None = None
    blockedReason: str | None = None
    ledger: WorkbenchDetailOpenLedgerResponse | None = None
    providerAction: WorkbenchProviderActionResponse | None = None
    createdAt: str
    updatedAt: str


class WorkbenchDetailOpenRequestListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[WorkbenchDetailOpenRequestResponse]


class WorkbenchEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    globalSeq: int
    sessionSeq: int | None = None
    sessionId: str | None = None
    sourceRunId: str | None = None
    sourceKind: SourceKind | None = None
    eventName: str
    payload: dict[str, object]
    createdAt: str


class WorkbenchEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[WorkbenchEventResponse]


class WorkbenchSecurityAuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auditId: int
    actorUserId: str | None = None
    actorRole: str | None = None
    workspaceId: str
    requestIp: str | None = None
    userAgent: str | None = None
    targetType: str
    targetId: str | None = None
    action: str
    result: str
    reasonCode: str | None = None
    metadata: dict[str, object]
    createdAt: str


class WorkbenchSecurityAuditEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[WorkbenchSecurityAuditEventResponse]


class WorkbenchCandidateEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidenceId: str
    sourceRunId: str
    sourceKind: SourceKind
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    score: int | None = None
    fitBucket: str | None = None
    matchedMustHaves: list[str]
    matchedPreferences: list[str]
    missingRisks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    createdAt: str


class WorkbenchCandidateReviewItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewItemId: str
    sessionId: str
    status: WorkbenchCandidateReviewStatus
    note: str
    displayName: str
    title: str
    company: str
    location: str
    summary: str
    aggregateScore: int | None = None
    fitBucket: str | None = None
    sourceBadges: list[str]
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    matchedMustHaves: list[str]
    matchedPreferences: list[str]
    missingRisks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    evidence: list[WorkbenchCandidateEvidenceResponse]
    createdAt: str
    updatedAt: str


class WorkbenchCandidateReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkbenchCandidateReviewItemResponse]


class WorkbenchCandidateReviewItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkbenchCandidateReviewStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
