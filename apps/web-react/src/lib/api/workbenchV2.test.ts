import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyWorkbenchV2RequirementAction,
  createWorkbenchV2Conversation,
  getWorkbenchV2Conversation,
  listWorkbenchV2Conversations,
  submitWorkbenchV2Message,
  WorkbenchV2RequestError,
} from "./workbenchV2Client";
import {
  shouldApplyWorkbenchV2Snapshot,
  useApplyWorkbenchV2RequirementAction,
} from "./workbenchV2";
import {
  normalizeWorkbenchV2Conversation,
  type WorkbenchV2ConversationView,
} from "./workbenchV2Types";
import { createWorkbenchQueryClient } from "../query/client";
import { queryKeys } from "../query/keys";

describe("Workbench v2 normalization", () => {
  it("sorts transcriptEvents by step without mutating the input", () => {
    const input = conversationView({
      transcriptEvents: [
        transcriptEvent({ eventId: "event_3", step: 3 }),
        transcriptEvent({ eventId: "event_1", step: 1 }),
        transcriptEvent({ eventId: "event_2", step: 2 }),
      ],
    });

    const normalized = normalizeWorkbenchV2Conversation(input);

    expect(normalized).not.toBe(input);
    expect(normalized.conversation).toBe(input.conversation);
    expect(normalized.transcriptEvents).not.toBe(input.transcriptEvents);
    expect(normalized.transcriptEvents.map((event) => event.eventId)).toEqual([
      "event_1",
      "event_2",
      "event_3",
    ]);
    expect(input.transcriptEvents.map((event) => event.eventId)).toEqual([
      "event_3",
      "event_1",
      "event_2",
    ]);
  });
});

describe("Workbench v2 snapshot freshness", () => {
  it("accepts a snapshot with a newer updatedAt", () => {
    const current = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:03.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ step: 3 })],
    });
    const next = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:04.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ step: 1 })],
    });

    expect(shouldApplyWorkbenchV2Snapshot(current, next)).toBe(true);
  });

  it("rejects a snapshot with an older updatedAt", () => {
    const current = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:04.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ step: 1 })],
    });
    const next = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:03.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ step: 8 })],
    });

    expect(shouldApplyWorkbenchV2Snapshot(current, next)).toBe(false);
  });

  it("rejects a same-updatedAt snapshot with a lower transcript step", () => {
    const current = conversationView({
      transcriptEvents: [transcriptEvent({ step: 4 })],
    });
    const next = conversationView({
      transcriptEvents: [transcriptEvent({ step: 3 })],
    });

    expect(shouldApplyWorkbenchV2Snapshot(current, next)).toBe(false);
  });

  it("accepts a same-updatedAt snapshot with equal or higher transcript step", () => {
    const current = conversationView({
      transcriptEvents: [transcriptEvent({ step: 4 })],
    });
    const equal = conversationView({
      transcriptEvents: [transcriptEvent({ step: 4 })],
    });
    const higher = conversationView({
      transcriptEvents: [transcriptEvent({ step: 5 })],
    });

    expect(shouldApplyWorkbenchV2Snapshot(current, equal)).toBe(true);
    expect(shouldApplyWorkbenchV2Snapshot(current, higher)).toBe(true);
  });
});

describe("Workbench v2 client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists conversations with the v2 endpoint and normalizes the response", async () => {
    const responseBody = {
      schemaVersion: "agent.workbench.v2.list" as const,
      conversations: [
        {
          conversationId: "agentv2_1",
          title: "Existing conversation",
          status: "idle" as const,
          updatedAt: "2026-06-25T01:02:03.000004+00:00",
        },
      ],
    };
    const fetchMock = stubJsonFetch(responseBody);

    const result = await listWorkbenchV2Conversations();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations",
      { method: "GET" },
    );
    expect(result).toEqual(responseBody);
    expect(result).not.toBe(responseBody);
    expect(result.conversations).not.toBe(responseBody.conversations);
  });

  it("creates a conversation with a JSON body and normalizes transcriptEvents", async () => {
    const responseBody = conversationView({
      transcriptEvents: [
        transcriptEvent({ eventId: "event_2", step: 2 }),
        transcriptEvent({ eventId: "event_1", step: 1 }),
      ],
    });
    const fetchMock = stubJsonFetch(responseBody, 201);

    const result = await createWorkbenchV2Conversation({
      message: "先聊一下候选人搜索",
      idempotencyKey: "create-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: "先聊一下候选人搜索",
          idempotencyKey: "create-1",
        }),
      },
    );
    expect(result.transcriptEvents.map((event) => event.eventId)).toEqual([
      "event_1",
      "event_2",
    ]);
  });

  it("gets a conversation with an encoded id and normalizes transcriptEvents", async () => {
    const responseBody = conversationView({
      transcriptEvents: [
        transcriptEvent({ eventId: "event_7", step: 7 }),
        transcriptEvent({ eventId: "event_4", step: 4 }),
      ],
    });
    const fetchMock = stubJsonFetch(responseBody);

    const result = await getWorkbenchV2Conversation("agent conv/1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations/agent%20conv%2F1",
      { method: "GET" },
    );
    expect(result.transcriptEvents.map((event) => event.step)).toEqual([4, 7]);
  });

  it("submits a message with a JSON body and normalizes transcriptEvents", async () => {
    const responseBody = conversationView({
      transcriptEvents: [
        transcriptEvent({ eventId: "event_5", step: 5 }),
        transcriptEvent({ eventId: "event_4", step: 4 }),
      ],
    });
    const fetchMock = stubJsonFetch(responseBody);

    const result = await submitWorkbenchV2Message("agent conv/1", {
      message: "继续",
      idempotencyKey: "submit-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations/agent%20conv%2F1/messages",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: "继续",
          idempotencyKey: "submit-1",
        }),
      },
    );
    expect(result.transcriptEvents.map((event) => event.step)).toEqual([4, 5]);
  });

  it("applies a requirement action with a JSON body and normalizes transcriptEvents", async () => {
    const responseBody = conversationView({
      transcriptEvents: [
        transcriptEvent({ eventId: "event_8", step: 8 }),
        transcriptEvent({ eventId: "event_6", step: 6 }),
      ],
    });
    const fetchMock = stubJsonFetch(responseBody);

    const result = await applyWorkbenchV2RequirementAction("agent conv/1", {
      action: "set_selected",
      itemId: "item_1",
      selected: false,
      idempotencyKey: "action-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations/agent%20conv%2F1/requirement-actions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "set_selected",
          itemId: "item_1",
          selected: false,
          idempotencyKey: "action-1",
        }),
      },
    );
    expect(result.transcriptEvents.map((event) => event.step)).toEqual([6, 8]);
  });

  it("throws a stable request error with Problem Details status and reason", async () => {
    const fetchMock = stubJsonFetch(
      { detail: { reasonCode: "workbench_v2_conversation_not_found" } },
      404,
    );

    const error = await captureError(() =>
      getWorkbenchV2Conversation("missing"),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(WorkbenchV2RequestError);
    expect(error).toMatchObject({
      name: "WorkbenchV2RequestError",
      status: 404,
      reasonCode: "workbench_v2_conversation_not_found",
    });
  });

  it("preserves top-level Problem Details reason and correlation ids", async () => {
    const fetchMock = stubJsonFetch(
      {
        detail: "Idempotency conflict.",
        reasonCode: "workbench_v2_idempotency_conflict",
        correlationId: "corr_1",
      },
      409,
    );

    const error = await captureError(() =>
      createWorkbenchV2Conversation({
        message: "different message",
        idempotencyKey: "same-key",
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(WorkbenchV2RequestError);
    expect(error).toMatchObject({
      message: "Idempotency conflict.",
      status: 409,
      reasonCode: "workbench_v2_idempotency_conflict",
      correlationId: "corr_1",
    });
  });

  it("uses a stable fallback for non-ok responses with an empty body", async () => {
    const fetchMock = stubFetchResponse(new Response(null, { status: 503 }));

    const error = await captureError(() => listWorkbenchV2Conversations());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(WorkbenchV2RequestError);
    expect(error).toMatchObject({
      message: "Request failed.",
      status: 503,
      reasonCode: null,
      correlationId: null,
    });
  });

  it("uses a stable fallback for non-ok responses with a non-JSON body", async () => {
    const fetchMock = stubFetchResponse(
      new Response("Proxy gateway failure", { status: 502 }),
    );

    const error = await captureError(() => listWorkbenchV2Conversations());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(WorkbenchV2RequestError);
    expect(error).toMatchObject({
      message: "Request failed.",
      status: 502,
      reasonCode: null,
      correlationId: null,
    });
  });

  it("wraps network rejections in a stable request error", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error("Failed to fetch")));
    vi.stubGlobal("fetch", fetchMock);

    const error = await captureError(() =>
      getWorkbenchV2Conversation("agentv2_1"),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(WorkbenchV2RequestError);
    expect(error).toMatchObject({
      message: "Network request failed.",
      status: 0,
      reasonCode: "workbench_v2_network_error",
      correlationId: null,
    });
  });
});

describe("Workbench v2 requirement action hook", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("updates the conversation cache through the snapshot guard and invalidates the list", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    const queryKey = queryKeys.workbenchV2Conversation("agentv2_1");
    const current = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:03.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ eventId: "event_1", step: 1 })],
    });
    const next = conversationView({
      conversation: conversationSummary({
        updatedAt: "2026-06-25T01:02:04.000004+00:00",
      }),
      transcriptEvents: [transcriptEvent({ eventId: "event_2", step: 2 })],
    });
    queryClient.setQueryData(queryKey, current);
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    const fetchMock = stubJsonFetch(next);

    const { result } = renderHook(
      () => useApplyWorkbenchV2RequirementAction("agentv2_1"),
      { wrapper: wrapperFor(queryClient) },
    );

    await result.current.mutateAsync({
      action: "confirm",
      idempotencyKey: "action-2",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/workbench/v2/conversations/agentv2_1/requirement-actions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "confirm",
          idempotencyKey: "action-2",
        }),
      },
    );
    expect(queryClient.getQueryData(queryKey)).toEqual(next);
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: queryKeys.workbenchV2Conversations,
    });
  });
});

function stubJsonFetch(body: unknown, status = 200) {
  return stubFetchResponse(jsonResponse(body, status));
}

function stubFetchResponse(response: Response) {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function conversationView(
  overrides: Partial<WorkbenchV2ConversationView> = {},
): WorkbenchV2ConversationView {
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: conversationSummary(),
    transcriptEvents: [transcriptEvent()],
    requirementForm: null,
    runtime: null,
    ...overrides,
  };
}

function conversationSummary(
  overrides: Partial<WorkbenchV2ConversationView["conversation"]> = {},
): WorkbenchV2ConversationView["conversation"] {
  return {
    conversationId: "agentv2_1",
    title: "先聊一下候选人搜索",
    runtimeState: "idle",
    runtimeRunId: null,
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    updatedAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function transcriptEvent(
  overrides: Partial<
    WorkbenchV2ConversationView["transcriptEvents"][number]
  > = {},
): WorkbenchV2ConversationView["transcriptEvents"][number] {
  return {
    eventId: "event_1",
    step: 1,
    type: "user_message",
    role: "user",
    status: "completed",
    payload: { text: "先聊一下候选人搜索" },
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

async function captureError(
  operation: () => Promise<unknown>,
): Promise<unknown> {
  try {
    await operation();
  } catch (error) {
    return error;
  }
  throw new Error("Expected operation to reject.");
}

function wrapperFor(
  queryClient: ReturnType<typeof createWorkbenchQueryClient>,
) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}
