import { describe, expect, it } from "vitest";
import {
  shouldApplyWorkbenchSnapshot,
  workbenchStreamStartSeq,
} from "./agentWorkbench";
import {
  normalizeAgentWorkbenchConversation,
  type AgentWorkbenchConversationResponse,
} from "./agentWorkbenchTypes";
import { ApiRequestError, requireData } from "./client";

describe("Agent Workbench snapshot helpers", () => {
  it("does not replace a newer cached view with an older mutation snapshot", () => {
    const current = viewFixture({ snapshotSeq: 15 });

    expect(
      shouldApplyWorkbenchSnapshot(current, viewFixture({ snapshotSeq: 12 })),
    ).toBe(false);
    expect(
      shouldApplyWorkbenchSnapshot(current, viewFixture({ snapshotSeq: 15 })),
    ).toBe(true);
    expect(
      shouldApplyWorkbenchSnapshot(current, viewFixture({ snapshotSeq: 16 })),
    ).toBe(true);
    expect(
      shouldApplyWorkbenchSnapshot(undefined, viewFixture({ snapshotSeq: 12 })),
    ).toBe(true);
  });

  it("does not replace stream-applied cache state with an older effective stream boundary", () => {
    const current = viewFixture({ snapshotSeq: 4, latestStreamSeq: 5 });
    const olderRefetch = viewFixture({ snapshotSeq: 4, latestStreamSeq: 4 });

    expect(shouldApplyWorkbenchSnapshot(current, olderRefetch)).toBe(false);
  });

  it("uses snapshotSeq as the live stream start cursor", () => {
    expect(
      workbenchStreamStartSeq(
        viewFixture({ latestStreamSeq: 12, snapshotSeq: 9 }),
      ),
    ).toBe(9);
  });
});

describe("Agent Workbench response normalization", () => {
  it("defaults omitted query-group arrays while retaining public query metadata", () => {
    const input = {
      ...viewFixture(),
      thinkingProcess: {
        activeRoundNo: 1,
        rounds: [
          {
            roundNo: 1,
            status: "running",
            queryGroups: [
              {
                queryInstanceId: "query_1",
                termGroupKey: "term_group_1",
                queryRole: "exploit",
                laneType: "exploit",
                lifecycle: "executed",
                executionStatus: "completed",
                attempted: true,
                rawCandidateCount: 4,
                uniqueCandidateCount: 3,
                duplicateCandidateCount: 1,
              },
            ],
          },
        ],
      },
    } as Parameters<typeof normalizeAgentWorkbenchConversation>[0];

    const normalized = normalizeAgentWorkbenchConversation(input);

    expect(normalized.thinkingProcess.rounds[0]?.queryGroups).toEqual([
      {
        queryInstanceId: "query_1",
        termGroupKey: "term_group_1",
        queryRole: "exploit",
        laneType: "exploit",
        queryTerms: [],
        lifecycle: "executed",
        executionStatus: "completed",
        attempted: true,
        rawCandidateCount: 4,
        uniqueCandidateCount: 3,
        duplicateCandidateCount: 1,
        executions: [],
      },
    ]);
  });
});

describe("API error parsing", () => {
  it("preserves Workbench Problem Details reason and correlation ids", () => {
    expect.hasAssertions();

    try {
      requireData({
        error: {
          detail: "Requirement draft changed.",
          reasonCode: "requirement_draft_stale",
          correlationId: "corr_1",
        },
        response: new Response(null, { status: 409 }),
      });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiRequestError);
      expect(error).toMatchObject({
        message: "Requirement draft changed.",
        status: 409,
        reasonCode: "requirement_draft_stale",
        correlationId: "corr_1",
      });
    }
  });

  it("uses a safe fallback for malformed API errors", () => {
    expect(() =>
      requireData({
        error: { unexpected: "shape" },
        response: new Response(null, { status: 502 }),
      }),
    ).toThrow(new ApiRequestError("Request failed.", 502));
  });
});

function viewFixture(
  streamCursor: Partial<
    AgentWorkbenchConversationResponse["streamCursor"]
  > = {},
): AgentWorkbenchConversationResponse {
  return {
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
      ...streamCursor,
    },
    reasonCode: null,
  };
}
