import type {
  AgentStreamEnvelope,
  AgentStreamKind,
} from "./agentStreamReducer";
import { createFrameBatcher } from "./frameBatcher";

type ConnectAgentStreamOptions = {
  conversationId: string;
  afterSeq: number;
  onBatch: (events: AgentStreamEnvelope[]) => void;
  onGap: () => void;
  onDisconnect?: () => void;
  onReconnect?: () => void;
};

type JsonPrimitive = string | number | boolean | null;
type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
type JsonObject = {
  readonly [key: string]: JsonValue | undefined;
};

const STREAM_SCHEMA_VERSION = "agent.workbench.stream.v1";
const STREAM_KINDS = new Set<AgentStreamKind>([
  "item.started",
  "item.completed",
  "message.created",
  "message.delta",
  "message.completed",
  "activity.upserted",
  "requirement.updated",
  "runtime.eventProjected",
  "strategyGraph.changed",
  "tool.started",
  "tool.outputDelta",
  "tool.completed",
  "tool.failed",
  "sourceSearch.started",
  "sourceSearch.completed",
  "sourceSearch.failed",
  "webSearch.started",
  "webSearch.completed",
  "command.started",
  "command.outputDelta",
  "command.completed",
  "command.failed",
  "runtime.stageChanged",
  "candidate.upserted",
  "detailApproval.changed",
  "finalSummary.updated",
  "pendingAction.changed",
  "sourceConnection.changed",
  "context.compacted",
  "transcript.groupCollapsed",
  "thinkingProcess.changed",
  "stream.gap",
]);

export function connectAgentStream({
  conversationId,
  afterSeq,
  onBatch,
  onGap,
  onDisconnect,
  onReconnect,
}: ConnectAgentStreamOptions) {
  if (typeof EventSource === "undefined") {
    return () => {};
  }

  const batcher = createFrameBatcher<AgentStreamEnvelope>(onBatch);
  let latestSeq = afterSeq;
  let source: EventSource | null = null;
  let waitingForVisibleReconnect = false;
  let cleanedUp = false;

  const openSource = () => {
    const nextSource = new EventSource(streamUrl(conversationId, latestSeq));
    source = nextSource;
    waitingForVisibleReconnect = false;

    nextSource.addEventListener("agent_workbench_event", (message) => {
      const envelope = parseEnvelope((message as MessageEvent<string>).data);
      if (envelope === null) {
        return;
      }
      latestSeq = Math.max(latestSeq, envelope.seq);
      if (envelope.kind === "stream.gap") {
        onGap();
      }
      batcher.push(envelope);
    });

    nextSource.onerror = () => {
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        nextSource.close();
        if (source === nextSource) {
          source = null;
        }
        waitingForVisibleReconnect = true;
        return;
      }
      onDisconnect?.();
    };
  };

  const handleVisibilityChange = () => {
    if (
      cleanedUp ||
      !waitingForVisibleReconnect ||
      typeof document === "undefined" ||
      document.visibilityState !== "visible"
    ) {
      return;
    }
    openSource();
    onReconnect?.();
  };

  openSource();

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  return () => {
    cleanedUp = true;
    batcher.cancel();
    source?.close();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    }
  };
}

function streamUrl(conversationId: string, afterSeq: number): string {
  return `/api/agent/workbench/conversations/${encodeURIComponent(conversationId)}/events/stream?after_seq=${String(afterSeq)}`;
}

function parseEnvelope(data: string): AgentStreamEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }

  if (!isJsonObject(parsed)) {
    return null;
  }
  if (parsed.schemaVersion !== STREAM_SCHEMA_VERSION) {
    return null;
  }
  if (
    typeof parsed.conversationId !== "string" ||
    typeof parsed.seq !== "number" ||
    !Number.isInteger(parsed.seq) ||
    typeof parsed.kind !== "string" ||
    !STREAM_KINDS.has(parsed.kind as AgentStreamKind) ||
    !isJsonObject(parsed.payload) ||
    !payloadMatchesKind(parsed.payload, parsed.kind) ||
    typeof parsed.createdAt !== "string"
  ) {
    return null;
  }

  return parsed as AgentStreamEnvelope;
}

function payloadMatchesKind(payload: JsonObject, kind: string): boolean {
  return payload.payloadType === kind;
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
