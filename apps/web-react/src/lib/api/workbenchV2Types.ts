import type { components } from "./schema";

type Schemas = components["schemas"];

export type WorkbenchV2CandidateSummary =
  Schemas["WorkbenchV2CandidateSummaryView"] & {
    sourceLabel?: string | null;
  };
export type WorkbenchV2CandidateDetail = Omit<
  Schemas["WorkbenchV2CandidateDetailView"],
  | "sections"
  | "sourceReferences"
  | "skills"
  | "evidence"
  | "workExperience"
  | "projectExperience"
  | "educationExperience"
> & {
  sections: Array<
    Omit<Schemas["WorkbenchV2CandidateDetailSectionView"], "items"> & {
      items: string[];
    }
  >;
  sourceReferences: Schemas["WorkbenchV2SourceReferenceView"][];
  skills: string[];
  evidence: string[];
  workExperience: Schemas["WorkbenchV2CandidateTimelineItemView"][];
  projectExperience: Schemas["WorkbenchV2CandidateTimelineItemView"][];
  educationExperience: Schemas["WorkbenchV2CandidateTimelineItemView"][];
};
export type WorkbenchV2StrategyGraph = Omit<
  Schemas["WorkbenchV2StrategyGraphView"],
  "nodes" | "edges"
> & {
  nodes: Schemas["WorkbenchV2GraphNodeView"][];
  edges: Schemas["WorkbenchV2GraphEdgeView"][];
};
export type WorkbenchV2QueryGroup = Omit<
  Schemas["WorkbenchV2QueryGroupView"],
  "queryTerms" | "executions"
> & {
  queryTerms: string[];
  executions: Schemas["WorkbenchV2QueryExecutionView"][];
};
export type WorkbenchV2ThinkingProcessCard = Omit<
  Schemas["WorkbenchV2ThinkingProcessCardView"],
  "terms"
> & {
  terms: string[];
};
export type WorkbenchV2ThinkingProcessRound = Omit<
  Schemas["WorkbenchV2ThinkingProcessRoundView"],
  "queryGroups" | "cards"
> & {
  queryGroups: WorkbenchV2QueryGroup[];
  cards: WorkbenchV2ThinkingProcessCard[];
};
export type WorkbenchV2ThinkingProcess = Omit<
  Schemas["WorkbenchV2ThinkingProcessView"],
  "rounds"
> & {
  rounds: WorkbenchV2ThinkingProcessRound[];
};
export type AgentWorkbenchQueryGroup = WorkbenchV2QueryGroup;
export type AgentWorkbenchThinkingProcessRound =
  WorkbenchV2ThinkingProcessRound;
export type AgentWorkbenchGraphNode = Schemas["WorkbenchV2GraphNodeView"];
export type AgentWorkbenchGraphEdge = Schemas["WorkbenchV2GraphEdgeView"];
export type WorkbenchV2GraphNode = Schemas["WorkbenchV2GraphNodeView"];
export type WorkbenchV2GraphEdge = Schemas["WorkbenchV2GraphEdgeView"];
export type WorkbenchV2TranscriptGroup = {
  groupId?: string;
  title?: string;
  events: WorkbenchV2TranscriptEvent[];
};
export type WorkbenchV2PendingActions = {
  allowed: string[];
};
export type WorkbenchV2RequirementDraftItem = {
  itemId: string;
  label: string;
  selected: boolean;
};
export type WorkbenchV2RequirementDraft = {
  sections: Array<{
    sectionId: string;
    title: string;
    items: WorkbenchV2RequirementDraftItem[];
  }>;
};
export type WorkbenchV2FinalSummary = Record<string, never>;
export type WorkbenchV2DetailApproval = {
  approvalId: string;
  candidateId: string;
  status: "pending" | "accepted" | "rejected" | "applied";
  reason: string;
};
export type WorkbenchV2CandidateSummaryLegacy = WorkbenchV2CandidateSummary;

export type WorkbenchV2EventType =
  | "user_message"
  | "assistant_message"
  | "assistant_status"
  | "requirement_form"
  | "requirement_form_confirmed"
  | "runtime_progress"
  | "runtime_result"
  | "error"
  | "context_summary";

export type WorkbenchV2Role = "user" | "assistant" | "system" | "runtime";

export type WorkbenchV2EventStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export type WorkbenchV2RuntimeState =
  | "idle"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkbenchV2Payload = Record<string, unknown>;

export type WorkbenchV2TranscriptEvent = {
  eventId: string;
  step: number;
  type: WorkbenchV2EventType;
  role: WorkbenchV2Role;
  status: WorkbenchV2EventStatus;
  payload: WorkbenchV2Payload;
  createdAt: string;
};

export type WorkbenchV2Conversation = {
  conversationId: string;
  title: string;
  runtimeState: WorkbenchV2RuntimeState;
  runtimeRunId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type WorkbenchV2Runtime = {
  state: WorkbenchV2RuntimeState;
  runtimeRunId: string | null;
};

export type WorkbenchV2ConversationView = {
  schemaVersion: "agent.workbench.v2";
  conversation: WorkbenchV2Conversation;
  transcriptEvents: WorkbenchV2TranscriptEvent[];
  requirementForm: WorkbenchV2Payload | null;
  runtime: WorkbenchV2Runtime | null;
  strategyGraph?: WorkbenchV2StrategyGraph;
  thinkingProcess?: WorkbenchV2ThinkingProcess;
  candidates?: WorkbenchV2CandidateSummary[];
};

export type WorkbenchV2ConversationListSummary = {
  conversationId: string;
  title: string;
  status: WorkbenchV2RuntimeState;
  updatedAt: string;
};

export type WorkbenchV2ConversationListView = {
  schemaVersion: "agent.workbench.v2.list";
  conversations: WorkbenchV2ConversationListSummary[];
};

export type WorkbenchV2ConversationEventsView = {
  schemaVersion: "agent.workbench.v2.events";
  conversationId: string;
  afterStep: number;
  latestStep: number;
  events: WorkbenchV2TranscriptEvent[];
};

export type WorkbenchV2MessageRequest = {
  message: string;
  idempotencyKey?: string | null;
};

export type WorkbenchV2RequirementActionRequest = {
  action: "set_selected" | "add_other" | "confirm";
  itemId?: string | null;
  selected?: boolean | null;
  text?: string | null;
  idempotencyKey?: string | null;
};

export type WorkbenchV2RuntimeRecheckRequest = {
  idempotencyKey: string;
};

type GeneratedWorkbenchV2StrategyGraph = Omit<
  WorkbenchV2StrategyGraph,
  "edges" | "nodes"
> & {
  edges?: WorkbenchV2StrategyGraph["edges"] | null;
  nodes?: WorkbenchV2StrategyGraph["nodes"] | null;
};

type GeneratedWorkbenchV2ThinkingProcessCard = Omit<
  Schemas["WorkbenchV2ThinkingProcessCardView"],
  "terms"
> & {
  terms?: string[] | null;
};

type GeneratedWorkbenchV2QueryExecution =
  Schemas["WorkbenchV2QueryExecutionView"];

type GeneratedWorkbenchV2QueryGroup = Omit<
  Schemas["WorkbenchV2QueryGroupView"],
  "executions" | "queryTerms"
> & {
  queryTerms?: string[] | null;
  executions?: GeneratedWorkbenchV2QueryExecution[] | null;
};

type GeneratedWorkbenchV2ThinkingProcessRound = Omit<
  Schemas["WorkbenchV2ThinkingProcessRoundView"],
  "cards" | "queryGroups"
> & {
  cards?: GeneratedWorkbenchV2ThinkingProcessCard[] | null;
  queryGroups?: GeneratedWorkbenchV2QueryGroup[] | null;
};

type GeneratedWorkbenchV2ThinkingProcess = Omit<
  Schemas["WorkbenchV2ThinkingProcessView"],
  "activeRoundNo" | "rounds"
> & {
  activeRoundNo?: number | null;
  rounds?: GeneratedWorkbenchV2ThinkingProcessRound[] | null;
};

type GeneratedWorkbenchV2ConversationView = Omit<
  WorkbenchV2ConversationView,
  "candidates" | "strategyGraph" | "thinkingProcess" | "transcriptEvents"
> & {
  candidates?: WorkbenchV2CandidateSummary[] | null;
  strategyGraph?: GeneratedWorkbenchV2StrategyGraph | null;
  thinkingProcess?: GeneratedWorkbenchV2ThinkingProcess | null;
  transcriptEvents?: WorkbenchV2TranscriptEvent[] | null;
};

type GeneratedWorkbenchV2ConversationListView = Omit<
  WorkbenchV2ConversationListView,
  "conversations"
> & {
  conversations?: WorkbenchV2ConversationListSummary[] | null;
};

type GeneratedWorkbenchV2ConversationEventsView = Omit<
  WorkbenchV2ConversationEventsView,
  "events"
> & {
  events?: WorkbenchV2TranscriptEvent[] | null;
};

export function normalizeWorkbenchV2Conversation(
  input: GeneratedWorkbenchV2ConversationView,
): WorkbenchV2ConversationView {
  return {
    ...input,
    transcriptEvents: [...(input.transcriptEvents ?? [])].sort(
      (left, right) => left.step - right.step,
    ),
    strategyGraph: normalizeWorkbenchV2StrategyGraph(input.strategyGraph),
    thinkingProcess: normalizeWorkbenchV2ThinkingProcess(input.thinkingProcess),
    candidates: (input.candidates ?? []).map(
      normalizeWorkbenchV2CandidateSummary,
    ),
  };
}

export function normalizeWorkbenchV2ConversationList(
  input: GeneratedWorkbenchV2ConversationListView,
): WorkbenchV2ConversationListView {
  return {
    ...input,
    conversations: [...(input.conversations ?? [])],
  };
}

export function normalizeWorkbenchV2ConversationEvents(
  input: GeneratedWorkbenchV2ConversationEventsView,
): WorkbenchV2ConversationEventsView {
  return {
    ...input,
    events: [...(input.events ?? [])].sort(
      (left, right) => left.step - right.step,
    ),
  };
}

function normalizeWorkbenchV2StrategyGraph(
  strategyGraph: GeneratedWorkbenchV2StrategyGraph | null | undefined,
): WorkbenchV2StrategyGraph {
  return {
    ...(strategyGraph ?? {}),
    nodes: strategyGraph?.nodes ?? [],
    edges: strategyGraph?.edges ?? [],
  };
}

function normalizeWorkbenchV2ThinkingProcess(
  thinkingProcess: GeneratedWorkbenchV2ThinkingProcess | null | undefined,
): WorkbenchV2ThinkingProcess {
  return {
    ...(thinkingProcess ?? {}),
    activeRoundNo: thinkingProcess?.activeRoundNo ?? null,
    rounds: (thinkingProcess?.rounds ?? []).map((round) => ({
      ...round,
      queryGroups: (round.queryGroups ?? []).map((queryGroup) => ({
        ...queryGroup,
        queryTerms: queryGroup.queryTerms ?? [],
        executions: (queryGroup.executions ?? []).map((execution) => ({
          ...execution,
          safeReasonCode: execution.safeReasonCode ?? null,
        })),
      })),
      cards: (round.cards ?? []).map((card) => ({
        ...card,
        terms: card.terms ?? [],
      })),
    })),
  };
}

export function normalizeWorkbenchV2CandidateSummary(
  candidate: WorkbenchV2CandidateSummary,
): WorkbenchV2CandidateSummary {
  return { ...candidate, sourceKinds: candidate.sourceKinds ?? [] };
}

export function normalizeWorkbenchV2CandidateDetail(
  detail: WorkbenchV2CandidateDetail,
): WorkbenchV2CandidateDetail {
  const payload = detail as Partial<WorkbenchV2CandidateDetail>;
  return {
    ...detail,
    sections: (payload.sections ?? []).map((section) => ({
      ...section,
      items: (section as Partial<typeof section>).items ?? [],
    })),
    sourceReferences: payload.sourceReferences ?? [],
    skills: payload.skills ?? [],
    evidence: payload.evidence ?? [],
    workExperience: payload.workExperience ?? [],
    projectExperience: payload.projectExperience ?? [],
    educationExperience: payload.educationExperience ?? [],
    match: detail.match
      ? {
          ...detail.match,
          strengths: detail.match.strengths ?? [],
          weaknesses: detail.match.weaknesses ?? [],
        }
      : null,
  };
}

export type AgentWorkbenchCandidateSummary = WorkbenchV2CandidateSummary;
export type AgentWorkbenchCandidateDetailResponse = WorkbenchV2CandidateDetail;
export type AgentWorkbenchStrategyGraph = WorkbenchV2StrategyGraph;
export type AgentWorkbenchThinkingProcess = WorkbenchV2ThinkingProcess;
export type AgentWorkbenchTranscriptEvent = WorkbenchV2TranscriptEvent;
export type AgentWorkbenchTranscriptGroup = WorkbenchV2TranscriptGroup;
export type AgentWorkbenchPendingActions = WorkbenchV2PendingActions;
export type AgentWorkbenchRequirementDraft = WorkbenchV2RequirementDraft;
export type AgentWorkbenchRequirementDraftItem =
  WorkbenchV2RequirementDraftItem;
export type AgentWorkbenchFinalSummary = WorkbenchV2FinalSummary;
export type AgentWorkbenchDetailApproval = WorkbenchV2DetailApproval;
export type AgentWorkbenchConversationResponse = WorkbenchV2ConversationView;
