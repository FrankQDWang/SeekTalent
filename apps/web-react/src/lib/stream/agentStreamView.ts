import type {
  AgentWorkbenchActivity,
  AgentWorkbenchConversationResponse,
  AgentWorkbenchStatus,
  AgentWorkbenchStreamEnvelope,
  AgentWorkbenchStreamPayload,
  AgentWorkbenchTranscriptEvent,
  AgentWorkbenchTranscriptGroup,
  AgentWorkbenchTranscriptPayload,
} from "../api/agentWorkbenchTypes";

const LIVE_GROUP_SUFFIX = "stream-live";

export function mergeStreamEnvelopesIntoConversation(
  view: AgentWorkbenchConversationResponse,
  envelopes: AgentWorkbenchStreamEnvelope[],
): AgentWorkbenchConversationResponse {
  if (envelopes.length === 0) {
    return view;
  }

  let latestSeq = view.streamCursor.latestStreamSeq;
  const accepted = envelopes
    .filter(
      (envelope) =>
        envelope.conversationId === view.conversation.conversationId,
    )
    .sort((left, right) => left.seq - right.seq)
    .filter((envelope) => {
      if (envelope.seq <= latestSeq) {
        return false;
      }
      latestSeq = envelope.seq;
      return true;
    });

  if (accepted.length === 0) {
    return view;
  }
  const lastEnvelope = accepted[accepted.length - 1];
  if (lastEnvelope === undefined) {
    return view;
  }

  return {
    ...view,
    activities: mergeActivitySummaries(view.activities, accepted),
    conversation: {
      ...view.conversation,
      status: accepted.some((envelope) => envelope.kind === "stream.gap")
        ? "disconnected"
        : view.conversation.status,
      updatedAt: lastEnvelope.createdAt,
    },
    transcriptGroups: appendLiveTranscriptEvents(view, accepted),
    streamCursor: {
      ...view.streamCursor,
      latestStreamSeq: latestSeq,
    },
  };
}

function mergeActivitySummaries(
  activities: AgentWorkbenchActivity[],
  envelopes: AgentWorkbenchStreamEnvelope[],
): AgentWorkbenchActivity[] {
  let nextActivities = activities;
  for (const envelope of envelopes) {
    const payload = envelope.payload;
    if (envelope.kind !== "activity.upserted" || payload.kind !== "activity") {
      continue;
    }
    nextActivities = nextActivities.map((activity) => {
      if (activity.activityId !== payload.activityId) {
        return activity;
      }
      return {
        ...activity,
        summary: payload.summary ?? activity.summary,
        updatedAt: envelope.createdAt,
      };
    });
  }
  return nextActivities;
}

function appendLiveTranscriptEvents(
  view: AgentWorkbenchConversationResponse,
  envelopes: AgentWorkbenchStreamEnvelope[],
): AgentWorkbenchTranscriptGroup[] {
  const groupId = `${view.conversation.conversationId}:${LIVE_GROUP_SUFFIX}`;
  const liveEvents = envelopes.map(streamEnvelopeToTranscriptEvent);
  const groups = view.transcriptGroups;
  const existingIndex = groups.findIndex((group) => group.groupId === groupId);

  if (existingIndex === -1) {
    return [
      ...groups,
      {
        groupId,
        title: "实时事件",
        status: "running",
        startedAt: liveEvents[0]?.createdAt ?? null,
        completedAt: null,
        events: liveEvents,
      },
    ];
  }

  const existingGroup = groups[existingIndex];
  if (existingGroup === undefined) {
    return groups;
  }
  const existingEventIds = new Set(
    existingGroup.events.map((event) => event.eventId),
  );
  const nextGroup: AgentWorkbenchTranscriptGroup = {
    ...existingGroup,
    events: [
      ...existingGroup.events,
      ...liveEvents.filter((event) => !existingEventIds.has(event.eventId)),
    ],
  };
  return groups.map((group, index) =>
    index === existingIndex ? nextGroup : group,
  );
}

function streamEnvelopeToTranscriptEvent(
  envelope: AgentWorkbenchStreamEnvelope,
): AgentWorkbenchTranscriptEvent {
  return {
    eventId: `stream:${String(envelope.seq)}`,
    itemId: streamItemId(envelope),
    kind: envelope.kind,
    status: streamStatus(envelope.kind),
    label: streamLabel(envelope.kind),
    summary: streamSummary(envelope.payload),
    payload: streamPayloadToTranscriptPayload(envelope.payload),
    createdAt: envelope.createdAt,
  };
}

function streamItemId(envelope: AgentWorkbenchStreamEnvelope): string {
  const payload = envelope.payload;
  if (payload.kind === "message") {
    return payload.messageId;
  }
  if (payload.kind === "activity") {
    return payload.activityId;
  }
  if (payload.kind === "gap") {
    return `seq:${String(envelope.seq)}`;
  }
  return payload.itemId;
}

function streamSummary(payload: AgentWorkbenchStreamPayload): string | null {
  if (payload.kind === "message") {
    return payload.summary ?? payload.delta ?? null;
  }
  if (payload.kind === "activity" || payload.kind === "gap") {
    return payload.summary ?? null;
  }
  return payload.summary ?? payload.delta ?? null;
}

function streamPayloadToTranscriptPayload(
  payload: AgentWorkbenchStreamPayload,
): AgentWorkbenchTranscriptPayload {
  if (payload.kind === "message") {
    return {
      kind: "message",
      messageId: payload.messageId,
      delta: payload.delta ?? null,
      summary: payload.summary ?? null,
    };
  }
  if (payload.kind === "activity") {
    return {
      kind: "activity",
      activityId: payload.activityId,
      activitySeq: payload.activitySeq ?? null,
      activityType: payload.activityType ?? null,
      sourceRuntimeRunId: payload.sourceRuntimeRunId ?? null,
      summary: payload.summary ?? null,
    };
  }
  if (payload.kind === "gap") {
    return {
      kind: "gap",
      missingFromSeq: payload.missingFromSeq,
      nextAvailableSeq: payload.nextAvailableSeq,
      summary: payload.summary ?? null,
    };
  }
  return {
    kind: payload.kind,
    itemId: payload.itemId,
    delta: payload.delta ?? null,
    sourceRuntimeRunId: payload.sourceRuntimeRunId ?? null,
    summary: payload.summary ?? null,
  };
}

function streamStatus(
  kind: AgentWorkbenchStreamEnvelope["kind"],
): AgentWorkbenchStatus | null {
  if (kind.endsWith(".failed")) {
    return "failed";
  }
  if (kind.endsWith(".completed") || kind === "finalSummary.updated") {
    return "completed";
  }
  if (
    kind.endsWith(".started") ||
    kind.endsWith(".changed") ||
    kind === "activity.upserted"
  ) {
    return "running";
  }
  return null;
}

function streamLabel(kind: AgentWorkbenchStreamEnvelope["kind"]): string {
  switch (kind) {
    case "activity.upserted":
      return "Runtime activity";
    case "strategyGraph.changed":
      return "Strategy graph";
    case "candidate.upserted":
      return "Candidate";
    case "detailApproval.changed":
      return "Detail approval";
    case "finalSummary.updated":
      return "Final shortlist";
    case "pendingAction.changed":
      return "Pending action";
    case "sourceConnection.changed":
      return "Source connection";
    case "thinkingProcess.changed":
      return "Runtime thinking";
    case "stream.gap":
      return "Stream recovery";
    default:
      return kind;
  }
}
