import { createRoute } from "@tanstack/react-router";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "../components/workbench/ConversationScreen";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { useAgentWorkbenchLiveConversation } from "../lib/api/agentWorkbench";
import { safeErrorMessage } from "../lib/api/client";
import { rootRoute } from "./root";

export const conversationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/conversations/$conversationId",
  component: ConversationRoute,
});

function ConversationRoute() {
  const { conversationId } = conversationRoute.useParams();
  const query = useAgentWorkbenchLiveConversation(conversationId);

  if (query.isPending) {
    return (
      <ConversationShell
        main={<section aria-busy="true" className="conversation-view__state" />}
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={<div />}
      />
    );
  }

  if (query.isError) {
    return (
      <ConversationShell
        main={
          <section className="conversation-view__state" role="alert">
            {safeErrorMessage(query.error)}
          </section>
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={<div />}
      />
    );
  }

  const view = query.data;
  return (
    <ConversationShell
      main={<ConversationScreen view={view} />}
      rail={<ConversationList selectedConversationId={conversationId} />}
      side={<ConversationScreenSide view={view} />}
    />
  );
}
