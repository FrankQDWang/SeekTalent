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
}: ConnectAgentStreamOptions) {
  if (typeof EventSource === "undefined") {
    return () => {};
  }

  const batcher = createFrameBatcher<AgentStreamEnvelope>(onBatch);
  const source = new EventSource(
    `/api/agent/workbench/conversations/${encodeURIComponent(conversationId)}/events/stream?after_seq=${String(afterSeq)}`,
  );

  source.addEventListener("agent_workbench_event", (message) => {
    const envelope = parseEnvelope((message as MessageEvent<string>).data);
    if (envelope === null) {
      return;
    }
    if (envelope.kind === "stream.gap") {
      onGap();
    }
    batcher.push(envelope);
  });

  source.onerror = () => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState === "hidden"
    ) {
      source.close();
    }
  };

  return () => {
    batcher.cancel();
    source.close();
  };
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
