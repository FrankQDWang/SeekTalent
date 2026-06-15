import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAgentWorkbenchLiveConversation } from "./agentWorkbench";
import type { AgentWorkbenchConversationResponse } from "./agentWorkbenchTypes";
import type * as AgentStreamReducerModule from "../stream/agentStreamReducer";
import { createWorkbenchQueryClient } from "../query/client";
import { queryKeys } from "../query/keys";
import { connectAgentStream } from "../stream/agentStream";
import { applyStreamEnvelope } from "../stream/agentStreamReducer";

vi.mock("../stream/agentStream", () => ({
  connectAgentStream: vi.fn(() => vi.fn()),
}));

vi.mock("../stream/agentStreamReducer", async (importOriginal) => {
  const actual = await importOriginal<typeof AgentStreamReducerModule>();
  return {
    ...actual,
    applyStreamEnvelope: vi.fn(actual.applyStreamEnvelope),
  };
});

describe("live Agent Workbench conversation hook", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("connects the EventSource stream once a snapshot is available", async () => {
    expect.hasAssertions();
    const client = createWorkbenchQueryClient();
    client.setQueryData(
      queryKeys.agentConversation("agent_conv_1"),
      conversationSnapshot,
    );

    render(
      <QueryClientProvider client={client}>
        <ConversationProbe conversationId="agent_conv_1" />
      </QueryClientProvider>,
    );

    expect(screen.getByText("资深 Python 后端")).toBeVisible();
    await waitFor(() => {
      expect(connectAgentStream).toHaveBeenCalledWith(
        expect.objectContaining({
          conversationId: "agent_conv_1",
          afterSeq: 0,
        }),
      );
    });
  });

  it("merges transcript stream events without invalidating the active snapshot cache", async () => {
    expect.hasAssertions();
    const client = createWorkbenchQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    client.setQueryData(
      queryKeys.agentConversation("agent_conv_1"),
      conversationSnapshot,
    );

    render(
      <QueryClientProvider client={client}>
        <ConversationProbe conversationId="agent_conv_1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(connectAgentStream).toHaveBeenCalledOnce());
    const options = vi.mocked(connectAgentStream).mock.calls[0]?.[0];
    expect(options).toBeDefined();

    options?.onBatch([
      {
        schemaVersion: "agent.workbench.stream.v1",
        conversationId: "agent_conv_1",
        seq: 1,
        kind: "activity.upserted",
        payload: {
          payloadType: "activity.upserted",
          kind: "activity",
          activityId: "activity_1",
          summary: "第一轮检索完成。",
        },
        createdAt: "2026-06-12T12:00:00+00:00",
      },
    ]);

    await waitFor(() => {
      const updated = client.getQueryData<AgentWorkbenchConversationResponse>(
        queryKeys.agentConversation("agent_conv_1"),
      );
      expect(updated?.streamCursor.latestStreamSeq).toBe(1);
      expect(updated?.transcriptGroups.at(-1)?.groupId).toBe(
        "agent_conv_1:stream-live",
      );
      expect(updated?.transcriptGroups.at(-1)?.events.at(-1)?.summary).toBe(
        "第一轮检索完成。",
      );
      expect(invalidate).not.toHaveBeenCalled();
    });
  });

  it("invalidates the snapshot only for stream events that depend on durable view data", async () => {
    expect.hasAssertions();
    const client = createWorkbenchQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    client.setQueryData(
      queryKeys.agentConversation("agent_conv_1"),
      conversationSnapshot,
    );

    render(
      <QueryClientProvider client={client}>
        <ConversationProbe conversationId="agent_conv_1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(connectAgentStream).toHaveBeenCalledOnce());
    const options = vi.mocked(connectAgentStream).mock.calls[0]?.[0];
    expect(options).toBeDefined();

    options?.onBatch([
      {
        schemaVersion: "agent.workbench.stream.v1",
        conversationId: "agent_conv_1",
        seq: 1,
        kind: "candidate.upserted",
        payload: {
          payloadType: "candidate.upserted",
          kind: "candidate",
          itemId: "candidate_1",
          summary: "候选人摘要已更新。",
        },
        createdAt: "2026-06-12T12:00:00+00:00",
      },
    ]);

    await waitFor(() => {
      const updated = client.getQueryData<AgentWorkbenchConversationResponse>(
        queryKeys.agentConversation("agent_conv_1"),
      );
      expect(updated?.streamCursor.latestStreamSeq).toBe(1);
      expect(updated?.conversation.status).toBe("disconnected");
      expect(updated?.reasonCode).toBe("stream_recovery");
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: queryKeys.agentConversation("agent_conv_1"),
      });
    });
  });

  it("surfaces visible stream disconnects in the active snapshot cache", async () => {
    expect.hasAssertions();
    const client = createWorkbenchQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    client.setQueryData(
      queryKeys.agentConversation("agent_conv_1"),
      conversationSnapshot,
    );

    render(
      <QueryClientProvider client={client}>
        <ConversationProbe conversationId="agent_conv_1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(connectAgentStream).toHaveBeenCalledOnce());
    const options = vi.mocked(connectAgentStream).mock.calls[0]?.[0];
    expect(options).toBeDefined();
    expect(options?.onDisconnect).toEqual(expect.any(Function));

    options?.onDisconnect?.();

    await waitFor(() => {
      const updated = client.getQueryData<AgentWorkbenchConversationResponse>(
        queryKeys.agentConversation("agent_conv_1"),
      );
      expect(updated?.conversation.status).toBe("disconnected");
      expect(updated?.reasonCode).toBe("stream_disconnected");
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: queryKeys.agentConversation("agent_conv_1"),
      });
    });
  });

  it("starts the local reducer from the durable snapshot stream cursor", async () => {
    expect.hasAssertions();
    const client = createWorkbenchQueryClient();
    const snapshot = {
      ...conversationSnapshot,
      streamCursor: {
        ...conversationSnapshot.streamCursor,
        latestStreamSeq: 4,
      },
    };
    client.setQueryData(queryKeys.agentConversation("agent_conv_1"), snapshot);

    render(
      <QueryClientProvider client={client}>
        <ConversationProbe conversationId="agent_conv_1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(connectAgentStream).toHaveBeenCalledOnce());
    const options = vi.mocked(connectAgentStream).mock.calls[0]?.[0];
    const event = {
      schemaVersion: "agent.workbench.stream.v1" as const,
      conversationId: "agent_conv_1",
      seq: 5,
      kind: "candidate.upserted" as const,
      payload: {
        payloadType: "candidate.upserted" as const,
        kind: "candidate" as const,
        itemId: "candidate_1",
      },
      createdAt: "2026-06-12T12:00:01+00:00",
    };

    options?.onBatch([event]);

    expect(applyStreamEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({ latestSeq: 4, gapDetected: false }),
      event,
    );
  });
});

function ConversationProbe({ conversationId }: { conversationId: string }) {
  const query = useAgentWorkbenchLiveConversation(conversationId);
  return <div>{query.data?.conversation.title}</div>;
}

const conversationSnapshot: AgentWorkbenchConversationResponse = {
  schemaVersion: "agent.workbench.view.v1",
  conversation: {
    conversationId: "agent_conv_1",
    title: "资深 Python 后端",
    status: "running",
    isArchived: false,
    runtimeRunId: "runtime_1",
    workbenchSessionId: "session_1",
    updatedAt: "2026-06-12T12:00:00+00:00",
  },
  messages: [],
  activities: [],
  transcriptGroups: [],
  requirementDraft: null,
  runtime: null,
  strategyGraph: { nodes: [], edges: [] },
  thinkingProcess: { activeRoundNo: null, rounds: [] },
  sourceConnections: [],
  candidates: [],
  detailApprovals: [],
  reviewArtifacts: [],
  finalSummary: null,
  pendingActions: {
    primary: null,
    allowed: [],
    pendingCommandCount: 0,
    pendingRequirementReviewCount: 0,
    pendingMemoryReviewCount: 0,
  },
  streamCursor: {
    latestMessageSeq: 0,
    latestActivitySeq: 0,
    latestRuntimeEventSeq: 0,
    latestStreamSeq: 0,
  },
  reasonCode: null,
};
