export const queryKeys = {
  agentConversations: ["agent", "workbench", "conversations"] as const,
  agentConversation: (conversationId: string) =>
    ["agent", "workbench", "conversations", conversationId] as const,
  sourceConnections: ["workbench", "source-connections"] as const,
};
