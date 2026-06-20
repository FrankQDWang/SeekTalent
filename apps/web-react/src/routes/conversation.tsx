import { createRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "../components/workbench/ConversationScreen";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { CandidateDetailDrawer } from "../components/workbench/CandidateDetailDrawer";
import {
  useConfirmAgentWorkbenchRequirements,
  useAgentWorkbenchCandidateDetail,
  useAgentWorkbenchLiveConversation,
  useSubmitAgentWorkbenchMessage,
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
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(
    null,
  );
  const detailQuery = useAgentWorkbenchCandidateDetail(
    conversationId,
    selectedCandidateId,
  );
  const submitMessageMutation = useSubmitAgentWorkbenchMessage(conversationId);
  const confirmRequirementsMutation =
    useConfirmAgentWorkbenchRequirements(conversationId);
  const selectedCandidate = useMemo(
    () =>
      query.data?.candidates.find(
        (candidate) => candidate.candidateId === selectedCandidateId,
      ) ?? null,
    [selectedCandidateId, query.data?.candidates],
  );

  useEffect(() => {
    setActionErrorMessage(null);
    setSelectedCandidateId(null);
  }, [conversationId]);

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
  const onSubmitMessage = async (message: string) => {
    setActionErrorMessage(null);
    try {
      await submitMessageMutation.mutateAsync(message);
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };
  const onConfirmRequirements = async () => {
    setActionErrorMessage(null);
    const draftRevisionId = view.requirementDraft?.draftRevisionId;
    if (!draftRevisionId) {
      setActionErrorMessage("当前没有可确认的需求草稿。");
      return;
    }
    try {
      await confirmRequirementsMutation.mutateAsync(draftRevisionId);
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
    }
  };
  return (
    <>
      <ConversationShell
        main={
          <ConversationScreen
            actionErrorMessage={actionErrorMessage}
            confirmingRequirements={confirmRequirementsMutation.isPending}
            onConfirmRequirements={() => void onConfirmRequirements()}
            onSubmitMessage={onSubmitMessage}
            onViewCandidateDetails={(candidateId) => {
              setActionErrorMessage(null);
              setSelectedCandidateId(candidateId);
            }}
            submittingMessage={submitMessageMutation.isPending}
            view={view}
          />
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={
          <ConversationScreenSide
            onViewCandidateDetails={(candidateId) => {
              setActionErrorMessage(null);
              setSelectedCandidateId(candidateId);
            }}
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
