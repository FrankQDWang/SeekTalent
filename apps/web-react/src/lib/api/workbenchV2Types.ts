import {
  normalizeAgentWorkbenchCandidateSummary,
  type AgentWorkbenchCandidateSummary,
  type AgentWorkbenchStrategyGraph,
  type AgentWorkbenchThinkingProcess,
} from "./agentWorkbenchTypes";

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
  strategyGraph?: AgentWorkbenchStrategyGraph;
  thinkingProcess?: AgentWorkbenchThinkingProcess;
  candidates?: AgentWorkbenchCandidateSummary[];
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

export function normalizeWorkbenchV2Conversation(
  input: WorkbenchV2ConversationView,
): WorkbenchV2ConversationView {
  return {
    ...input,
    transcriptEvents: [...input.transcriptEvents].sort(
      (left, right) => left.step - right.step,
    ),
    strategyGraph: input.strategyGraph ?? { nodes: [], edges: [] },
    thinkingProcess: input.thinkingProcess ?? {
      activeRoundNo: null,
      rounds: [],
    },
    candidates: (input.candidates ?? []).map(
      normalizeAgentWorkbenchCandidateSummary,
    ),
  };
}

export function normalizeWorkbenchV2ConversationList(
  input: WorkbenchV2ConversationListView,
): WorkbenchV2ConversationListView {
  return {
    ...input,
    conversations: [...input.conversations],
  };
}

export function normalizeWorkbenchV2ConversationEvents(
  input: WorkbenchV2ConversationEventsView,
): WorkbenchV2ConversationEventsView {
  return {
    ...input,
    events: [...input.events].sort((left, right) => left.step - right.step),
  };
}
