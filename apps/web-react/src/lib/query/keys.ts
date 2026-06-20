export const queryKeys = {
  agentConversations: ["agent", "workbench", "conversations"] as const,
  agentConversation: (conversationId: string) =>
    ["agent", "workbench", "conversations", conversationId] as const,
  agentCandidateDetails: (conversationId: string) =>
    [
      "agent",
      "workbench",
      "conversations",
      conversationId,
      "candidates",
    ] as const,
  agentCandidateDetail: (conversationId: string, candidateId: string) =>
    [
      ...queryKeys.agentCandidateDetails(conversationId),
      candidateId,
      "detail",
    ] as const,
  sourceConnections: ["workbench", "source-connections"] as const,
};
