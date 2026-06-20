import { createRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "../components/workbench/ConversationScreen";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { CandidateDetailDrawer } from "../components/workbench/CandidateDetailDrawer";
import {
  useAgentWorkbenchCandidateDetail,
  useAgentWorkbenchLiveConversation,
} from "../lib/api/agentWorkbench";
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
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const detailQuery = useAgentWorkbenchCandidateDetail(
    conversationId,
    selectedCandidateId,
  );
  const selectedCandidate = useMemo(
    () =>
      query.data?.candidates.find(
        (candidate) => candidate.candidateId === selectedCandidateId,
      ) ?? null,
    [selectedCandidateId, query.data?.candidates],
  );

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
    <>
      <ConversationShell
        main={
          <ConversationScreen
            onViewCandidateDetails={setSelectedCandidateId}
            view={view}
          />
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={
          <ConversationScreenSide
            onViewCandidateDetails={setSelectedCandidateId}
            view={view}
          />
        }
      />
      <CandidateDetailDrawer
        candidate={selectedCandidate}
        detail={detailQuery.data ?? null}
        errorMessage={
          detailQuery.isError ? safeErrorMessage(detailQuery.error) : undefined
        }
        onClose={() => setSelectedCandidateId(null)}
        onRetry={() => void detailQuery.refetch()}
        open={selectedCandidateId !== null}
        status={
          selectedCandidateId === null
            ? "idle"
            : detailQuery.isPending
              ? "loading"
              : detailQuery.isError
                ? "error"
                : "ready"
        }
      />
    </>
  );
}
