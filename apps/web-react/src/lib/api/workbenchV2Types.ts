import {
  normalizeAgentWorkbenchCandidateSummary,
  type AgentWorkbenchCandidateSummary,
  type AgentWorkbenchStrategyGraph,
  type AgentWorkbenchThinkingProcess,
} from "./agentWorkbenchTypes";
import type { components } from "./schema";

type Schemas = components["schemas"];

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

type GeneratedWorkbenchV2StrategyGraph = Omit<
  AgentWorkbenchStrategyGraph,
  "edges" | "nodes"
> & {
  edges?: AgentWorkbenchStrategyGraph["edges"] | null;
  nodes?: AgentWorkbenchStrategyGraph["nodes"] | null;
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
  candidates?: AgentWorkbenchCandidateSummary[] | null;
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
      normalizeAgentWorkbenchCandidateSummary,
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
): AgentWorkbenchStrategyGraph {
  return {
    ...(strategyGraph ?? {}),
    nodes: strategyGraph?.nodes ?? [],
    edges: strategyGraph?.edges ?? [],
  };
}

function normalizeWorkbenchV2ThinkingProcess(
  thinkingProcess: GeneratedWorkbenchV2ThinkingProcess | null | undefined,
): AgentWorkbenchThinkingProcess {
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
