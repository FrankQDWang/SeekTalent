import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyWorkbenchV2RequirementAction,
  createWorkbenchV2Conversation,
  getWorkbenchV2CandidateDetail,
  getWorkbenchV2Conversation,
  listWorkbenchV2Conversations,
  submitWorkbenchV2Message,
} from "./workbenchV2Client";
import type {
  WorkbenchV2ConversationView,
  WorkbenchV2MessageRequest,
  WorkbenchV2RequirementActionRequest,
} from "./workbenchV2Types";
import { queryKeys } from "../query/keys";

type WorkbenchV2ConversationQueryKey = ReturnType<
  typeof queryKeys.workbenchV2Conversation
>;

export function shouldApplyWorkbenchV2Snapshot(
  current: WorkbenchV2ConversationView | undefined,
  next: WorkbenchV2ConversationView,
): boolean {
  if (current === undefined) {
    return true;
  }

  const updatedAtComparison = compareUpdatedAt(
    current.conversation.updatedAt,
    next.conversation.updatedAt,
  );
  if (updatedAtComparison !== 0) {
    return updatedAtComparison < 0;
  }

  return maxTranscriptStep(next) >= maxTranscriptStep(current);
}

export function workbenchV2RefetchInterval(
  view: WorkbenchV2ConversationView | undefined,
): 2000 | false {
  const runtimeState = view?.conversation.runtimeState;
  return runtimeState === "queued" || runtimeState === "running"
    ? 2_000
    : false;
}

export function useWorkbenchV2Conversations() {
  return useQuery({
    queryKey: queryKeys.workbenchV2Conversations,
    queryFn: listWorkbenchV2Conversations,
  });
}

export function useWorkbenchV2Conversation(conversationId: string) {
  return useQuery<WorkbenchV2ConversationView>({
    queryKey: queryKeys.workbenchV2Conversation(conversationId),
    queryFn: () => getWorkbenchV2Conversation(conversationId),
    structuralSharing: (current, next) => {
      const currentSnapshot = current as
        | WorkbenchV2ConversationView
        | undefined;
      const nextSnapshot = next as WorkbenchV2ConversationView;
      return shouldApplyWorkbenchV2Snapshot(currentSnapshot, nextSnapshot)
        ? nextSnapshot
        : currentSnapshot;
    },
    refetchInterval: (query) => workbenchV2RefetchInterval(query.state.data),
  });
}

export function useWorkbenchV2CandidateDetail(
  conversationId: string,
  candidateId: string | null,
) {
  return useQuery({
    enabled: candidateId !== null,
    queryKey:
      candidateId === null
        ? queryKeys.workbenchV2CandidateDetails(conversationId)
        : queryKeys.workbenchV2CandidateDetail(conversationId, candidateId),
    queryFn: () => {
      if (candidateId === null) {
        throw new Error("Candidate detail query requires a candidate id.");
      }
      return getWorkbenchV2CandidateDetail(conversationId, candidateId);
    },
  });
}

export function useCreateWorkbenchV2Conversation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: WorkbenchV2MessageRequest) =>
      createWorkbenchV2Conversation(payload),
    onSuccess: async (view) => {
      const queryKey = queryKeys.workbenchV2Conversation(
        view.conversation.conversationId,
      );
      await queryClient.cancelQueries({ queryKey });
      applyWorkbenchV2Snapshot(queryClient, queryKey, view);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.workbenchV2Conversations,
      });
    },
  });
}

export function useSubmitWorkbenchV2Message(conversationId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: WorkbenchV2MessageRequest) =>
      submitWorkbenchV2Message(conversationId, payload),
    onSuccess: async (view) => {
      const queryKey = queryKeys.workbenchV2Conversation(
        view.conversation.conversationId,
      );
      await queryClient.cancelQueries({ queryKey });
      applyWorkbenchV2Snapshot(queryClient, queryKey, view);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.workbenchV2Conversations,
      });
    },
  });
}

export function useApplyWorkbenchV2RequirementAction(conversationId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: WorkbenchV2RequirementActionRequest) =>
      applyWorkbenchV2RequirementAction(conversationId, payload),
    onSuccess: async (view) => {
      const queryKey = queryKeys.workbenchV2Conversation(
        view.conversation.conversationId,
      );
      await queryClient.cancelQueries({ queryKey });
      applyWorkbenchV2Snapshot(queryClient, queryKey, view);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.workbenchV2Conversations,
      });
    },
  });
}

function applyWorkbenchV2Snapshot(
  queryClient: ReturnType<typeof useQueryClient>,
  queryKey: WorkbenchV2ConversationQueryKey,
  next: WorkbenchV2ConversationView,
) {
  queryClient.setQueryData<WorkbenchV2ConversationView>(queryKey, (current) =>
    shouldApplyWorkbenchV2Snapshot(current, next) ? next : current,
  );
}

function compareUpdatedAt(current: string, next: string): number {
  const currentTime = Date.parse(current);
  const nextTime = Date.parse(next);
  if (Number.isFinite(currentTime) && Number.isFinite(nextTime)) {
    return currentTime - nextTime;
  }
  return current.localeCompare(next);
}

function maxTranscriptStep(view: WorkbenchV2ConversationView): number {
  return view.transcriptEvents.reduce(
    (maxStep, event) => Math.max(maxStep, event.step),
    0,
  );
}
