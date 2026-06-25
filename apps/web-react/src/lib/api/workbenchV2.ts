import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createWorkbenchV2Conversation,
  getWorkbenchV2Conversation,
  listWorkbenchV2Conversations,
  submitWorkbenchV2Message,
} from "./workbenchV2Client";
import type {
  WorkbenchV2ConversationView,
  WorkbenchV2MessageRequest,
} from "./workbenchV2Types";
import { queryKeys } from "../query/keys";

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
    refetchInterval: 2_000,
  });
}

export function useCreateWorkbenchV2Conversation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: WorkbenchV2MessageRequest) =>
      createWorkbenchV2Conversation(payload),
    onSuccess: (view) => {
      queryClient.setQueryData(
        queryKeys.workbenchV2Conversation(view.conversation.conversationId),
        view,
      );
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
    onSuccess: (view) => {
      queryClient.setQueryData(
        queryKeys.workbenchV2Conversation(view.conversation.conversationId),
        view,
      );
    },
  });
}
