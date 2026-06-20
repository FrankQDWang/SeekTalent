import { useEffect, useMemo, useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  confirmAgentWorkbenchRequirements,
  getAgentWorkbenchCandidateDetail,
  getAgentWorkbenchConversation,
  listAgentWorkbenchConversations,
  submitAgentWorkbenchMessage,
} from "./client";
import { queryKeys } from "../query/keys";
import { useQueryClient } from "@tanstack/react-query";
import { connectAgentStream } from "../stream/agentStream";
import {
  applyStreamEnvelope,
  initialAgentStreamState,
  type AgentStreamEnvelope,
  type AgentStreamState,
} from "../stream/agentStreamReducer";
import {
  isSnapshotDependentStreamKind,
  mergeStreamEnvelopesIntoConversation,
} from "../stream/agentStreamView";
import type { AgentWorkbenchConversationResponse } from "./agentWorkbenchTypes";

export function shouldApplyWorkbenchSnapshot(
  current: AgentWorkbenchConversationResponse | undefined,
  next: AgentWorkbenchConversationResponse,
): boolean {
  if (current === undefined) {
    return true;
  }
  const currentAppliedStreamSeq = Math.max(
    current.streamCursor.snapshotSeq,
    current.streamCursor.latestStreamSeq,
  );
  const nextAppliedStreamSeq = Math.max(
    next.streamCursor.snapshotSeq,
    next.streamCursor.latestStreamSeq,
  );
  return nextAppliedStreamSeq >= currentAppliedStreamSeq;
}

export function workbenchStreamStartSeq(
  view: AgentWorkbenchConversationResponse,
): number {
  return view.streamCursor.snapshotSeq;
}

export function useAgentWorkbenchConversations() {
  return useQuery({
    queryKey: queryKeys.agentConversations,
    queryFn: listAgentWorkbenchConversations,
  });
}

export function useAgentWorkbenchConversation(conversationId: string) {
  return useQuery<AgentWorkbenchConversationResponse>({
    queryKey: queryKeys.agentConversation(conversationId),
    queryFn: () => getAgentWorkbenchConversation(conversationId),
    structuralSharing: (current, next) => {
      const currentSnapshot = current as
        | AgentWorkbenchConversationResponse
        | undefined;
      const nextSnapshot = next as AgentWorkbenchConversationResponse;
      return shouldApplyWorkbenchSnapshot(currentSnapshot, nextSnapshot)
        ? nextSnapshot
        : currentSnapshot;
    },
  });
}

export function useAgentWorkbenchCandidateDetail(
  conversationId: string,
  candidateId: string | null,
) {
  return useQuery({
    enabled: candidateId !== null,
    queryKey:
      candidateId === null
        ? queryKeys.agentCandidateDetails(conversationId)
        : queryKeys.agentCandidateDetail(conversationId, candidateId),
    queryFn: () => {
      if (candidateId === null) {
        throw new Error("Candidate detail query requires a candidate id.");
      }
      return getAgentWorkbenchCandidateDetail(conversationId, candidateId);
    },
  });
}

export function useSubmitAgentWorkbenchMessage(conversationId: string) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: (message: string) =>
      submitAgentWorkbenchMessage(conversationId, {
        idempotencyKey: actionIdempotencyKey("message"),
        messageType: "userText",
        text: message,
      }),
    onSuccess: (next) => {
      applyActionSnapshot(queryClient, queryKey, next);
    },
  });
}

export function useConfirmAgentWorkbenchRequirements(conversationId: string) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: (draftRevisionId: string) =>
      confirmAgentWorkbenchRequirements(conversationId, {
        draftRevisionId,
        expectedDraftRevisionId: draftRevisionId,
        idempotencyKey: actionIdempotencyKey("confirm-requirements"),
      }),
    onSuccess: (next) => {
      applyActionSnapshot(queryClient, queryKey, next);
    },
  });
}

export function useAgentWorkbenchLiveConversation(conversationId: string) {
  const queryClient = useQueryClient();
  const query = useAgentWorkbenchConversation(conversationId);
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );
  const snapshotSeq = query.data?.streamCursor.snapshotSeq;
  const viewRevision = query.data?.streamCursor.viewRevision;
  const streamState = useRef<AgentStreamState>(
    initialAgentStreamState(conversationId),
  );

  useEffect(() => {
    streamState.current = initialAgentStreamState(conversationId);
  }, [conversationId]);

  useEffect(() => {
    if (
      !query.isSuccess ||
      snapshotSeq === undefined ||
      viewRevision === undefined
    ) {
      return;
    }
    const streamStartSeq = snapshotSeq;
    streamState.current = initialAgentStreamState(
      conversationId,
      streamStartSeq,
    );

    const cleanup = connectAgentStream({
      conversationId,
      afterSeq: streamStartSeq,
      onBatch: (events) => {
        let next = streamState.current;
        const acceptedEvents: AgentStreamEnvelope[] = [];
        for (const event of events) {
          const previousLatestSeq = next.latestSeq;
          next = applyStreamEnvelope(next, event);
          if (
            next.latestSeq === event.seq &&
            next.latestSeq !== previousLatestSeq
          ) {
            acceptedEvents.push(event);
          }
        }
        streamState.current = next;
        if (acceptedEvents.length > 0) {
          queryClient.setQueryData<AgentWorkbenchConversationResponse>(
            queryKey,
            (current) =>
              current === undefined
                ? current
                : mergeStreamEnvelopesIntoConversation(current, acceptedEvents),
          );
        }
        if (acceptedEvents.some(isSnapshotDependentStreamKind)) {
          void queryClient.invalidateQueries({
            queryKey,
          });
        }
        if (acceptedEvents.some(isCandidateDetailDependentStreamKind)) {
          void queryClient.invalidateQueries({
            queryKey: queryKeys.agentCandidateDetails(conversationId),
          });
        }
      },
      onGap: () => {
        streamState.current = {
          ...streamState.current,
          gapDetected: true,
        };
        void queryClient.invalidateQueries({
          queryKey,
        });
      },
      onDisconnect: () => {
        markConversationDisconnected(
          queryClient,
          queryKey,
          "stream_disconnected",
        );
      },
      onReconnect: () => {
        void queryClient.invalidateQueries({
          queryKey,
        });
      },
    });

    return cleanup;
  }, [
    conversationId,
    query.isSuccess,
    queryClient,
    queryKey,
    snapshotSeq,
    viewRevision,
  ]);

  return query;
}

function isCandidateDetailDependentStreamKind(
  envelope: Pick<AgentStreamEnvelope, "kind">,
): boolean {
  return (
    envelope.kind === "candidate.upserted" ||
    envelope.kind === "detailApproval.changed"
  );
}

function applyActionSnapshot(
  queryClient: ReturnType<typeof useQueryClient>,
  queryKey: ReturnType<typeof queryKeys.agentConversation>,
  next: AgentWorkbenchConversationResponse,
) {
  queryClient.setQueryData<AgentWorkbenchConversationResponse>(
    queryKey,
    (current) => (shouldApplyWorkbenchSnapshot(current, next) ? next : current),
  );
  void queryClient.invalidateQueries({ queryKey });
}

function actionIdempotencyKey(action: string): string {
  const randomUUID = (globalThis.crypto as { randomUUID?: () => string })
    .randomUUID;
  const id =
    typeof randomUUID === "function"
      ? randomUUID.call(globalThis.crypto)
      : `${String(Date.now())}:${Math.random().toString(36).slice(2)}`;
  return `workbench:${action}:${id}`;
}

function markConversationDisconnected(
  queryClient: ReturnType<typeof useQueryClient>,
  queryKey: ReturnType<typeof queryKeys.agentConversation>,
  reasonCode: string,
) {
  queryClient.setQueryData<AgentWorkbenchConversationResponse>(
    queryKey,
    (current) =>
      current === undefined
        ? current
        : {
            ...current,
            conversation: {
              ...current.conversation,
              status: "disconnected",
            },
            reasonCode,
          },
  );
  void queryClient.invalidateQueries({
    queryKey,
  });
}
