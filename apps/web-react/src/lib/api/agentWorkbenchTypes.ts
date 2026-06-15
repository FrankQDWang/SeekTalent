import type { components } from "./schema";

type Schemas = components["schemas"];

export type AgentWorkbenchStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentWorkbenchConversationSummary =
  Schemas["AgentWorkbenchConversationSummaryResponse"];
export type AgentWorkbenchMessage = Schemas["AgentWorkbenchMessageResponse"];
export type AgentWorkbenchActivity = Schemas["AgentWorkbenchActivityResponse"];
export type AgentWorkbenchCandidateSummary =
  Schemas["AgentWorkbenchCandidateSummaryResponse"];
export type AgentWorkbenchDetailApproval =
  Schemas["AgentWorkbenchDetailApprovalResponse"];
export type AgentWorkbenchFinalSummary =
  Schemas["AgentWorkbenchFinalSummaryResponse"];
export type AgentWorkbenchRequirementDraft =
  Schemas["AgentWorkbenchRequirementDraftResponse"];
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

export type AgentWorkbenchConversationResponse = Omit<
  Schemas["AgentWorkbenchConversationResponse"],
  | "messages"
  | "activities"
  | "transcriptGroups"
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

type GeneratedConversationResponse =
  Schemas["AgentWorkbenchConversationResponse"];
type GeneratedConversationListResponse =
  Schemas["AgentWorkbenchConversationListResponse"];

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
    },
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
