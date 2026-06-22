import { Circle, CircleCheck, CircleDashed } from "lucide-react";
import type { AgentWorkbenchConversationSummary } from "../../lib/api/agentWorkbenchTypes";
import { useAgentWorkbenchConversations } from "../../lib/api/agentWorkbench";
import "./ConversationList.css";

type ConversationListProps = {
  conversations?: readonly AgentWorkbenchConversationSummary[];
  selectedConversationId?: string;
};

export function ConversationList({
  conversations,
  selectedConversationId,
}: ConversationListProps) {
  if (conversations !== undefined) {
    return (
      <ConversationListView
        conversations={conversations}
        selectedConversationId={selectedConversationId}
      />
    );
  }
  return (
    <ConversationListQuery selectedConversationId={selectedConversationId} />
  );
}

function ConversationListQuery({
  selectedConversationId,
}: {
  selectedConversationId: string | undefined;
}) {
  const query = useAgentWorkbenchConversations();

  if (query.isPending) {
    return (
      <section className="conversation-list" data-state="loading">
        <h2>任务</h2>
        <p role="status">读取中</p>
      </section>
    );
  }
  if (query.isError) {
    return (
      <section className="conversation-list" data-state="error">
        <h2>任务</h2>
        <p role="alert">无法加载任务列表</p>
      </section>
    );
  }
  return (
    <ConversationListView
      conversations={query.data.conversations}
      selectedConversationId={selectedConversationId}
    />
  );
}

function ConversationListView({
  conversations,
  selectedConversationId,
}: {
  conversations: readonly AgentWorkbenchConversationSummary[];
  selectedConversationId: string | undefined;
}) {
  return (
    <nav aria-label="Agent conversations" className="conversation-list">
      <div className="conversation-list__header">
        <h2>历史任务</h2>
        <span className="conversation-list__count">{conversations.length}</span>
      </div>
      {conversations.length === 0 ? (
        <p className="conversation-list__empty" role="status">
          还没有历史任务
        </p>
      ) : (
        <ul>
          {conversations.map((conversation) => (
            <li key={conversation.conversationId}>
              <a
                aria-current={
                  conversation.conversationId === selectedConversationId
                    ? "page"
                    : undefined
                }
                className="conversation-list__item"
                href={`/conversations/${conversation.conversationId}`}
              >
                <StatusIcon status={conversation.status} />
                <span>
                  <strong>{conversation.title}</strong>
                  <em>{conversation.status}</em>
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "completed") {
    return <CircleCheck aria-hidden="true" size={16} />;
  }
  if (status === "running") {
    return <CircleDashed aria-hidden="true" size={16} />;
  }
  return <Circle aria-hidden="true" size={16} />;
}
