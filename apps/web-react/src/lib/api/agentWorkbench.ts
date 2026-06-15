import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getAgentWorkbenchConversation,
  listAgentWorkbenchConversations,
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
import { mergeStreamEnvelopesIntoConversation } from "../stream/agentStreamView";
import type { AgentWorkbenchConversationResponse } from "./agentWorkbenchTypes";

export function useAgentWorkbenchConversations() {
  return useQuery({
    queryKey: queryKeys.agentConversations,
    queryFn: listAgentWorkbenchConversations,
  });
}

export function useAgentWorkbenchConversation(conversationId: string) {
  return useQuery({
    queryKey: queryKeys.agentConversation(conversationId),
    queryFn: () => getAgentWorkbenchConversation(conversationId),
  });
}

export function useAgentWorkbenchLiveConversation(conversationId: string) {
  const queryClient = useQueryClient();
  const query = useAgentWorkbenchConversation(conversationId);
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );
  const streamState = useRef<AgentStreamState>(
    initialAgentStreamState(conversationId),
  );

  useEffect(() => {
    streamState.current = initialAgentStreamState(conversationId);
  }, [conversationId]);

  useEffect(() => {
    if (!query.isSuccess) {
      return;
    }
    const latestStreamSeq = query.data.streamCursor.latestStreamSeq;
    streamState.current = initialAgentStreamState(
      conversationId,
      latestStreamSeq,
    );

    const cleanup = connectAgentStream({
      conversationId,
      afterSeq: latestStreamSeq,
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
        void queryClient.invalidateQueries({
          queryKey,
        });
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
    });

    return cleanup;
  }, [conversationId, query.isSuccess, queryClient, queryKey]);

  return query;
}
