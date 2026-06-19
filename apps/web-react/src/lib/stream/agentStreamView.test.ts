import { describe, expect, it } from "vitest";
import { mergeStreamEnvelopesIntoConversation } from "./agentStreamView";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchStreamEnvelope,
} from "../api/agentWorkbenchTypes";

describe("agent stream view merge", () => {
  it("keeps normal graph and candidate update events connected without fabricating objects", () => {
    expect.hasAssertions();

    const updated = mergeStreamEnvelopesIntoConversation(conversationSnapshot, [
      envelope({
        seq: 1,
        kind: "strategyGraph.changed",
        payload: {
          payloadType: "strategyGraph.changed",
          kind: "strategy_graph",
          itemId: "strategy_graph",
          summary: "图谱已更新",
        },
      }),
      envelope({
        seq: 2,
        kind: "candidate.upserted",
        payload: {
          payloadType: "candidate.upserted",
          kind: "candidate",
          itemId: "candidate_1",
          summary: "候选人摘要已更新",
        },
      }),
    ]);

    expect(updated.streamCursor.latestStreamSeq).toBe(2);
    expect(updated.conversation.status).toBe("running");
    expect(updated.reasonCode).toBeNull();
    expect(updated.strategyGraph).toBe(conversationSnapshot.strategyGraph);
    expect(updated.candidates).toBe(conversationSnapshot.candidates);
    expect(
      updated.transcriptGroups.at(-1)?.events.map((event) => event.kind),
    ).toEqual(["strategyGraph.changed", "candidate.upserted"]);
  });

  it("marks explicit stream gaps as snapshot recovery", () => {
    expect.hasAssertions();

    const updated = mergeStreamEnvelopesIntoConversation(conversationSnapshot, [
      envelope({
        seq: 1,
        kind: "stream.gap",
        payload: {
          payloadType: "stream.gap",
          kind: "gap",
          missingFromSeq: 1,
          nextAvailableSeq: 4,
          summary: "事件流存在缺口，正在恢复快照。",
        },
      }),
    ]);

    expect(updated.streamCursor.latestStreamSeq).toBe(1);
    expect(updated.conversation.status).toBe("disconnected");
    expect(updated.reasonCode).toBe("stream_recovery");
    expect(updated.transcriptGroups.at(-1)?.events.at(-1)?.kind).toBe(
      "stream.gap",
    );
  });
});

function envelope({
  seq,
  kind,
  payload,
}: {
  seq: number;
  kind: AgentWorkbenchStreamEnvelope["kind"];
  payload: AgentWorkbenchStreamEnvelope["payload"];
}): AgentWorkbenchStreamEnvelope {
  return {
    schemaVersion: "agent.workbench.stream.v1",
    conversationId: "agent_conv_1",
    seq,
    kind,
    payload,
    createdAt: "2026-06-12T12:00:00+00:00",
  };
}

const conversationSnapshot: AgentWorkbenchConversationResponse = {
  schemaVersion: "agent.workbench.view.v2",
  conversation: {
    conversationId: "agent_conv_1",
    title: "资深 Python 后端",
    status: "running",
    isArchived: false,
    runtimeRunId: "runtime_1",
    workbenchSessionId: "session_1",
    workflowStartState: "running",
    workflowStartReasonCode: null,
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
    snapshotSeq: 0,
    viewRevision: 0,
  },
  reasonCode: null,
};
