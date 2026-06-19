import createClient from "openapi-fetch";
import type { paths } from "./schema";
import {
  normalizeAgentWorkbenchConversation,
  normalizeAgentWorkbenchConversationList,
  type AgentWorkbenchConversationListResponse,
  type AgentWorkbenchConversationResponse,
} from "./agentWorkbenchTypes";

export const api = createClient<paths>({ baseUrl: "" });

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
