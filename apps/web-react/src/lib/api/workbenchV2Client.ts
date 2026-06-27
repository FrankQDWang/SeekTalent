import {
  normalizeAgentWorkbenchCandidateDetail,
  type AgentWorkbenchCandidateDetailResponse,
} from "./agentWorkbenchTypes";
import {
  normalizeWorkbenchV2Conversation,
  normalizeWorkbenchV2ConversationEvents,
  normalizeWorkbenchV2ConversationList,
  type WorkbenchV2ConversationEventsView,
  type WorkbenchV2ConversationListView,
  type WorkbenchV2ConversationView,
  type WorkbenchV2MessageRequest,
  type WorkbenchV2RequirementActionRequest,
} from "./workbenchV2Types";

const WORKBENCH_V2_CONVERSATIONS_PATH = "/api/agent/workbench/v2/conversations";

export class WorkbenchV2RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly reasonCode: string | null = null,
    readonly correlationId: string | null = null,
  ) {
    super(message);
    this.name = "WorkbenchV2RequestError";
  }
}

export async function listWorkbenchV2Conversations(): Promise<WorkbenchV2ConversationListView> {
  return normalizeWorkbenchV2ConversationList(
    await requestJson<WorkbenchV2ConversationListView>(
      WORKBENCH_V2_CONVERSATIONS_PATH,
      { method: "GET" },
    ),
  );
}

export async function createWorkbenchV2Conversation(
  payload: WorkbenchV2MessageRequest,
): Promise<WorkbenchV2ConversationView> {
  return normalizeWorkbenchV2Conversation(
    await requestJson<WorkbenchV2ConversationView>(
      WORKBENCH_V2_CONVERSATIONS_PATH,
      postJsonInit(payload),
    ),
  );
}

export async function getWorkbenchV2Conversation(
  conversationId: string,
): Promise<WorkbenchV2ConversationView> {
  return normalizeWorkbenchV2Conversation(
    await requestJson<WorkbenchV2ConversationView>(
      `${WORKBENCH_V2_CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}`,
      { method: "GET" },
    ),
  );
}

export async function getWorkbenchV2CandidateDetail(
  conversationId: string,
  candidateId: string,
): Promise<AgentWorkbenchCandidateDetailResponse> {
  return normalizeAgentWorkbenchCandidateDetail(
    await requestJson<AgentWorkbenchCandidateDetailResponse>(
      `${WORKBENCH_V2_CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/candidates/${encodeURIComponent(candidateId)}/detail`,
      { method: "GET" },
    ),
  );
}

export async function listWorkbenchV2ConversationEvents({
  afterStep = 0,
  conversationId,
  limit = 100,
}: {
  afterStep?: number;
  conversationId: string;
  limit?: number;
}): Promise<WorkbenchV2ConversationEventsView> {
  const params = new URLSearchParams({
    afterStep: String(afterStep),
    limit: String(limit),
  });
  return normalizeWorkbenchV2ConversationEvents(
    await requestJson<WorkbenchV2ConversationEventsView>(
      `${WORKBENCH_V2_CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/events?${params.toString()}`,
      { method: "GET" },
    ),
  );
}

export async function submitWorkbenchV2Message(
  conversationId: string,
  payload: WorkbenchV2MessageRequest,
): Promise<WorkbenchV2ConversationView> {
  return normalizeWorkbenchV2Conversation(
    await requestJson<WorkbenchV2ConversationView>(
      `${WORKBENCH_V2_CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/messages`,
      postJsonInit(payload),
    ),
  );
}

export async function applyWorkbenchV2RequirementAction(
  conversationId: string,
  payload: WorkbenchV2RequirementActionRequest,
): Promise<WorkbenchV2ConversationView> {
  return normalizeWorkbenchV2Conversation(
    await requestJson<WorkbenchV2ConversationView>(
      `${WORKBENCH_V2_CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/requirement-actions`,
      postJsonInit(payload),
    ),
  );
}

function postJsonInit(
  payload: WorkbenchV2MessageRequest | WorkbenchV2RequirementActionRequest,
): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new WorkbenchV2RequestError(
      "Network request failed.",
      0,
      "workbench_v2_network_error",
    );
  }

  const body = await readJsonBody(response);

  if (!response.ok) {
    const problem = parseProblemDetails(body);
    throw new WorkbenchV2RequestError(
      problem.message,
      response.status,
      problem.reasonCode,
      problem.correlationId,
    );
  }

  return body as T;
}

async function readJsonBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length === 0) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function parseProblemDetails(body: unknown): {
  message: string;
  reasonCode: string | null;
  correlationId: string | null;
} {
  const problem = asRecord(body);
  const detail = asRecord(problem?.detail);
  const detailMessage =
    typeof problem?.detail === "string"
      ? problem.detail
      : stringField(detail, "message");
  const reasonCode =
    stringField(problem, "reasonCode") ?? stringField(detail, "reasonCode");
  return {
    message:
      detailMessage ??
      stringField(problem, "message") ??
      stringField(problem, "title") ??
      reasonCode ??
      "Request failed.",
    reasonCode: reasonCode ?? null,
    correlationId: stringField(problem, "correlationId") ?? null,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function stringField(
  value: Record<string, unknown> | null,
  field: string,
): string | undefined {
  const fieldValue = value?.[field];
  return typeof fieldValue === "string" ? fieldValue : undefined;
}
