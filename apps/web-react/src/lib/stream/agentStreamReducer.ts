import type {
  AgentWorkbenchStreamEnvelope,
  AgentWorkbenchStreamKind,
  AgentWorkbenchStreamPayload,
} from "../api/agentWorkbenchTypes";

export type AgentStreamKind = AgentWorkbenchStreamKind;
export type AgentStreamPayload = AgentWorkbenchStreamPayload;
export type AgentStreamEnvelope = AgentWorkbenchStreamEnvelope;

export type StreamMessage = {
  messageId: string;
  text: string;
};

export type StreamActivity = {
  activityId: string;
  activitySeq?: number;
  activityType?: string;
  sourceRuntimeRunId?: string;
  summary?: string;
};

export type AgentStreamState = {
  conversationId: string;
  latestSeq: number;
  gapDetected: boolean;
  messages: StreamMessage[];
  activities: Record<string, StreamActivity>;
  transcriptEvents: AgentStreamEnvelope[];
  graphEvents: AgentStreamEnvelope[];
  candidateEvents: AgentStreamEnvelope[];
  graphDirty: boolean;
};

export function initialAgentStreamState(
  conversationId: string,
  latestSeq = 0,
): AgentStreamState {
  return {
    conversationId,
    latestSeq,
    gapDetected: false,
    messages: [],
    activities: {},
    transcriptEvents: [],
    graphEvents: [],
    candidateEvents: [],
    graphDirty: false,
  };
}

export function applyStreamEnvelope(
  state: AgentStreamState,
  envelope: AgentStreamEnvelope,
): AgentStreamState {
  if (
    envelope.conversationId !== state.conversationId ||
    envelope.seq <= state.latestSeq
  ) {
    return state;
  }

  if (envelope.seq !== state.latestSeq + 1) {
    return { ...state, gapDetected: true };
  }

  if (envelope.kind === "stream.gap") {
    return {
      ...state,
      latestSeq: envelope.seq,
      gapDetected: true,
      transcriptEvents: [...state.transcriptEvents, envelope],
    };
  }

  if (envelope.kind === "message.created") {
    const payload = messagePayload(envelope.payload);
    return {
      ...state,
      latestSeq: envelope.seq,
      messages: [
        ...state.messages,
        {
          messageId: payload?.messageId ?? "",
          text: payload?.summary ?? payload?.delta ?? "",
        },
      ],
    };
  }

  if (envelope.kind === "message.delta") {
    const payload = messagePayload(envelope.payload);
    return {
      ...state,
      latestSeq: envelope.seq,
      messages: appendMessageDelta(
        state.messages,
        payload?.messageId ?? "",
        payload?.delta ?? "",
      ),
      transcriptEvents: [...state.transcriptEvents, envelope],
    };
  }

  if (envelope.kind === "activity.upserted") {
    const activity = activityFromPayload(envelope.payload);
    return {
      ...state,
      latestSeq: envelope.seq,
      activities:
        activity === null
          ? state.activities
          : { ...state.activities, [activity.activityId]: activity },
      graphDirty: true,
    };
  }

  if (envelope.kind === "strategyGraph.changed") {
    return {
      ...state,
      latestSeq: envelope.seq,
      graphDirty: true,
      graphEvents: [...state.graphEvents, envelope],
    };
  }

  if (envelope.kind === "candidate.upserted") {
    return {
      ...state,
      latestSeq: envelope.seq,
      candidateEvents: [...state.candidateEvents, envelope],
    };
  }

  if (isTranscriptEvent(envelope.kind)) {
    return {
      ...state,
      latestSeq: envelope.seq,
      transcriptEvents: [...state.transcriptEvents, envelope],
    };
  }

  return { ...state, latestSeq: envelope.seq };
}

function appendMessageDelta(
  messages: StreamMessage[],
  messageId: string,
  delta: string,
): StreamMessage[] {
  const existingIndex = messages.findIndex(
    (message) => message.messageId === messageId,
  );
  if (existingIndex === -1) {
    return [...messages, { messageId, text: delta }];
  }

  return messages.map((message, index) =>
    index === existingIndex
      ? { ...message, text: `${message.text}${delta}` }
      : message,
  );
}

function activityFromPayload(
  payload: AgentStreamPayload,
): StreamActivity | null {
  if (payload.kind !== "activity") {
    return null;
  }

  const activity: StreamActivity = {
    activityId: payload.activityId,
  };
  if (payload.activitySeq !== undefined && payload.activitySeq !== null) {
    activity.activitySeq = payload.activitySeq;
  }
  if (payload.activityType !== undefined && payload.activityType !== null) {
    activity.activityType = payload.activityType;
  }
  if (payload.sourceRuntimeRunId !== undefined) {
    if (payload.sourceRuntimeRunId !== null) {
      activity.sourceRuntimeRunId = payload.sourceRuntimeRunId;
    }
  }
  if (payload.summary !== undefined && payload.summary !== null) {
    activity.summary = payload.summary;
  }
  return activity;
}

function messagePayload(
  payload: AgentStreamPayload,
): Extract<AgentStreamPayload, { kind: "message" }> | null {
  return payload.kind === "message" ? payload : null;
}

function isTranscriptEvent(kind: AgentStreamKind): boolean {
  return (
    kind.startsWith("item.") ||
    kind.startsWith("operation.") ||
    kind.startsWith("sourceSearch.") ||
    kind.startsWith("webSearch.") ||
    kind.startsWith("command.") ||
    kind === "message.delta" ||
    kind === "message.completed" ||
    kind === "runtime.eventProjected" ||
    kind === "runtime.stageChanged" ||
    kind === "context.compacted" ||
    kind === "transcript.groupCollapsed"
  );
}
