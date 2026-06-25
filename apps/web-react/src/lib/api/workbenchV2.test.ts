import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createWorkbenchV2Conversation,
  getWorkbenchV2Conversation,
  listWorkbenchV2Conversations,
  submitWorkbenchV2Message,
  WorkbenchV2RequestError,
} from "./workbenchV2Client";
import {
  normalizeWorkbenchV2Conversation,
  type WorkbenchV2ConversationView,
} from "./workbenchV2Types";

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

  it("throws a stable request error with Problem Details status and reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            { detail: { reasonCode: "workbench_v2_conversation_not_found" } },
            404,
          ),
        ),
      ),
    );

    await expect(getWorkbenchV2Conversation("missing")).rejects.toMatchObject({
      name: "WorkbenchV2RequestError",
      status: 404,
      reasonCode: "workbench_v2_conversation_not_found",
    });
    await expect(getWorkbenchV2Conversation("missing")).rejects.toBeInstanceOf(
      WorkbenchV2RequestError,
    );
  });
});

function stubJsonFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(body, status)));
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
    conversation: {
      conversationId: "agentv2_1",
      title: "先聊一下候选人搜索",
      runtimeState: "idle",
      runtimeRunId: null,
      createdAt: "2026-06-25T01:02:03.000004+00:00",
      updatedAt: "2026-06-25T01:02:03.000004+00:00",
    },
    transcriptEvents: [transcriptEvent()],
    requirementForm: null,
    runtime: null,
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
