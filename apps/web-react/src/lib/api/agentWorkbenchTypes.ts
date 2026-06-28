import type { components } from "./schema";

type Schemas = components["schemas"];

export type AgentWorkbenchStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "blocked"
  | "failed"
  | "cancelled";

export type AgentWorkbenchConversationSummary =
  Schemas["AgentWorkbenchConversationSummaryResponse"];
export type AgentWorkbenchMessage = Schemas["AgentWorkbenchMessageResponse"];
export type AgentWorkbenchActivity = Schemas["AgentWorkbenchActivityResponse"];
export type AgentWorkbenchCandidateSummary =
  Schemas["AgentWorkbenchCandidateSummaryResponse"] & {
    activeStatus?: string | null;
    age?: number | null;
    gender?: string | null;
    jobStatus?: string | null;
  };
export type AgentWorkbenchCandidateDetailSection = Omit<
  Schemas["AgentWorkbenchCandidateDetailSectionResponse"],
  "items"
> & {
  items: string[];
};
export type AgentWorkbenchDetailApproval =
  Schemas["AgentWorkbenchDetailApprovalResponse"];
export type AgentWorkbenchFinalSummary =
  Schemas["AgentWorkbenchFinalSummaryResponse"];
export type AgentWorkbenchRuntime = Schemas["AgentWorkbenchRuntimeResponse"];
export type AgentWorkbenchSourceConnection =
  Schemas["AgentWorkbenchSourceConnectionResponse"];
export type AgentWorkbenchReviewArtifact =
  Schemas["AgentWorkbenchReviewArtifactResponse"];
export type AgentWorkbenchStreamCursor =
  Schemas["AgentWorkbenchStreamCursorResponse"];

export type AgentWorkbenchPendingActions = Omit<
  Schemas["AgentWorkbenchPendingActionsResponse"],
  "allowed"
> & {
  allowed: string[];
};

export type AgentWorkbenchTranscriptPayload =
  Schemas["AgentWorkbenchTranscriptPayloadResponse"];
export type AgentWorkbenchStreamPayload = NonNullable<
  Schemas["AgentWorkbenchStreamEnvelopeResponse"]["payload"]
>;
export type AgentWorkbenchStreamKind =
  Schemas["AgentWorkbenchStreamEnvelopeResponse"]["kind"];

export type AgentWorkbenchStreamEnvelope = Omit<
  Schemas["AgentWorkbenchStreamEnvelopeResponse"],
  "payload"
> & {
  payload: AgentWorkbenchStreamPayload;
};

export type AgentWorkbenchTranscriptEvent = Omit<
  Schemas["AgentWorkbenchTranscriptEventResponse"],
  "payload"
> & {
  payload: AgentWorkbenchTranscriptPayload;
};

export type AgentWorkbenchTranscriptGroup = Omit<
  Schemas["AgentWorkbenchTranscriptGroupResponse"],
  "events"
> & {
  events: AgentWorkbenchTranscriptEvent[];
};

export type AgentWorkbenchGraphNode =
  Schemas["AgentWorkbenchGraphNodeResponse"];
export type AgentWorkbenchGraphEdge =
  Schemas["AgentWorkbenchGraphEdgeResponse"];

export type AgentWorkbenchStrategyGraph = {
  nodes: AgentWorkbenchGraphNode[];
  edges: AgentWorkbenchGraphEdge[];
};

export type AgentWorkbenchThinkingProcessCard = Omit<
  Schemas["AgentWorkbenchThinkingProcessCardResponse"],
  "terms"
> & {
  terms: string[];
};

export type AgentWorkbenchThinkingProcessRound = Omit<
  Schemas["AgentWorkbenchThinkingProcessRoundResponse"],
  "cards"
> & {
  cards: AgentWorkbenchThinkingProcessCard[];
};

export type AgentWorkbenchThinkingProcess = Omit<
  Schemas["AgentWorkbenchThinkingProcessResponse"],
  "rounds"
> & {
  activeRoundNo: number | null;
  rounds: AgentWorkbenchThinkingProcessRound[];
};

export type AgentWorkbenchRequirementDraftItem = Omit<
  Schemas["AgentWorkbenchRequirementDraftItemResponse"],
  "allowedActions"
> & {
  allowedActions: string[];
};

export type AgentWorkbenchRequirementDraftSection = Omit<
  Schemas["AgentWorkbenchRequirementDraftSectionResponse"],
  "items"
> & {
  items: AgentWorkbenchRequirementDraftItem[];
};

export type AgentWorkbenchRequirementDraft = Omit<
  Schemas["AgentWorkbenchRequirementDraftResponse"],
  "sections"
> & {
  sections: AgentWorkbenchRequirementDraftSection[];
};

export type AgentWorkbenchConversationResponse = Omit<
  Schemas["AgentWorkbenchConversationResponse"],
  | "messages"
  | "activities"
  | "transcriptGroups"
  | "requirementDraft"
  | "strategyGraph"
  | "thinkingProcess"
  | "sourceConnections"
  | "candidates"
  | "detailApprovals"
  | "reviewArtifacts"
  | "pendingActions"
  | "streamCursor"
> & {
  messages: AgentWorkbenchMessage[];
  activities: AgentWorkbenchActivity[];
  transcriptGroups: AgentWorkbenchTranscriptGroup[];
  requirementDraft: AgentWorkbenchRequirementDraft | null;
  strategyGraph: AgentWorkbenchStrategyGraph;
  thinkingProcess: AgentWorkbenchThinkingProcess;
  sourceConnections: AgentWorkbenchSourceConnection[];
  candidates: AgentWorkbenchCandidateSummary[];
  detailApprovals: AgentWorkbenchDetailApproval[];
  reviewArtifacts: AgentWorkbenchReviewArtifact[];
  pendingActions: AgentWorkbenchPendingActions;
  streamCursor: AgentWorkbenchStreamCursor;
};

export type AgentWorkbenchConversationListResponse = {
  conversations: AgentWorkbenchConversationSummary[];
};

export type AgentWorkbenchCandidateDetailResponse = Omit<
  Schemas["AgentWorkbenchCandidateDetailResponse"],
  "sections" | "evidence"
> & {
  activeStatus?: string | null;
  age?: number | null;
  company?: string | null;
  education?: string | null;
  sections: AgentWorkbenchCandidateDetailSection[];
  evidence: string[];
  experienceYears?: number | null;
  gender?: string | null;
  jobStatus?: string | null;
  location?: string | null;
};

export type WorkbenchUserTextMessageRequest =
  Schemas["WorkbenchUserTextMessageRequest"];
export type WorkbenchConversationFromJdRequest = Omit<
  Schemas["WorkbenchConversationFromJdRequest"],
  "sourceKinds"
> & {
  sourceKinds?: ("cts" | "liepin")[] | null;
};
export type WorkbenchAgentMessageRequest = WorkbenchUserTextMessageRequest;
export type WorkbenchConversationCreateRequest =
  Schemas["WorkbenchConversationCreateRequest"];
export type WorkbenchRequirementConfirmRequest =
  Schemas["WorkbenchRequirementConfirmRequest"];
export type WorkbenchRequirementOperationsRequest =
  Schemas["WorkbenchRequirementOperationsRequest"];
export type WorkbenchRequirementAmendRequest =
  Schemas["WorkbenchRequirementAmendRequest"];
export type RequirementDraftOperationRequest =
  Schemas["RequirementDraftOperationRequest"];

type GeneratedConversationResponse =
  Schemas["AgentWorkbenchConversationResponse"];
type GeneratedConversationListResponse =
  Schemas["AgentWorkbenchConversationListResponse"];
type GeneratedCandidateDetailResponse =
  Schemas["AgentWorkbenchCandidateDetailResponse"];

export function normalizeAgentWorkbenchConversationList(
  response: GeneratedConversationListResponse,
): AgentWorkbenchConversationListResponse {
  return {
    conversations: response.conversations ?? [],
  };
}

export function normalizeAgentWorkbenchConversation(
  response: GeneratedConversationResponse,
): AgentWorkbenchConversationResponse {
  const streamCursor = response.streamCursor;
  return {
    ...response,
    messages: response.messages ?? [],
    activities: response.activities ?? [],
    transcriptGroups: normalizeTranscriptGroups(
      response.transcriptGroups ?? [],
    ),
    requirementDraft: normalizeRequirementDraft(response.requirementDraft),
    strategyGraph: {
      nodes: response.strategyGraph.nodes ?? [],
      edges: response.strategyGraph.edges ?? [],
    },
    thinkingProcess: normalizeThinkingProcess(response.thinkingProcess),
    sourceConnections: response.sourceConnections ?? [],
    candidates: response.candidates ?? [],
    detailApprovals: response.detailApprovals ?? [],
    reviewArtifacts: response.reviewArtifacts ?? [],
    pendingActions: {
      ...response.pendingActions,
      allowed: response.pendingActions.allowed ?? [],
    },
    streamCursor: {
      ...streamCursor,
      latestStreamSeq: streamCursor.latestStreamSeq,
      snapshotSeq: streamCursor.snapshotSeq,
      viewRevision: streamCursor.viewRevision,
    },
  };
}

export function normalizeAgentWorkbenchCandidateDetail(
  response: GeneratedCandidateDetailResponse,
): AgentWorkbenchCandidateDetailResponse {
  return {
    ...response,
    sourceKinds: response.sourceKinds ?? [],
    sections: (response.sections ?? []).map((section) => ({
      ...section,
      items: section.items ?? [],
    })),
    evidence: response.evidence ?? [],
  };
}

function normalizeTranscriptGroups(
  groups: Schemas["AgentWorkbenchTranscriptGroupResponse"][],
): AgentWorkbenchTranscriptGroup[] {
  return groups.map((group) => ({
    ...group,
    events: (group.events ?? []).map((event) => ({
      ...event,
      payload: event.payload ?? { kind: "empty" },
    })),
  }));
}

function normalizeThinkingProcess(
  thinkingProcess: Schemas["AgentWorkbenchThinkingProcessResponse"],
): AgentWorkbenchThinkingProcess {
  return {
    ...thinkingProcess,
    activeRoundNo: thinkingProcess.activeRoundNo ?? null,
    rounds: (thinkingProcess.rounds ?? []).map((round) => ({
      ...round,
      cards: (round.cards ?? []).map((card) => ({
        ...card,
        terms: card.terms ?? [],
      })),
    })),
  };
}

function normalizeRequirementDraft(
  requirementDraft:
    | Schemas["AgentWorkbenchRequirementDraftResponse"]
    | null
    | undefined,
): AgentWorkbenchRequirementDraft | null {
  if (!requirementDraft) {
    return null;
  }
  return {
    ...requirementDraft,
    sections: (requirementDraft.sections ?? []).map((section) => ({
      ...section,
      items: (section.items ?? []).map((item) => ({
        ...item,
        allowedActions: item.allowedActions ?? [],
      })),
    })),
  };
}
