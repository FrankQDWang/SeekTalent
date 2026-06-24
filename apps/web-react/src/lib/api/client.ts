import createClient from "openapi-fetch";
import type { paths } from "./schema";
import {
  normalizeAgentWorkbenchCandidateDetail,
  normalizeAgentWorkbenchConversation,
  normalizeAgentWorkbenchConversationList,
  type AgentWorkbenchCandidateDetailResponse,
  type AgentWorkbenchConversationListResponse,
  type AgentWorkbenchConversationResponse,
  type WorkbenchAgentMessageRequest,
  type WorkbenchConversationCreateRequest,
  type WorkbenchConversationFromJdRequest,
  type WorkbenchRequirementAmendRequest,
  type WorkbenchRequirementConfirmRequest,
  type WorkbenchRequirementOperationsRequest,
} from "./agentWorkbenchTypes";

type WorkbenchBffPath = Extract<keyof paths, `/api/agent/workbench/${string}`>;
export type WorkbenchBffPaths = Pick<paths, WorkbenchBffPath>;

export const api = createClient<WorkbenchBffPaths>({ baseUrl: "" });

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly reasonCode: string | null = null,
    readonly correlationId: string | null = null,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export function requireData<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.data !== undefined) {
    return result.data;
  }

  const problem = parseProblemDetails(result.error);
  throw new ApiRequestError(
    problem?.detail ?? "Request failed.",
    result.response.status,
    problem?.reasonCode ?? null,
    problem?.correlationId ?? null,
  );
}

export function safeErrorMessage(error: unknown) {
  if (error instanceof ApiRequestError) {
    return `请求失败，状态码 ${String(error.status)}`;
  }

  return "请求失败，请稍后重试。";
}

function parseProblemDetails(error: unknown): {
  detail?: string;
  reasonCode?: string;
  correlationId?: string;
} | null {
  if (typeof error !== "object" || error === null) {
    return null;
  }
  const candidate = error as Record<string, unknown>;
  const detail = parseProblemDetail(candidate.detail);
  if (
    typeof candidate.reasonCode !== "string" &&
    typeof candidate.correlationId !== "string"
  ) {
    return null;
  }
  const reasonCode =
    typeof candidate.reasonCode === "string"
      ? candidate.reasonCode
      : detail.reasonCode;
  return {
    ...(detail.message !== undefined ? { detail: detail.message } : {}),
    ...(reasonCode !== undefined ? { reasonCode } : {}),
    ...(typeof candidate.correlationId === "string"
      ? { correlationId: candidate.correlationId }
      : {}),
  };
}

function parseProblemDetail(detail: unknown): {
  message?: string;
  reasonCode?: string;
} {
  if (typeof detail === "string") {
    return { message: detail };
  }
  if (typeof detail !== "object" || detail === null) {
    return {};
  }
  const candidate = detail as Record<string, unknown>;
  return {
    ...(typeof candidate.message === "string"
      ? { message: candidate.message }
      : {}),
    ...(typeof candidate.reasonCode === "string"
      ? { reasonCode: candidate.reasonCode }
      : {}),
  };
}

export async function listAgentWorkbenchConversations(): Promise<AgentWorkbenchConversationListResponse> {
  return normalizeAgentWorkbenchConversationList(
    requireData(await api.GET("/api/agent/workbench/conversations")),
  );
}

export async function createAgentWorkbenchConversation(
  payload: WorkbenchConversationCreateRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST("/api/agent/workbench/conversations", {
        body: payload,
      }),
    ),
  );
}

export async function createAgentWorkbenchConversationFromJd(
  payload: WorkbenchConversationFromJdRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST("/api/agent/workbench/conversations/from-jd", {
        body: payload,
      }),
    ),
  );
}

export async function getAgentWorkbenchConversation(
  conversationId: string,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.GET("/api/agent/workbench/conversations/{conversation_id}", {
        params: {
          path: {
            conversation_id: conversationId,
          },
        },
      }),
    ),
  );
}

export async function getAgentWorkbenchCandidateDetail(
  conversationId: string,
  candidateId: string,
): Promise<AgentWorkbenchCandidateDetailResponse> {
  return normalizeAgentWorkbenchCandidateDetail(
    requireData(
      await api.GET(
        "/api/agent/workbench/conversations/{conversation_id}/candidates/{candidate_id}/detail",
        {
          params: {
            path: {
              conversation_id: conversationId,
              candidate_id: candidateId,
            },
          },
        },
      ),
    ),
  );
}

export async function submitAgentWorkbenchMessage(
  conversationId: string,
  payload: WorkbenchAgentMessageRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST(
        "/api/agent/workbench/conversations/{conversation_id}/messages",
        {
          params: {
            path: {
              conversation_id: conversationId,
            },
          },
          body: payload,
        },
      ),
    ),
  );
}

export async function confirmAgentWorkbenchRequirements(
  conversationId: string,
  payload: WorkbenchRequirementConfirmRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST(
        "/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        {
          params: {
            path: {
              conversation_id: conversationId,
            },
          },
          body: payload,
        },
      ),
    ),
  );
}

export async function updateAgentWorkbenchRequirementDraft(
  conversationId: string,
  payload: WorkbenchRequirementOperationsRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST(
        "/api/agent/workbench/conversations/{conversation_id}/requirements/operations",
        {
          params: {
            path: {
              conversation_id: conversationId,
            },
          },
          body: payload,
        },
      ),
    ),
  );
}

export async function amendAgentWorkbenchRequirementFromText(
  conversationId: string,
  payload: WorkbenchRequirementAmendRequest,
): Promise<AgentWorkbenchConversationResponse> {
  return normalizeAgentWorkbenchConversation(
    requireData(
      await api.POST(
        "/api/agent/workbench/conversations/{conversation_id}/requirements/amend-from-text",
        {
          params: {
            path: {
              conversation_id: conversationId,
            },
          },
          body: payload,
        },
      ),
    ),
  );
}
