import { afterEach, describe, expect, it, vi } from "vitest";
import { connectAgentStream } from "./agentStream";
import type { AgentStreamEnvelope } from "./agentStreamReducer";

describe("agent stream client", () => {
  afterEach(() => {
    setDocumentVisibility("visible");
    vi.unstubAllGlobals();
  });

  it("connects with after_seq and batches agent_workbench_event envelopes", () => {
    expect.hasAssertions();

    const frame = installAnimationFrame();
    const eventSources = installEventSource();
    const onBatch = vi.fn();
    const onGap = vi.fn();

    const cleanup = connectAgentStream({
      conversationId: "agent conv/1",
      afterSeq: 12,
      onBatch,
      onGap,
    });
    const source = onlyEventSource(eventSources);

    expect(source.url).toBe(
      "/api/agent/workbench/conversations/agent%20conv%2F1/events/stream?after_seq=12",
    );

    source.dispatch(
      "agent_workbench_event",
      JSON.stringify(envelope({ seq: 13, kind: "message.completed" })),
    );
    source.dispatch("agent_workbench_event", '{"schemaVersion":"unknown"}');
    source.dispatch(
      "message",
      JSON.stringify(envelope({ seq: 14, kind: "message.completed" })),
    );

    expect(onBatch).not.toHaveBeenCalled();
    frame.flush();

    expect(onBatch).toHaveBeenCalledWith([
      envelope({ seq: 13, kind: "message.completed" }),
    ]);
    expect(onGap).not.toHaveBeenCalled();

    cleanup();
    expect(source.closed).toBe(true);
  });

  it("rejects stream envelopes whose payload discriminator does not match the envelope kind", () => {
    expect.hasAssertions();

    const frame = installAnimationFrame();
    const eventSources = installEventSource();
    const onBatch = vi.fn();

    connectAgentStream({
      conversationId: "agent_conv_1",
      afterSeq: 0,
      onBatch,
      onGap: vi.fn(),
    });
    const source = onlyEventSource(eventSources);

    source.dispatch(
      "agent_workbench_event",
      JSON.stringify(
        envelope({
          seq: 1,
          kind: "message.delta",
          payload: {
            payloadType: "message.completed",
            kind: "message",
            messageId: "msg_1",
            delta: "忽略",
          },
        }),
      ),
    );
    frame.flush();

    expect(onBatch).not.toHaveBeenCalled();
  });

  it("calls onGap for stream.gap and cancels queued batches on cleanup", () => {
    expect.hasAssertions();

    const frame = installAnimationFrame();
    const eventSources = installEventSource();
    const onBatch = vi.fn();
    const onGap = vi.fn();

    const cleanup = connectAgentStream({
      conversationId: "agent_conv_1",
      afterSeq: 0,
      onBatch,
      onGap,
    });
    const source = onlyEventSource(eventSources);

    source.dispatch(
      "agent_workbench_event",
      JSON.stringify(
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
      ),
    );

    expect(onGap).toHaveBeenCalledOnce();
    cleanup();
    frame.flush();

    expect(frame.cancelAnimationFrame).toHaveBeenCalledOnce();
    expect(onBatch).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
  });

  it("surfaces EventSource errors while the document is visible", () => {
    expect.hasAssertions();

    installAnimationFrame();
    const eventSources = installEventSource();
    const onDisconnect = vi.fn();

    connectAgentStream({
      conversationId: "agent_conv_1",
      afterSeq: 0,
      onBatch: vi.fn(),
      onGap: vi.fn(),
      onDisconnect,
    });
    const source = onlyEventSource(eventSources);

    source.onerror?.();

    expect(source.closed).toBe(false);
    expect(onDisconnect).toHaveBeenCalledOnce();
  });

  it("closes hidden EventSource errors and reopens when visibility returns", () => {
    expect.hasAssertions();

    installAnimationFrame();
    setDocumentVisibility("hidden");
    const eventSources = installEventSource();
    const onDisconnect = vi.fn();
    const onReconnect = vi.fn();

    const cleanup = connectAgentStream({
      conversationId: "agent_conv_1",
      afterSeq: 7,
      onBatch: vi.fn(),
      onGap: vi.fn(),
      onDisconnect,
      onReconnect,
    });
    const hiddenSource = onlyEventSource(eventSources);

    hiddenSource.onerror?.();
    expect(hiddenSource.closed).toBe(true);
    expect(onDisconnect).not.toHaveBeenCalled();

    setDocumentVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));

    expect(eventSources).toHaveLength(2);
    expect(eventSources[1]?.url).toBe(
      "/api/agent/workbench/conversations/agent_conv_1/events/stream?after_seq=7",
    );
    expect(onReconnect).toHaveBeenCalledOnce();

    cleanup();
    expect(eventSources[1]?.closed).toBe(true);
  });
});

function installAnimationFrame() {
  let frameId = 0;
  let callback: FrameRequestCallback | null = null;
  const cancelAnimationFrame = vi.fn();
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((next: FrameRequestCallback) => {
      frameId += 1;
      callback = next;
      return frameId;
    }),
  );
  vi.stubGlobal("cancelAnimationFrame", cancelAnimationFrame);

  return {
    cancelAnimationFrame,
    flush: () => {
      const next = callback;
      callback = null;
      next?.(16);
    },
  };
}

function installEventSource() {
  const eventSources: FakeEventSource[] = [];

  vi.stubGlobal(
    "EventSource",
    class extends FakeEventSource {
      constructor(url: string) {
        super(url);
        eventSources.push(this);
      }
    },
  );

  return eventSources;
}

function onlyEventSource(eventSources: FakeEventSource[]) {
  const source = eventSources[0];
  if (source === undefined) {
    throw new Error("EventSource was not created.");
  }
  return source;
}

class FakeEventSource {
  readonly listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >();
  closed = false;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {}

  addEventListener(
    eventName: string,
    listener: (event: MessageEvent<string>) => void,
  ) {
    this.listeners.set(eventName, [
      ...(this.listeners.get(eventName) ?? []),
      listener,
    ]);
  }

  close() {
    this.closed = true;
  }

  dispatch(eventName: string, data: string) {
    for (const listener of this.listeners.get(eventName) ?? []) {
      listener(new MessageEvent(eventName, { data }));
    }
  }
}

function setDocumentVisibility(visibilityState: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: visibilityState,
  });
}

function envelope({
  seq,
  kind,
  payload = {
    payloadType: "message.completed",
    kind: "message",
    messageId: "msg_1",
  },
}: {
  seq: number;
  kind: AgentStreamEnvelope["kind"];
  payload?: AgentStreamEnvelope["payload"];
}): AgentStreamEnvelope {
  return {
    schemaVersion: "agent.workbench.stream.v1",
    conversationId: "agent_conv_1",
    seq,
    kind,
    payload,
    createdAt: "2026-06-12T12:00:00+00:00",
  };
}
