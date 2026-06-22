import { useEffect, useMemo, useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  amendAgentWorkbenchRequirementFromText,
  confirmAgentWorkbenchRequirements,
  createAgentConversation,
  getAgentWorkbenchCandidateDetail,
  getAgentWorkbenchConversation,
  listAgentWorkbenchConversations,
  submitAgentWorkbenchMessage,
  updateAgentWorkbenchRequirementDraft,
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
import type {
  AgentWorkbenchConversationResponse,
  RequirementDraftOperationRequest,
} from "./agentWorkbenchTypes";

type RequirementDraftUpdateInput = {
  draftRevisionId: string;
  operations: RequirementDraftOperationRequest[];
};

type RequirementDraftAmendInput = {
  draftRevisionId: string;
  targetSectionHint?: string | null | undefined;
  text: string;
};

type PendingOperationKey = {
  signature: string;
  idempotencyKey: string;
};

type PendingCreateFromJdOperation = {
  signature: string;
  conversationId: string | null;
  submitIdempotencyKey: string;
};

export type CreateAgentWorkbenchConversationFromJdInput = {
  jobDescription: string;
  jobTitle?: string | null | undefined;
};

export type CreateAgentWorkbenchConversationFromJdOutput = {
  conversationId: string;
  view: AgentWorkbenchConversationResponse;
};

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

export function useCreateAgentWorkbenchConversationFromJd() {
  const queryClient = useQueryClient();
  const pendingCreate = useRef<PendingCreateFromJdOperation | null>(null);

  return useMutation<
    CreateAgentWorkbenchConversationFromJdOutput,
    Error,
    CreateAgentWorkbenchConversationFromJdInput
  >({
    mutationFn: async ({ jobDescription, jobTitle }) => {
      const text = jobDescription.trim();
      if (text.length === 0) {
        throw new Error("Job description is required.");
      }
      const normalizedJobTitle = normalizeOptionalText(jobTitle);
      const signature = operationSignature("create-from-jd", {
        jobTitle: normalizedJobTitle,
        text,
      });
      if (pendingCreate.current?.signature !== signature) {
        pendingCreate.current = {
          signature,
          conversationId: null,
          submitIdempotencyKey: actionIdempotencyKey("submit-jd"),
        };
      }
      const pending = pendingCreate.current;
      let conversationId = pending.conversationId;
      if (conversationId === null) {
        const title = conversationTitleFromInput(jobTitle, text);
        const created = await createAgentConversation({ title });
        conversationId = created.conversation.conversationId;
        pending.conversationId = conversationId;
      }
      const view = await submitAgentWorkbenchMessage(conversationId, {
        idempotencyKey: pending.submitIdempotencyKey,
        jobTitle: normalizedJobTitle,
        messageType: "submitJd",
        notes: null,
        sourceKinds: ["liepin"],
        text,
      });
      return { conversationId, view };
    },
    onSuccess: ({ conversationId, view }) => {
      pendingCreate.current = null;
      applyActionSnapshot(
        queryClient,
        queryKeys.agentConversation(conversationId),
        view,
      );
      void queryClient.invalidateQueries({
        queryKey: queryKeys.agentConversations,
      });
    },
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
  const operationKey = useStableOperationKey("message");
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: (message: string) => {
      const signature = operationSignature("message", {
        conversationId,
        message,
      });
      return submitAgentWorkbenchMessage(conversationId, {
        idempotencyKey: operationKey.keyFor(signature),
        messageType: "userText",
        text: message,
      });
    },
    onSuccess: (next, message) => {
      operationKey.clear(
        operationSignature("message", {
          conversationId,
          message,
        }),
      );
      applyActionSnapshot(queryClient, queryKey, next);
    },
  });
}

export function useConfirmAgentWorkbenchRequirements(conversationId: string) {
  const queryClient = useQueryClient();
  const operationKey = useStableOperationKey("confirm-requirements");
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: (draftRevisionId: string) => {
      const signature = operationSignature("confirm-requirements", {
        conversationId,
        draftRevisionId,
      });
      return confirmAgentWorkbenchRequirements(conversationId, {
        draftRevisionId,
        expectedDraftRevisionId: draftRevisionId,
        idempotencyKey: operationKey.keyFor(signature),
      });
    },
    onSuccess: (next, draftRevisionId) => {
      operationKey.clear(
        operationSignature("confirm-requirements", {
          conversationId,
          draftRevisionId,
        }),
      );
      applyActionSnapshot(queryClient, queryKey, next);
    },
  });
}

export function useUpdateAgentWorkbenchRequirementDraft(
  conversationId: string,
) {
  const queryClient = useQueryClient();
  const operationKey = useStableOperationKey("requirement-update");
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: ({
      draftRevisionId,
      operations,
    }: RequirementDraftUpdateInput) => {
      const signature = operationSignature("requirement-update", {
        conversationId,
        draftRevisionId,
        operations,
      });
      return updateAgentWorkbenchRequirementDraft(conversationId, {
        draftRevisionId,
        expectedDraftRevisionId: draftRevisionId,
        idempotencyKey: operationKey.keyFor(signature),
        operations,
      });
    },
    onSuccess: (next, variables) => {
      operationKey.clear(
        operationSignature("requirement-update", {
          conversationId,
          draftRevisionId: variables.draftRevisionId,
          operations: variables.operations,
        }),
      );
      applyActionSnapshot(queryClient, queryKey, next);
    },
  });
}

export function useAmendAgentWorkbenchRequirementFromText(
  conversationId: string,
) {
  const queryClient = useQueryClient();
  const operationKey = useStableOperationKey("requirement-amend");
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );

  return useMutation({
    mutationFn: ({
      draftRevisionId,
      targetSectionHint = null,
      text,
    }: RequirementDraftAmendInput) => {
      const signature = operationSignature("requirement-amend", {
        conversationId,
        draftRevisionId,
        targetSectionHint,
        text,
      });
      return amendAgentWorkbenchRequirementFromText(conversationId, {
        draftRevisionId,
        expectedDraftRevisionId: draftRevisionId,
        idempotencyKey: operationKey.keyFor(signature),
        targetSectionHint,
        text,
      });
    },
    onSuccess: (next, variables) => {
      operationKey.clear(
        operationSignature("requirement-amend", {
          conversationId,
          draftRevisionId: variables.draftRevisionId,
          targetSectionHint: variables.targetSectionHint ?? null,
          text: variables.text,
        }),
      );
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
  const latestSnapshotSeq = useRef<number | undefined>(undefined);
  const latestViewRevision = useRef<number | undefined>(undefined);
  const streamState = useRef<AgentStreamState>(
    initialAgentStreamState(conversationId),
  );

  useEffect(() => {
    latestSnapshotSeq.current = snapshotSeq;
    latestViewRevision.current = viewRevision;
  }, [snapshotSeq, viewRevision]);

  useEffect(() => {
    streamState.current = initialAgentStreamState(conversationId);
  }, [conversationId]);

  useEffect(() => {
    if (
      !query.isSuccess ||
      latestSnapshotSeq.current === undefined ||
      latestViewRevision.current === undefined
    ) {
      return;
    }
    const streamStartSeq = latestSnapshotSeq.current;
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
  }, [conversationId, query.isSuccess, queryClient, queryKey]);

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

function useStableOperationKey(action: string) {
  const pending = useRef<PendingOperationKey | null>(null);
  return {
    keyFor(signature: string): string {
      if (pending.current?.signature !== signature) {
        pending.current = {
          signature,
          idempotencyKey: actionIdempotencyKey(action),
        };
      }
      return pending.current.idempotencyKey;
    },
    clear(signature: string) {
      if (pending.current?.signature === signature) {
        pending.current = null;
      }
    },
  };
}

function operationSignature(action: string, payload: unknown): string {
  return JSON.stringify([action, payload]);
}

function conversationTitleFromInput(
  jobTitle: string | null | undefined,
  jobDescription: string,
): string {
  const normalizedTitle = normalizeOptionalText(jobTitle);
  if (normalizedTitle !== null) {
    return truncateConversationTitle(normalizedTitle);
  }
  return truncateConversationTitle(
    jobDescription.replace(/\s+/g, " ").trim() || "新的寻才任务",
  );
}

function normalizeOptionalText(
  value: string | null | undefined,
): string | null {
  const normalized = value?.trim() ?? "";
  return normalized.length > 0 ? normalized : null;
}

function truncateConversationTitle(title: string): string {
  return title.length > 120 ? title.slice(0, 120) : title;
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
