import { useEffect } from "react";
import {
  createRootRoute,
  createRoute,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { App } from "../App";
import { ConversationList } from "../components/workbench/ConversationList";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { useAgentWorkbenchConversations } from "../lib/api/agentWorkbench";

export const rootRoute = createRootRoute({
  component: () => (
    <App>
      <Outlet />
    </App>
  ),
});

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: WorkbenchIndexRoute,
});

function WorkbenchIndexRoute() {
  const navigate = useNavigate({ from: "/" });
  const query = useAgentWorkbenchConversations();
  const firstConversation = query.data?.conversations[0];

  useEffect(() => {
    if (firstConversation !== undefined) {
      void navigate({
        params: { conversationId: firstConversation.conversationId },
        replace: true,
        to: "/conversations/$conversationId",
      });
    }
  }, [firstConversation, navigate]);

  const conversations = query.data?.conversations ?? [];
  const stateText = query.isPending
    ? "读取任务"
    : query.isError
      ? "无法加载任务"
      : "暂无任务";

  return (
    <ConversationShell
      main={
        <section aria-label="任务状态" className="workbench-index-state">
          <h2>{stateText}</h2>
        </section>
      }
      rail={
        query.isSuccess ? (
          <ConversationList conversations={conversations} />
        ) : (
          <ConversationList />
        )
      }
    />
  );
}
