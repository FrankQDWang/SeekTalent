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

  throw new ApiRequestError("Request failed.", result.response.status);
}

export function safeErrorMessage(error: unknown) {
  if (error instanceof ApiRequestError) {
    return `请求失败，状态码 ${String(error.status)}`;
  }

  return "请求失败，请稍后重试。";
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
