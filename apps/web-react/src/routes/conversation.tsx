import { createRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
  hasConversationWorkflowSurface,
} from "../components/workbench/ConversationScreen";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { CandidateDetailDrawer } from "../components/workbench/CandidateDetailDrawer";
import {
  useAmendAgentWorkbenchRequirementFromText,
  useConfirmAgentWorkbenchRequirements,
  useAgentWorkbenchCandidateDetail,
  useAgentWorkbenchLiveConversation,
  useSubmitAgentWorkbenchMessage,
  useUpdateAgentWorkbenchRequirementDraft,
} from "../lib/api/agentWorkbench";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchRequirementDraftItem,
} from "../lib/api/agentWorkbenchTypes";
import { safeErrorMessage } from "../lib/api/client";
import { queryKeys } from "../lib/query/keys";
import { rootRoute } from "./root";

export const conversationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/conversations/$conversationId",
  component: ConversationRoute,
});

function ConversationRoute() {
  const { conversationId } = conversationRoute.useParams();
  const queryClient = useQueryClient();
  const query = useAgentWorkbenchLiveConversation(conversationId);
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );
  const requirementMutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(
    null,
  );
  const [updatingRequirementItemIds, setUpdatingRequirementItemIds] = useState<
    string[]
  >([]);
  const detailQuery = useAgentWorkbenchCandidateDetail(
    conversationId,
    selectedCandidateId,
  );
  const submitMessageMutation = useSubmitAgentWorkbenchMessage(conversationId);
  const confirmRequirementsMutation =
    useConfirmAgentWorkbenchRequirements(conversationId);
  const updateRequirementMutation =
    useUpdateAgentWorkbenchRequirementDraft(conversationId);
  const amendRequirementMutation =
    useAmendAgentWorkbenchRequirementFromText(conversationId);
  const selectedCandidate = useMemo(
    () =>
      query.data?.candidates.find(
        (candidate) => candidate.candidateId === selectedCandidateId,
      ) ?? null,
    [selectedCandidateId, query.data?.candidates],
  );
  const closeCandidateDrawer = useCallback(() => {
    setSelectedCandidateId(null);
  }, []);
  const retryCandidateDetail = useCallback(() => {
    void detailQuery.refetch();
  }, [detailQuery]);
  const viewCandidateDetails = useCallback((candidateId: string) => {
    setActionErrorMessage(null);
    setSelectedCandidateId(candidateId);
  }, []);

  useEffect(() => {
    requirementMutationChainRef.current = Promise.resolve();
    setActionErrorMessage(null);
    setSelectedCandidateId(null);
    setUpdatingRequirementItemIds([]);
  }, [conversationId]);

  if (query.isPending) {
    return (
      <ConversationShell
        main={<section aria-busy="true" className="conversation-view__state" />}
        rail={<ConversationList selectedConversationId={conversationId} />}
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
      />
    );
  }

  const view = query.data;
  const workflowSurfaceVisible = hasConversationWorkflowSurface(view);
  const latestRequirementDraftRevisionId = () =>
    queryClient.getQueryData<AgentWorkbenchConversationResponse>(queryKey)
      ?.requirementDraft?.draftRevisionId ??
    view.requirementDraft?.draftRevisionId;
  const enqueueRequirementMutation = (run: () => Promise<void>) => {
    const next = requirementMutationChainRef.current
      .catch(() => undefined)
      .then(run);
    requirementMutationChainRef.current = next.catch(() => undefined);
    return next;
  };
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
    await requirementMutationChainRef.current.catch(() => undefined);
    const draftRevisionId = latestRequirementDraftRevisionId();
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
  const onToggleRequirementItem = async (
    item: AgentWorkbenchRequirementDraftItem,
    selected: boolean,
  ) => {
    setActionErrorMessage(null);
    setUpdatingRequirementItemIds((current) =>
      current.includes(item.itemId) ? current : [...current, item.itemId],
    );
    try {
      await enqueueRequirementMutation(async () => {
        const draftRevisionId = latestRequirementDraftRevisionId();
        if (!draftRevisionId) {
          throw new Error("Requirement draft is unavailable.");
        }
        await updateRequirementMutation.mutateAsync({
          draftRevisionId,
          operations: [
            {
              itemId: item.itemId,
              op: "set_selected",
              selected,
            },
          ],
        });
      });
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
    } finally {
      setUpdatingRequirementItemIds((current) =>
        current.filter((itemId) => itemId !== item.itemId),
      );
    }
  };
  const onAddOtherRequirement = async (text: string) => {
    setActionErrorMessage(null);
    try {
      await enqueueRequirementMutation(async () => {
        const draftRevisionId = latestRequirementDraftRevisionId();
        if (!draftRevisionId) {
          throw new Error("Requirement draft is unavailable.");
        }
        await amendRequirementMutation.mutateAsync({
          draftRevisionId,
          text,
        });
      });
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };
  return (
    <>
      <ConversationShell
        main={
          <ConversationScreen
            actionErrorMessage={actionErrorMessage}
            amendingRequirements={amendRequirementMutation.isPending}
            confirmingRequirements={confirmRequirementsMutation.isPending}
            onAddOtherRequirement={onAddOtherRequirement}
            onConfirmRequirements={() => void onConfirmRequirements()}
            onSubmitMessage={onSubmitMessage}
            onToggleRequirementItem={(item, selected) => {
              void onToggleRequirementItem(item, selected);
            }}
            onViewCandidateDetails={viewCandidateDetails}
            submittingMessage={submitMessageMutation.isPending}
            updatingRequirementItemIds={updatingRequirementItemIds}
            view={view}
          />
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={
          workflowSurfaceVisible ? (
            <ConversationScreenSide
              onViewCandidateDetails={viewCandidateDetails}
              view={view}
            />
          ) : null
        }
      />
      <CandidateDetailDrawer
        candidate={selectedCandidate}
        detail={detailQuery.data ?? null}
        errorMessage={
          detailQuery.isError ? safeErrorMessage(detailQuery.error) : undefined
        }
        onClose={closeCandidateDrawer}
        onRetry={retryCandidateDetail}
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
