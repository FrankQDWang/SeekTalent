from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentWorkbenchStatus = Literal["pending", "running", "completed", "partial", "blocked", "failed", "cancelled"]
AgentWorkbenchDetailApprovalStatus = Literal["pending", "accepted", "rejected", "applied"]
AgentWorkbenchStreamKind = Literal[
    "item.started",
    "item.completed",
    "message.created",
    "message.delta",
    "message.completed",
    "activity.upserted",
    "requirement.updated",
    "runtime.eventProjected",
    "strategyGraph.changed",
    "operation.started",
    "operation.completed",
    "operation.failed",
    "sourceSearch.started",
    "sourceSearch.completed",
    "sourceSearch.failed",
    "webSearch.started",
    "webSearch.completed",
    "command.started",
    "command.outputDelta",
    "command.completed",
    "command.failed",
    "runtime.stageChanged",
    "candidate.upserted",
    "detailApproval.changed",
    "finalSummary.updated",
    "runtimeFinalization.changed",
    "pendingAction.changed",
    "sourceConnection.changed",
    "context.compacted",
    "transcript.groupCollapsed",
    "thinkingProcess.changed",
    "stream.gap",
]
AgentWorkbenchMessageStreamPayloadType = Literal["message.created", "message.delta", "message.completed"]
AgentWorkbenchItemStreamPayloadType = Literal[
    "item.started",
    "item.completed",
    "requirement.updated",
    "runtime.eventProjected",
    "strategyGraph.changed",
    "operation.started",
    "operation.completed",
    "operation.failed",
    "sourceSearch.started",
    "sourceSearch.completed",
    "sourceSearch.failed",
    "webSearch.started",
    "webSearch.completed",
    "command.started",
    "command.outputDelta",
    "command.completed",
    "command.failed",
    "runtime.stageChanged",
    "candidate.upserted",
    "detailApproval.changed",
    "finalSummary.updated",
    "runtimeFinalization.changed",
    "pendingAction.changed",
    "sourceConnection.changed",
    "context.compacted",
    "transcript.groupCollapsed",
    "thinkingProcess.changed",
]


class AgentWorkbenchMessagePayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["empty", "job_request", "requirement_review"] = "empty"
    jobTitle: str | None = None
    notes: str | None = None
    sourceKinds: list[Literal["cts", "liepin"]] = Field(default_factory=list)
    requirementDraftId: str | None = None
    requirementDraftSnapshot: AgentWorkbenchRequirementDraftResponse | None = None


class AgentWorkbenchLinkedRuntimeRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str
    status: AgentWorkbenchStatus
    runKind: str
    workbenchSessionId: str | None = None
    approvedRequirementRevisionId: str
    runIntentId: str | None = None
    linkReason: str
    latestEventSeq: int
    linkedAt: str
    updatedAt: str
    activeAt: str | None = None
    supersededAt: str | None = None
    completedAt: str | None = None
    isActive: bool = False


class AgentWorkbenchConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversationId: str
    title: str
    status: str
    isArchived: bool
    runtimeRunId: str | None = None
    workbenchSessionId: str | None = None
    workflowStartIntentId: str | None = None
    workflowStartState: Literal["not_started", "queued", "starting", "running", "failed"] = "not_started"
    workflowStartReasonCode: str | None = None
    linkedRuntimeRuns: list[AgentWorkbenchLinkedRuntimeRunResponse] = Field(default_factory=list)
    updatedAt: str | None = None


class AgentWorkbenchConversationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: list[AgentWorkbenchConversationSummaryResponse] = Field(default_factory=list)


class AgentWorkbenchMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageId: str
    seq: int
    role: Literal["user", "assistant", "system"]
    messageType: str
    text: str
    payload: AgentWorkbenchMessagePayloadResponse = Field(default_factory=AgentWorkbenchMessagePayloadResponse)
    createdAt: str


class AgentWorkbenchQueryPackageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: str | None = None
    queryRole: str | None = None
    laneType: str | None = None
    queryTerms: list[str] = Field(default_factory=list)
    keywordQuery: str | None = None


class AgentWorkbenchActivityPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["runtime_round", "runtime_event", "operation_event", "empty"] = "empty"
    stage: str | None = None
    sourceId: str | None = None
    status: AgentWorkbenchStatus | None = None
    roundNo: int | None = None
    queryTerms: list[str] = Field(default_factory=list)
    keywordQuery: str | None = None
    plannedQueries: list[AgentWorkbenchQueryPackageResponse] = Field(default_factory=list)
    executedQueries: list[AgentWorkbenchQueryPackageResponse] = Field(default_factory=list)
    executedQueryTerms: list[list[str]] = Field(default_factory=list)
    rawCandidateCount: int | None = None
    uniqueNewCount: int | None = None
    totalMergedIdentityCount: int | None = None
    newlyScoredCount: int | None = None
    topPoolCount: int | None = None
    resumeQualityComment: str | None = None
    reflectionSummary: str | None = None
    suggestedActivateTerms: list[str] = Field(default_factory=list)
    suggestedKeepTerms: list[str] = Field(default_factory=list)
    suggestedDeprioritizeTerms: list[str] = Field(default_factory=list)
    suggestedDropTerms: list[str] = Field(default_factory=list)
    suggestedAddFilterFields: list[str] = Field(default_factory=list)
    suggestedKeepFilterFields: list[str] = Field(default_factory=list)
    suggestedDropFilterFields: list[str] = Field(default_factory=list)


class AgentWorkbenchActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activityId: str
    seq: int
    activityType: str
    status: str
    title: str
    summary: str
    sourceRuntimeRunId: str | None = None
    payload: AgentWorkbenchActivityPayloadResponse = Field(default_factory=AgentWorkbenchActivityPayloadResponse)
    updatedAt: str


class AgentWorkbenchTranscriptPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "message",
        "activity",
        "operation",
        "command",
        "source_search",
        "runtime_stage",
        "candidate",
        "approval",
        "artifact",
        "final_summary",
        "runtime_finalization",
        "strategy_graph",
        "pending_action",
        "source_connection",
        "thinking_process",
        "context",
        "gap",
        "empty",
    ] = "empty"
    messageId: str | None = None
    activityId: str | None = None
    activitySeq: int | None = None
    activityType: str | None = None
    sourceRuntimeRunId: str | None = None
    itemId: str | None = None
    delta: str | None = None
    summary: str | None = None
    missingFromSeq: int | None = None
    nextAvailableSeq: int | None = None


class AgentWorkbenchMessageStreamPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payloadType: AgentWorkbenchMessageStreamPayloadType
    kind: Literal["message"] = "message"
    messageId: str
    delta: str | None = None
    summary: str | None = None


class AgentWorkbenchActivityStreamPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payloadType: Literal["activity.upserted"]
    kind: Literal["activity"] = "activity"
    activityId: str
    activitySeq: int | None = None
    activityType: str | None = None
    sourceRuntimeRunId: str | None = None
    summary: str | None = None


class AgentWorkbenchItemStreamPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payloadType: AgentWorkbenchItemStreamPayloadType
    kind: Literal[
        "operation",
        "command",
        "source_search",
        "runtime_stage",
        "candidate",
        "approval",
        "artifact",
        "final_summary",
        "runtime_finalization",
        "strategy_graph",
        "pending_action",
        "source_connection",
        "thinking_process",
        "context",
    ]
    itemId: str
    delta: str | None = None
    sourceRuntimeRunId: str | None = None
    summary: str | None = None
    graphNodeCount: int | None = None
    graphEdgeCount: int | None = None
    roundNo: int | None = None
    activeRoundNo: int | None = None
    status: AgentWorkbenchStatus | None = None


class AgentWorkbenchGapStreamPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payloadType: Literal["stream.gap"] = "stream.gap"
    kind: Literal["gap"] = "gap"
    missingFromSeq: int
    nextAvailableSeq: int
    summary: str | None = None


AgentWorkbenchStreamPayloadResponse = Annotated[
    AgentWorkbenchMessageStreamPayloadResponse
    | AgentWorkbenchActivityStreamPayloadResponse
    | AgentWorkbenchItemStreamPayloadResponse
    | AgentWorkbenchGapStreamPayloadResponse,
    Field(discriminator="payloadType"),
]


def agent_workbench_stream_payload_from_transcript_payload(
    payload: AgentWorkbenchTranscriptPayloadResponse,
    stream_kind: AgentWorkbenchStreamKind,
) -> AgentWorkbenchStreamPayloadResponse:
    if payload.kind == "empty":
        return _empty_stream_payload_for_kind(stream_kind)
    if payload.kind == "message":
        return AgentWorkbenchMessageStreamPayloadResponse(
            payloadType=_message_stream_payload_type(stream_kind),
            messageId=payload.messageId or payload.itemId or "",
            delta=payload.delta,
            summary=payload.summary,
        )
    if payload.kind == "activity":
        return AgentWorkbenchActivityStreamPayloadResponse(
            payloadType="activity.upserted",
            activityId=payload.activityId or payload.itemId or "",
            activitySeq=payload.activitySeq,
            activityType=payload.activityType,
            sourceRuntimeRunId=payload.sourceRuntimeRunId,
            summary=payload.summary,
        )
    if payload.kind == "gap":
        return AgentWorkbenchGapStreamPayloadResponse(
            missingFromSeq=payload.missingFromSeq or 0,
            nextAvailableSeq=payload.nextAvailableSeq or 0,
            summary=payload.summary,
        )
    return AgentWorkbenchItemStreamPayloadResponse(
        payloadType=_item_stream_payload_type(stream_kind),
        kind=payload.kind,
        itemId=payload.itemId or payload.activityId or payload.messageId or payload.kind,
        delta=payload.delta,
        sourceRuntimeRunId=payload.sourceRuntimeRunId,
        summary=payload.summary,
    )


def normalize_agent_workbench_stream_payload(
    payload: AgentWorkbenchStreamPayloadResponse | AgentWorkbenchTranscriptPayloadResponse,
    stream_kind: AgentWorkbenchStreamKind,
) -> AgentWorkbenchStreamPayloadResponse:
    if isinstance(payload, AgentWorkbenchTranscriptPayloadResponse):
        return agent_workbench_stream_payload_from_transcript_payload(payload, stream_kind)
    return payload


def _message_stream_payload_type(stream_kind: AgentWorkbenchStreamKind) -> AgentWorkbenchMessageStreamPayloadType:
    if stream_kind == "message.created":
        return "message.created"
    if stream_kind == "message.delta":
        return "message.delta"
    if stream_kind == "message.completed":
        return "message.completed"
    return "message.completed"


def _item_stream_payload_type(stream_kind: AgentWorkbenchStreamKind) -> AgentWorkbenchItemStreamPayloadType:
    match stream_kind:
        case "item.started":
            return "item.started"
        case "item.completed":
            return "item.completed"
        case "requirement.updated":
            return "requirement.updated"
        case "runtime.eventProjected":
            return "runtime.eventProjected"
        case "strategyGraph.changed":
            return "strategyGraph.changed"
        case "operation.started":
            return "operation.started"
        case "operation.completed":
            return "operation.completed"
        case "operation.failed":
            return "operation.failed"
        case "sourceSearch.started":
            return "sourceSearch.started"
        case "sourceSearch.completed":
            return "sourceSearch.completed"
        case "sourceSearch.failed":
            return "sourceSearch.failed"
        case "webSearch.started":
            return "webSearch.started"
        case "webSearch.completed":
            return "webSearch.completed"
        case "command.started":
            return "command.started"
        case "command.outputDelta":
            return "command.outputDelta"
        case "command.completed":
            return "command.completed"
        case "command.failed":
            return "command.failed"
        case "runtime.stageChanged":
            return "runtime.stageChanged"
        case "candidate.upserted":
            return "candidate.upserted"
        case "detailApproval.changed":
            return "detailApproval.changed"
        case "finalSummary.updated":
            return "finalSummary.updated"
        case "runtimeFinalization.changed":
            return "runtimeFinalization.changed"
        case "pendingAction.changed":
            return "pendingAction.changed"
        case "sourceConnection.changed":
            return "sourceConnection.changed"
        case "context.compacted":
            return "context.compacted"
        case "transcript.groupCollapsed":
            return "transcript.groupCollapsed"
        case "thinkingProcess.changed":
            return "thinkingProcess.changed"
        case _:
            return "runtime.eventProjected"


def _empty_stream_payload_for_kind(stream_kind: AgentWorkbenchStreamKind) -> AgentWorkbenchStreamPayloadResponse:
    if stream_kind in {"message.created", "message.delta", "message.completed"}:
        return AgentWorkbenchMessageStreamPayloadResponse(
            payloadType=_message_stream_payload_type(stream_kind),
            messageId="",
        )
    if stream_kind == "activity.upserted":
        return AgentWorkbenchActivityStreamPayloadResponse(
            payloadType="activity.upserted",
            activityId="",
        )
    if stream_kind == "stream.gap":
        return AgentWorkbenchGapStreamPayloadResponse(
            missingFromSeq=0,
            nextAvailableSeq=0,
        )
    return AgentWorkbenchItemStreamPayloadResponse(
        payloadType=_item_stream_payload_type(stream_kind),
        kind=_item_payload_kind_for_stream_kind(stream_kind),
        itemId=stream_kind,
    )


def _item_payload_kind_for_stream_kind(stream_kind: AgentWorkbenchStreamKind) -> Literal[
    "operation",
    "command",
    "source_search",
    "runtime_stage",
    "candidate",
    "approval",
    "artifact",
    "final_summary",
    "runtime_finalization",
    "strategy_graph",
    "pending_action",
    "source_connection",
    "thinking_process",
    "context",
]:
    if stream_kind.startswith("operation."):
        return "operation"
    if stream_kind.startswith("command."):
        return "command"
    if stream_kind.startswith("sourceSearch.") or stream_kind.startswith("webSearch."):
        return "source_search"
    if stream_kind == "runtime.stageChanged":
        return "runtime_stage"
    if stream_kind == "candidate.upserted":
        return "candidate"
    if stream_kind == "detailApproval.changed":
        return "approval"
    if stream_kind == "finalSummary.updated":
        return "final_summary"
    if stream_kind == "runtimeFinalization.changed":
        return "runtime_finalization"
    if stream_kind == "strategyGraph.changed":
        return "strategy_graph"
    if stream_kind == "pendingAction.changed":
        return "pending_action"
    if stream_kind == "sourceConnection.changed":
        return "source_connection"
    if stream_kind == "thinkingProcess.changed":
        return "thinking_process"
    if stream_kind == "context.compacted":
        return "context"
    return "artifact"


class AgentWorkbenchTranscriptEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    itemId: str
    kind: AgentWorkbenchStreamKind
    status: AgentWorkbenchStatus | None = None
    label: str
    summary: str | None = None
    payload: AgentWorkbenchTranscriptPayloadResponse = Field(default_factory=AgentWorkbenchTranscriptPayloadResponse)
    createdAt: str


class AgentWorkbenchTranscriptGroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groupId: str
    title: str
    status: AgentWorkbenchStatus
    startedAt: str | None = None
    completedAt: str | None = None
    events: list[AgentWorkbenchTranscriptEventResponse] = Field(default_factory=list)


class AgentWorkbenchGraphNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodeId: str
    kind: Literal["requirements", "message", "activity", "candidate", "approval", "final", "lane", "phase"]
    label: str
    summary: str
    roundNo: int | None = None
    laneType: str | None = None
    phase: str | None = None
    stage: str | None = None
    status: AgentWorkbenchStatus
    sourceKind: Literal["cts", "liepin", "all"] = "all"
    activityId: str | None = None
    messageId: str | None = None


class AgentWorkbenchGraphEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edgeId: str
    fromNodeId: str
    toNodeId: str
    label: str | None = None


class AgentWorkbenchStrategyGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[AgentWorkbenchGraphNodeResponse] = Field(default_factory=list)
    edges: list[AgentWorkbenchGraphEdgeResponse] = Field(default_factory=list)


AgentWorkbenchRequirementItemStatus = Literal["resolved", "needs_review", "deleted", "moved", "rejected", "unknown"]
AgentWorkbenchRequirementDraftStatus = Literal["draft_ready", "needs_review", "unknown"]


class AgentWorkbenchRequirementDraftItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itemId: str
    sectionId: str
    selected: bool
    enabled: bool
    editable: bool
    text: str
    status: AgentWorkbenchRequirementItemStatus
    source: str
    allowedActions: list[str] = Field(default_factory=list)


class AgentWorkbenchRequirementDraftSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sectionId: str
    displayName: str
    backendField: str
    items: list[AgentWorkbenchRequirementDraftItemResponse] = Field(default_factory=list)


class AgentWorkbenchRequirementDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str
    parentDraftRevisionId: str | None = None
    status: AgentWorkbenchRequirementDraftStatus
    title: str
    summary: str
    canConfirm: bool
    unresolvedReviewItemCount: int
    sections: list[AgentWorkbenchRequirementDraftSectionResponse] = Field(default_factory=list)
    otherInputPrompt: str = "其他"


class AgentWorkbenchRuntimeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str
    status: str
    currentStage: str
    currentRound: int | None = None
    latestEventSeq: int


class AgentWorkbenchThinkingProcessCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Literal["关键词", "observation", "反思和下一轮变更"]
    text: str
    terms: list[str] = Field(default_factory=list)


class AgentWorkbenchThinkingProcessRoundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roundNo: int
    status: AgentWorkbenchStatus
    cards: list[AgentWorkbenchThinkingProcessCardResponse] = Field(default_factory=list)


class AgentWorkbenchThinkingProcessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activeRoundNo: int | None = None
    rounds: list[AgentWorkbenchThinkingProcessRoundResponse] = Field(default_factory=list)


class AgentWorkbenchCandidateSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    rank: int
    displayName: str
    headline: str | None = None
    company: str | None = None
    location: str | None = None
    education: str | None = None
    experienceYears: int | None = None
    sourceKinds: list[Literal["cts", "liepin"]] = Field(default_factory=list)
    matchScore: int | None = Field(default=None, ge=0, le=100)
    matchSummary: str | None = None
    status: str
    detailAvailability: Literal["available", "redacted", "approval_required", "unavailable"]
    accessState: Literal["allowed", "redacted", "approval_required", "denied"]
    evidenceLevel: Literal["summary", "detail", "final", "unknown"]


class AgentWorkbenchCandidateDetailSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    items: list[str] = Field(default_factory=list)


class AgentWorkbenchCandidateDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    displayName: str
    headline: str | None = None
    sourceKinds: list[Literal["cts", "liepin"]] = Field(default_factory=list)
    matchScore: int | None = Field(default=None, ge=0, le=100)
    sections: list[AgentWorkbenchCandidateDetailSectionResponse] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    detailAvailability: Literal["available", "redacted", "approval_required", "unavailable"]
    accessState: Literal["allowed", "redacted", "approval_required", "denied"]
    evidenceLevel: Literal["summary", "detail", "final", "unknown"]
    reasonCode: str | None = None


class AgentWorkbenchDetailApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approvalId: str
    candidateId: str
    status: AgentWorkbenchDetailApprovalStatus
    reason: str


class AgentWorkbenchFinalSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summaryId: str
    text: str


class AgentWorkbenchRunFinalizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selectedIdentityCount: int | None = None
    revision: int | None = None
    reasonCode: str | None = None
    status: AgentWorkbenchStatus = "completed"


class AgentWorkbenchReviewArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifactId: str
    title: str
    artifactKind: Literal["source_evidence", "approval", "final_output", "stream_recovery"]
    safeSummary: str


class AgentWorkbenchSourceConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKind: Literal["cts", "liepin"]
    status: Literal["connected", "disconnected", "expired", "unknown"]
    displayName: str
    lastCheckedAt: str | None = None


class AgentWorkbenchPendingActionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str | None = None
    allowed: list[str] = Field(default_factory=list)
    pendingCommandCount: int = 0
    pendingRequirementReviewCount: int = 0
    pendingMemoryReviewCount: int = 0


class AgentWorkbenchStreamCursorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latestMessageSeq: int = 0
    latestActivitySeq: int = 0
    latestRuntimeEventSeq: int = 0
    latestStreamSeq: int = 0
    snapshotSeq: int = 0
    viewRevision: int = 0


class AgentWorkbenchConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.view.v2"] = "agent.workbench.view.v2"
    conversation: AgentWorkbenchConversationSummaryResponse
    messages: list[AgentWorkbenchMessageResponse] = Field(default_factory=list)
    activities: list[AgentWorkbenchActivityResponse] = Field(default_factory=list)
    transcriptGroups: list[AgentWorkbenchTranscriptGroupResponse] = Field(default_factory=list)
    requirementDraft: AgentWorkbenchRequirementDraftResponse | None = None
    runtime: AgentWorkbenchRuntimeResponse | None = None
    strategyGraph: AgentWorkbenchStrategyGraphResponse
    thinkingProcess: AgentWorkbenchThinkingProcessResponse
    sourceConnections: list[AgentWorkbenchSourceConnectionResponse] = Field(default_factory=list)
    candidates: list[AgentWorkbenchCandidateSummaryResponse] = Field(default_factory=list)
    detailApprovals: list[AgentWorkbenchDetailApprovalResponse] = Field(default_factory=list)
    reviewArtifacts: list[AgentWorkbenchReviewArtifactResponse] = Field(default_factory=list)
    runtimeFinalization: AgentWorkbenchRunFinalizationResponse | None = None
    finalSummary: AgentWorkbenchFinalSummaryResponse | None = None
    pendingActions: AgentWorkbenchPendingActionsResponse
    streamCursor: AgentWorkbenchStreamCursorResponse
    reasonCode: str | None = None


class AgentWorkbenchStreamEnvelopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.stream.v1"] = "agent.workbench.stream.v1"
    conversationId: str
    seq: int
    kind: AgentWorkbenchStreamKind
    payload: AgentWorkbenchStreamPayloadResponse
    createdAt: str

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> AgentWorkbenchStreamEnvelopeResponse:
        if self.payload.payloadType != self.kind:
            raise ValueError("stream payloadType must match envelope kind")
        return self


class AgentWorkbenchStreamReplayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.stream.replay.v1"] = "agent.workbench.stream.replay.v1"
    conversationId: str
    events: list[AgentWorkbenchStreamEnvelopeResponse] = Field(default_factory=list)
    latestSeq: int
    hasMore: bool = False
    nextAfterSeq: int | None = None
