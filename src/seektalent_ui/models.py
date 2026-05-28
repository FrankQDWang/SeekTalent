from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import RequirementSheet


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
WorkbenchSourceStatus = Literal["queued", "blocked", "running", "completed", "partial", "failed", "cancelled"]
WorkbenchAuthState = Literal["not_required", "login_required"]
WorkbenchRequirementReviewStatus = Literal["draft", "approved"]
WorkbenchJobStatus = Literal["queued", "running", "completed", "failed"]
WorkbenchCandidateReviewStatus = Literal["new", "promising", "rejected"]
WorkbenchCandidateEvidenceLevel = Literal["card", "detail", "final"]
WorkbenchGraphNodeKind = Literal["recall", "scoring", "final", "liepin_card", "detail_approval"]
WorkbenchGraphRelationshipKind = Literal[
    "recalled",
    "new",
    "scored",
    "fit",
    "not_fit",
    "final",
    "detail_requested",
]
WorkbenchGraphCandidateRecoveryState = Literal["ready", "recoverable_empty"]
WorkbenchRuntimeGraphSourceKind = Literal["cts", "liepin", "all"]
WorkbenchRuntimeGraphNodeStatus = Literal[
    "pending",
    "running",
    "completed",
    "partial",
    "blocked",
    "degraded",
    "failed",
    "cancelled",
]
WorkbenchRuntimeGraphSectionKind = Literal["text", "facts", "list"]
WorkbenchRuntimeGraphCandidateScopeKind = Literal[
    "none",
    "round_recall",
    "round_score",
    "final",
    "detail_approval",
]
WorkbenchResumeSnapshotStatus = Literal["ready", "snapshot_forbidden", "snapshot_not_found"]
WorkbenchResumeSnapshotSourceCompleteness = Literal["cts_raw_payload", "normalized_fallback", "unavailable"]
WorkbenchDetailOpenMode = Literal["human_confirm", "bypass_confirm"]
WorkbenchDetailOpenRequestStatus = Literal["pending", "approved", "rejected", "bypassed", "blocked", "expired"]
WorkbenchDetailOpenLedgerStatus = Literal["planned", "leased", "opened", "skipped", "blocked", "failed", "maybe_used"]
WorkbenchNoteStatusHint = Literal[
    "new_progress",
    "waiting",
    "human_action_required",
    "completed",
    "failed",
    "canceled",
    "unknown",
]
WorkbenchNoteKind = Literal["progress", "waiting", "human_action", "terminal"]
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


class WorkbenchRequirementReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_sheet: RequirementSheet


class WorkbenchRequirementReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: WorkbenchRequirementReviewStatus
    requirement_sheet: RequirementSheet | None
    created_at: str
    updated_at: str
    approved_at: str | None = None


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


RuntimeSourceDisplayStatus = Literal["pending", "running", "completed", "partial", "blocked", "failed", "cancelled"]
RuntimeSourceCoverageStatus = Literal["pending", "complete", "degraded", "empty"]
RuntimeSourceDetailState = Literal["detail_recommended", "pending_approval", "leased", "completed", "blocked"]


class WorkbenchRuntimeSourceLaneStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: SourceKind
    status: RuntimeSourceDisplayStatus
    reasonCode: str | None = None
    eventType: str | None = None
    eventSeq: int | None = None
    cardsSeenCount: int = 0
    cardsFilteredCount: int = 0
    candidatesCount: int = 0
    detailRecommendationsCount: int = 0
    detailState: RuntimeSourceDetailState | None = None


class WorkbenchRuntimeSourceStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selectedSourceKinds: list[SourceKind]
    coverageStatus: RuntimeSourceCoverageStatus
    finalizationRevision: int | None = None
    finalizationReasonCode: str | None = None
    identityMergeCount: int = 0
    ambiguousDuplicateCount: int = 0
    canonicalResumeSelectedCount: int = 0
    sources: list[WorkbenchRuntimeSourceLaneStateResponse]


class WorkbenchDevModeComponentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    status: str
    reasonCode: str | None = None
    authNote: str | None = None


class WorkbenchDevModeDataRootResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    status: str
    reasonCode: str


class WorkbenchDevModeDataRootPostureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    roots: dict[str, WorkbenchDevModeDataRootResponse]


class WorkbenchDevModeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["settings", "raw_env_diagnostics"]
    overallStatus: str
    components: list[WorkbenchDevModeComponentResponse]
    credentials: dict[str, WorkbenchDevModeComponentResponse]
    sources: dict[str, WorkbenchDevModeComponentResponse]
    dataRoots: WorkbenchDevModeDataRootPostureResponse


class WorkbenchSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    workspaceId: str
    ownerUserId: str
    jobTitle: str
    jdText: str
    notes: str
    status: Literal["draft"]
    requirement_review: WorkbenchRequirementReviewResponse
    sourceRuns: list[WorkbenchSourceRunResponse]
    sourceCards: list[WorkbenchSourceCardResponse]
    runtimeSourceState: WorkbenchRuntimeSourceStateResponse | None = None


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


class WorkbenchRuntimeSourcingJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    status: WorkbenchJobStatus
    sourceKinds: list[SourceKind]
    attemptCount: int
    errorMessage: str | None = None
    createdAt: str
    updatedAt: str


class WorkbenchSessionStartBlockedSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRunId: str
    sourceKind: SourceKind
    reason: str


class WorkbenchSessionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    runtimeJob: WorkbenchRuntimeSourcingJobResponse | None = None
    blockedSources: list[WorkbenchSessionStartBlockedSourceResponse] = Field(default_factory=list)


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
    schemaVersion: str
    idempotencyKey: str | None = None
    payload: dict[str, object]
    occurredAt: str
    createdAt: str


class WorkbenchNoteCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventSeq: int
    noteId: str
    text: str
    statusHint: WorkbenchNoteStatusHint
    noteKind: WorkbenchNoteKind
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


class WorkbenchFinalTopCandidateEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidenceId: str
    sourceRunId: str
    sourceKind: SourceKind
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    score: int | None = None
    fitBucket: str | None = None


class WorkbenchFinalTopCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewItemId: str
    runtimeIdentityId: str
    canonicalReviewItemId: str
    mergedReviewItemIds: list[str]
    rank: int
    displayName: str
    title: str
    company: str
    location: str
    summary: str
    aggregateScore: int | None = None
    fitBucket: str | None = None
    whySelected: str
    riskFlags: list[str]
    matchedMustHaves: list[str]
    matchedPreferences: list[str]
    strengths: list[str]
    weaknesses: list[str]
    sourceRound: int | None = None
    sourceBadges: list[str]
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    sourceEvidence: list[WorkbenchFinalTopCandidateEvidenceResponse]


class WorkbenchFinalTopCandidateListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkbenchFinalTopCandidateResponse]
    coverageStatus: RuntimeSourceCoverageStatus
    finalizationRevision: int | None = None


class WorkbenchRuntimeGraphFactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class WorkbenchRuntimeGraphSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    kind: WorkbenchRuntimeGraphSectionKind
    text: str | None = None
    facts: list[WorkbenchRuntimeGraphFactResponse] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)


class WorkbenchRuntimeGraphCandidateScopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopeKind: WorkbenchRuntimeGraphCandidateScopeKind
    sourceKind: WorkbenchRuntimeGraphSourceKind = "all"
    roundNo: int | None = None
    reason: str | None = None


class WorkbenchRuntimeGraphNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodeId: str
    kind: str
    label: str
    summaryText: str
    status: WorkbenchRuntimeGraphNodeStatus
    stage: str
    sourceKind: WorkbenchRuntimeGraphSourceKind = "all"
    lane: Literal["shared", "cts", "liepin"] = "shared"
    roundNo: int | None = None
    eventIds: list[str] = Field(default_factory=list)
    detailSections: list[WorkbenchRuntimeGraphSectionResponse] = Field(default_factory=list)
    candidateScope: WorkbenchRuntimeGraphCandidateScopeResponse


class WorkbenchRuntimeGraphEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edgeId: str
    fromNodeId: str
    toNodeId: str
    label: str | None = None


class WorkbenchRuntimeGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    generatedAt: str
    nodes: list[WorkbenchRuntimeGraphNodeResponse]
    edges: list[WorkbenchRuntimeGraphEdgeResponse]
    completionText: str | None = None


class WorkbenchGraphCandidateSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graphCandidateId: str
    sourceKind: SourceKind
    sourceRunId: str
    nodeKind: WorkbenchGraphNodeKind
    roundNo: int | None = None
    laneType: str | None = None
    queryRole: str | None = None
    relationshipKind: WorkbenchGraphRelationshipKind
    displayName: str
    title: str
    company: str
    location: str
    sourceBadges: list[str]
    score: int | None = None
    fitBucket: str | None = None
    summary: str
    matchedMustHaves: list[str]
    strengths: list[str]
    missingRisks: list[str]
    reviewItemId: str | None = None
    evidenceLevel: WorkbenchCandidateEvidenceLevel | None = None
    detailOpenRequestId: str | None = None
    canExpandResume: bool
    canMarkPromising: bool
    canReject: bool
    canSaveNote: bool
    canRequestDetail: bool
    canOpenProvider: bool


class WorkbenchGraphCandidateNodeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    source: Literal["cts", "liepin", "all"]
    roundId: str | None = None
    nodeKind: WorkbenchGraphNodeKind


class WorkbenchGraphCandidateCoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceResultIdsSeen: list[str]
    missingSafeIdentityCount: int
    missingSnapshotCount: int
    forbiddenSnapshotCount: int
    droppedRows: int


class WorkbenchGraphCandidateListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodeId: str
    nodeScope: WorkbenchGraphCandidateNodeScope
    items: list[WorkbenchGraphCandidateSummaryResponse]
    nextCursor: str | None = None
    totalSourceResults: int
    totalGraphCandidates: int
    totalEstimate: int | None = None
    coverage: WorkbenchGraphCandidateCoverageResponse
    truncated: bool
    generatedAt: str
    recoveryState: WorkbenchGraphCandidateRecoveryState = "ready"
    recoveryReason: str | None = None


class WorkbenchResumeSnapshotProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str
    headline: str
    company: str
    location: str
    summary: str


class WorkbenchResumeSnapshotWorkExperienceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    title: str
    duration: str | None = None
    summary: str | None = None


class WorkbenchResumeSnapshotEducationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str
    degree: str | None = None
    major: str | None = None


class WorkbenchResumeSnapshotProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    summary: str | None = None


class WorkbenchResumeSnapshotSourceEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    text: str


class WorkbenchOriginalResumeFieldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: str


class WorkbenchOriginalResumeItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    fields: list[WorkbenchOriginalResumeFieldResponse] = Field(default_factory=list)


class WorkbenchOriginalResumeSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    items: list[WorkbenchOriginalResumeItemResponse] = Field(default_factory=list)


class WorkbenchOriginalResumeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: str
    sections: list[WorkbenchOriginalResumeSectionResponse] = Field(default_factory=list)


class WorkbenchGraphCandidateResumeSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graphCandidateId: str
    status: WorkbenchResumeSnapshotStatus
    reason: str | None = None
    sourceCompleteness: WorkbenchResumeSnapshotSourceCompleteness = "unavailable"
    originalResume: WorkbenchOriginalResumeResponse | None = None
    profile: WorkbenchResumeSnapshotProfileResponse | None = None
    workExperience: list[WorkbenchResumeSnapshotWorkExperienceResponse] = Field(default_factory=list)
    education: list[WorkbenchResumeSnapshotEducationResponse] = Field(default_factory=list)
    projects: list[WorkbenchResumeSnapshotProjectResponse] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    sourceEvidence: list[WorkbenchResumeSnapshotSourceEvidenceResponse] = Field(default_factory=list)


class WorkbenchCandidateReviewItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewItemId: str
    sessionId: str
    graphCandidateId: str | None = None
    canExpandResume: bool = False
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
