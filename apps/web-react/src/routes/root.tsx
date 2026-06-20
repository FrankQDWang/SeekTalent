import {
  createRootRoute,
  createRoute,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { App } from "../App";
import { ConversationList } from "../components/workbench/ConversationList";
import { ConversationShell } from "../components/workbench/ConversationShell";
import {
  HomeStartPanel,
  type HomeStartPanelSubmitInput,
} from "../components/workbench/HomeStartPanel";
import {
  useAgentWorkbenchConversations,
  useCreateAgentWorkbenchConversationFromJd,
} from "../lib/api/agentWorkbench";
import { safeErrorMessage } from "../lib/api/client";
import { useState } from "react";

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
  const createConversationMutation =
    useCreateAgentWorkbenchConversationFromJd();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const conversations = query.data?.conversations ?? [];
  const onSubmit = async ({
    jobDescription,
    jobTitle,
  }: HomeStartPanelSubmitInput) => {
    setErrorMessage(null);
    try {
      const result = await createConversationMutation.mutateAsync({
        jobDescription,
        jobTitle,
      });
      await navigate({
        params: { conversationId: result.conversationId },
        to: "/conversations/$conversationId",
      });
    } catch (error) {
      setErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };

  return (
    <ConversationShell
      main={
        <HomeStartPanel
          errorMessage={errorMessage}
          loading={createConversationMutation.isPending}
          onSubmit={onSubmit}
        />
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
