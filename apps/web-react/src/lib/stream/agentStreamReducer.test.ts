import { describe, expect, it } from "vitest";
import {
  applyStreamEnvelope,
  initialAgentStreamState,
  type AgentStreamEnvelope,
  type AgentStreamKind,
  type AgentStreamPayload,
} from "./agentStreamReducer";

describe("agent stream reducer", () => {
  it("applies semantic envelopes in sequence and ignores duplicates", () => {
    expect.hasAssertions();

    const first = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 1,
        kind: "message.created",
        payload: {
          payloadType: "message.created",
          kind: "message",
          messageId: "msg_1",
          summary: "你好",
        },
      }),
    );
    const duplicate = applyStreamEnvelope(
      first,
      envelope({
        seq: 1,
        kind: "message.created",
        payload: {
          payloadType: "message.created",
          kind: "message",
          messageId: "msg_1",
          summary: "重复",
        },
      }),
    );

    expect(first.latestSeq).toBe(1);
    expect(first.messages).toEqual([{ messageId: "msg_1", text: "你好" }]);
    expect(duplicate).toBe(first);
  });

  it("ignores out-of-conversation events without changing state identity", () => {
    expect.hasAssertions();

    const initial = initialAgentStreamState("agent_conv_1");
    const next = applyStreamEnvelope(
      initial,
      envelope({
        conversationId: "agent_conv_2",
        seq: 1,
        kind: "activity.upserted",
        payload: {
          payloadType: "activity.upserted",
          kind: "activity",
          activityId: "activity_1",
        },
      }),
    );

    expect(next).toBe(initial);
  });

  it("marks a gap without applying future out-of-order events", () => {
    expect.hasAssertions();

    const state = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 3,
        kind: "activity.upserted",
        payload: {
          payloadType: "activity.upserted",
          kind: "activity",
          activityId: "activity_3",
        },
      }),
    );

    expect(state.gapDetected).toBe(true);
    expect(state.latestSeq).toBe(0);
    expect(state.activities).toEqual({});
    expect(state.graphEvents).toEqual([]);
  });

  it("stores explicit stream gaps as transcript recovery events", () => {
    expect.hasAssertions();

    const state = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 1,
        kind: "stream.gap",
        payload: {
          payloadType: "stream.gap",
          kind: "gap",
          missingFromSeq: 1,
          nextAvailableSeq: 4,
        },
      }),
    );

    expect(state.latestSeq).toBe(1);
    expect(state.gapDetected).toBe(true);
    expect(state.transcriptEvents).toEqual([
      envelope({
        seq: 1,
        kind: "stream.gap",
        payload: {
          payloadType: "stream.gap",
          kind: "gap",
          missingFromSeq: 1,
          nextAvailableSeq: 4,
        },
      }),
    ]);
  });

  it("stores semantic transcript lifecycle events without parsing display text", () => {
    expect.hasAssertions();

    const state = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 1,
        kind: "operation.started",
        payload: {
          payloadType: "operation.started",
          kind: "operation",
          itemId: "operation_1",
          delta: "Read service.py",
        },
      }),
    );

    expect(state.transcriptEvents).toEqual([
      envelope({
        seq: 1,
        kind: "operation.started",
        payload: {
          payloadType: "operation.started",
          kind: "operation",
          itemId: "operation_1",
          delta: "Read service.py",
        },
      }),
    ]);
  });

  it("appends live assistant deltas to the active message without committing a new cell", () => {
    expect.hasAssertions();

    const created = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 1,
        kind: "message.created",
        payload: {
          payloadType: "message.created",
          kind: "message",
          messageId: "msg_1",
          summary: "",
        },
      }),
    );
    const firstDelta = applyStreamEnvelope(
      created,
      envelope({
        seq: 2,
        kind: "message.delta",
        payload: {
          payloadType: "message.delta",
          kind: "message",
          messageId: "msg_1",
          delta: "正在",
        },
      }),
    );
    const secondDelta = applyStreamEnvelope(
      firstDelta,
      envelope({
        seq: 3,
        kind: "message.delta",
        payload: {
          payloadType: "message.delta",
          kind: "message",
          messageId: "msg_1",
          delta: "分析",
        },
      }),
    );

    expect(secondDelta.messages).toEqual([
      { messageId: "msg_1", text: "正在分析" },
    ]);
    expect(secondDelta.transcriptEvents).toEqual([
      envelope({
        seq: 2,
        kind: "message.delta",
        payload: {
          payloadType: "message.delta",
          kind: "message",
          messageId: "msg_1",
          delta: "正在",
        },
      }),
      envelope({
        seq: 3,
        kind: "message.delta",
        payload: {
          payloadType: "message.delta",
          kind: "message",
          messageId: "msg_1",
          delta: "分析",
        },
      }),
    ]);
  });

  it("batches graph and candidate paths separately for later UI reducers", () => {
    expect.hasAssertions();

    const withGraph = applyStreamEnvelope(
      initialAgentStreamState("agent_conv_1"),
      envelope({
        seq: 1,
        kind: "strategyGraph.changed",
        payload: {
          payloadType: "strategyGraph.changed",
          kind: "strategy_graph",
          itemId: "strategy_graph",
        },
      }),
    );
    const withCandidate = applyStreamEnvelope(
      withGraph,
      envelope({
        seq: 2,
        kind: "candidate.upserted",
        payload: {
          payloadType: "candidate.upserted",
          kind: "candidate",
          itemId: "candidate_1",
          summary: "Safe summary",
        },
      }),
    );

    expect(withCandidate.graphEvents).toEqual([
      envelope({
        seq: 1,
        kind: "strategyGraph.changed",
        payload: {
          payloadType: "strategyGraph.changed",
          kind: "strategy_graph",
          itemId: "strategy_graph",
        },
      }),
    ]);
    expect(withCandidate.candidateEvents).toEqual([
      envelope({
        seq: 2,
        kind: "candidate.upserted",
        payload: {
          payloadType: "candidate.upserted",
          kind: "candidate",
          itemId: "candidate_1",
          summary: "Safe summary",
        },
      }),
    ]);
    expect(withCandidate.transcriptEvents).toEqual([]);
  });
});

function envelope({
  conversationId = "agent_conv_1",
  seq,
  kind,
  payload,
}: {
  conversationId?: string;
  seq: number;
  kind: AgentStreamKind;
  payload: AgentStreamPayload;
}): AgentStreamEnvelope {
  return {
    schemaVersion: "agent.workbench.stream.v1",
    conversationId,
    seq,
    kind,
    payload,
    createdAt: "2026-06-12T12:00:00+00:00",
  };
}
