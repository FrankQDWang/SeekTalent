import { createRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
  hasConversationWorkflowSurface,
} from "../components/workbench/ConversationScreen";
import {
  ConversationScreenV2,
  ConversationScreenV2Side,
  hasWorkbenchV2WorkflowSurface,
} from "../components/workbench/ConversationScreenV2";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { CandidateDetailDrawer } from "../components/workbench/CandidateDetailDrawer";
import {
  HomeStartPanel,
  type HomeStartPanelSubmitInput,
} from "../components/workbench/HomeStartPanel";
import {
  useApplyWorkbenchV2RequirementAction,
  useCreateWorkbenchV2Conversation,
  useSubmitWorkbenchV2Message,
  useWorkbenchV2CandidateDetail,
  useWorkbenchV2Conversation,
  useWorkbenchV2Conversations,
} from "../lib/api/workbenchV2";
import { WorkbenchV2RequestError } from "../lib/api/workbenchV2Client";
import type {
  WorkbenchV2ConversationView,
  WorkbenchV2RequirementActionRequest,
  WorkbenchV2TranscriptEvent,
} from "../lib/api/workbenchV2Types";
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
  component: WorkbenchRoute,
});

function WorkbenchRoute() {
  const { conversationId } = conversationRoute.useParams();

  if (conversationId === "new") {
    return <NewConversationFlow />;
  }

  if (isWorkbenchV2ConversationId(conversationId)) {
    return (
      <ExistingWorkbenchV2ConversationFlow
        key={conversationId}
        conversationId={conversationId}
      />
    );
  }

  return (
    <ExistingLegacyConversationFlow
      key={conversationId}
      conversationId={conversationId}
    />
  );
}

function NewConversationFlow() {
  const navigate = useNavigate({ from: "/conversations/$conversationId" });
  const createConversationMutation = useCreateWorkbenchV2Conversation();
  const [homeErrorMessage, setHomeErrorMessage] = useState<string | null>(null);
  const [recoveredHomeMessage, setRecoveredHomeMessage] = useState("");
  const [pendingInitialTurn, setPendingInitialTurn] = useState<{
    idempotencyKey: string;
    message: string;
  } | null>(null);

  const onHomeSubmit = async (input: HomeStartPanelSubmitInput) => {
    setHomeErrorMessage(null);
    setRecoveredHomeMessage("");
    const idempotencyKey = createIdempotencyKey();
    setPendingInitialTurn({ idempotencyKey, message: input.message });
    try {
      const result = await createConversationMutation.mutateAsync({
        message: input.message,
        idempotencyKey,
      });
      setPendingInitialTurn(null);
      void navigate({
        params: { conversationId: result.conversation.conversationId },
        to: "/conversations/$conversationId",
        replace: true,
      });
    } catch (error) {
      setPendingInitialTurn(null);
      setRecoveredHomeMessage(input.message);
      setHomeErrorMessage(safeWorkbenchV2ErrorMessage(error));
      throw error;
    }
  };

  if (pendingInitialTurn !== null) {
    return (
      <ConversationShell
        main={
          <ConversationScreenV2
            optimisticEvents={optimisticTurnEvents({
              conversationId: "agentv2_pending",
              idempotencyKey: pendingInitialTurn.idempotencyKey,
              message: pendingInitialTurn.message,
              step: 1,
            })}
            submittingMessage
            view={pendingWorkbenchV2View(pendingInitialTurn.message)}
          />
        }
        rail={<WorkbenchV2ConversationRail />}
      />
    );
  }

  return (
    <ConversationShell
      main={
        <HomeStartPanel
          errorMessage={homeErrorMessage}
          initialMessage={recoveredHomeMessage}
          key={recoveredHomeMessage}
          loading={createConversationMutation.isPending}
          onSubmit={onHomeSubmit}
        />
      }
      rail={<WorkbenchV2ConversationRail />}
    />
  );
}

function ExistingWorkbenchV2ConversationFlow({
  conversationId,
}: {
  conversationId: string;
}) {
  const query = useWorkbenchV2Conversation(conversationId);
  const submitMessageMutation = useSubmitWorkbenchV2Message(conversationId);
  const requirementActionMutation =
    useApplyWorkbenchV2RequirementAction(conversationId);
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(
    null,
  );
  const [optimisticEvents, setOptimisticEvents] = useState<
    WorkbenchV2TranscriptEvent[]
  >([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const detailQuery = useWorkbenchV2CandidateDetail(
    conversationId,
    selectedCandidateId,
  );

  useEffect(() => {
    setActionErrorMessage(null);
    setOptimisticEvents([]);
    setSelectedCandidateId(null);
  }, [conversationId]);

  const selectedCandidate = useMemo(
    () =>
      query.data?.candidates?.find(
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

  const onSubmitMessage = async (message: string) => {
    setActionErrorMessage(null);
    const idempotencyKey = createIdempotencyKey();
    const turnEvents = optimisticTurnEvents({
      conversationId,
      idempotencyKey,
      message,
      step: nextOptimisticStep(query.data, optimisticEvents),
    });
    setOptimisticEvents((current) => [...current, ...turnEvents]);
    try {
      await submitMessageMutation.mutateAsync({
        message,
        idempotencyKey,
      });
      setOptimisticEvents((current) =>
        current.filter(
          (event) => !event.eventId.includes(`:${idempotencyKey}:`),
        ),
      );
    } catch (error) {
      setOptimisticEvents((current) =>
        current.filter(
          (event) => !event.eventId.includes(`:${idempotencyKey}:`),
        ),
      );
      setActionErrorMessage(safeWorkbenchV2ErrorMessage(error));
      throw error;
    }
  };

  const onRequirementAction = async (
    payload: WorkbenchV2RequirementActionRequest,
  ) => {
    setActionErrorMessage(null);
    const idempotencyKey = payload.idempotencyKey ?? createIdempotencyKey();
    const requestPayload = { ...payload, idempotencyKey };
    const actionEvents = optimisticRequirementActionEvents({
      conversationId,
      idempotencyKey,
      payload: requestPayload,
      step: nextOptimisticStep(query.data, optimisticEvents),
    });
    if (actionEvents.length > 0) {
      setOptimisticEvents((current) => [...current, ...actionEvents]);
    }
    try {
      await requirementActionMutation.mutateAsync(requestPayload);
      if (actionEvents.length > 0) {
        setOptimisticEvents((current) =>
          current.filter(
            (event) => !event.eventId.includes(`:${idempotencyKey}:`),
          ),
        );
      }
    } catch (error) {
      if (actionEvents.length > 0) {
        setOptimisticEvents((current) =>
          current.filter(
            (event) => !event.eventId.includes(`:${idempotencyKey}:`),
          ),
        );
      }
      setActionErrorMessage(safeWorkbenchV2ErrorMessage(error));
      throw error;
    }
  };

  if (query.isPending) {
    return (
      <ConversationShell
        main={<section aria-busy="true" className="conversation-view__state" />}
        rail={
          <WorkbenchV2ConversationRail
            selectedConversationId={conversationId}
          />
        }
      />
    );
  }

  if (query.isError) {
    return (
      <ConversationShell
        main={
          <section className="conversation-view__state" role="alert">
            {safeWorkbenchV2ErrorMessage(query.error)}
          </section>
        }
        rail={
          <WorkbenchV2ConversationRail
            selectedConversationId={conversationId}
          />
        }
      />
    );
  }

  const view = query.data;
  const workflowSurfaceVisible = hasWorkbenchV2WorkflowSurface(view);

  return (
    <>
      <ConversationShell
        main={
          <ConversationScreenV2
            actionErrorMessage={actionErrorMessage}
            applyingRequirementAction={requirementActionMutation.isPending}
            onRequirementAction={onRequirementAction}
            onSubmitMessage={onSubmitMessage}
            optimisticEvents={optimisticEvents}
            submittingMessage={submitMessageMutation.isPending}
            view={view}
          />
        }
        rail={
          <WorkbenchV2ConversationRail
            selectedConversationId={conversationId}
          />
        }
        side={
          workflowSurfaceVisible ? (
            <ConversationScreenV2Side
              onViewCandidateDetails={viewCandidateDetails}
              selectedCandidateId={selectedCandidateId}
              view={view}
            />
          ) : null
        }
      />
      <CandidateDetailDrawer
        candidate={selectedCandidate}
        detail={detailQuery.data ?? null}
        errorMessage={
          detailQuery.isError
            ? safeWorkbenchV2ErrorMessage(detailQuery.error)
            : undefined
        }
        onClose={closeCandidateDrawer}
        onRetry={retryCandidateDetail}
        open={selectedCandidateId !== null}
        status={
          selectedCandidateId === null
            ? "idle"
            : detailQuery.isError
              ? "error"
              : detailQuery.isPending
                ? "loading"
                : "ready"
        }
      />
    </>
  );
}

function ExistingLegacyConversationFlow({
  conversationId,
}: {
  conversationId: string;
}) {
  const queryClient = useQueryClient();
  const query = useAgentWorkbenchLiveConversation(conversationId);
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );
  const requirementMutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const requirementMutationErrorRef = useRef<unknown>(null);
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
    requirementMutationErrorRef.current = null;
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
      .then(async () => {
        requirementMutationErrorRef.current = null;
        await run();
      });
    requirementMutationChainRef.current = next.catch((error: unknown) => {
      requirementMutationErrorRef.current = error;
    });
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
    if (requirementMutationErrorRef.current !== null) {
      setActionErrorMessage(
        safeErrorMessage(requirementMutationErrorRef.current),
      );
      return;
    }
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
          operations: [{ itemId: item.itemId, op: "set_selected", selected }],
        });
      });
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
      throw error;
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
        await amendRequirementMutation.mutateAsync({ draftRevisionId, text });
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
            onToggleRequirementItem={onToggleRequirementItem}
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

function WorkbenchV2ConversationRail({
  selectedConversationId,
}: {
  selectedConversationId?: string;
}) {
  const conversationsQuery = useWorkbenchV2Conversations();
  return (
    <ConversationList
      conversations={conversationsQuery.data?.conversations ?? []}
      selectedConversationId={selectedConversationId}
    />
  );
}

function isWorkbenchV2ConversationId(conversationId: string): boolean {
  return conversationId.startsWith("agentv2_");
}

function createIdempotencyKey(): string {
  const globalWithOptionalCrypto = globalThis as {
    crypto?: { randomUUID?: () => string };
  };
  if (typeof globalWithOptionalCrypto.crypto?.randomUUID === "function") {
    return globalWithOptionalCrypto.crypto.randomUUID();
  }
  return `idempotency-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

function pendingWorkbenchV2View(message: string): WorkbenchV2ConversationView {
  const now = new Date().toISOString();
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: {
      conversationId: "agentv2_pending",
      title: conversationTitleFromMessage(message),
      runtimeState: "idle",
      runtimeRunId: null,
      createdAt: now,
      updatedAt: now,
    },
    transcriptEvents: [],
    requirementForm: null,
    runtime: null,
  };
}

function optimisticTurnEvents({
  conversationId,
  idempotencyKey,
  message,
  step,
}: {
  conversationId: string;
  idempotencyKey: string;
  message: string;
  step: number;
}): WorkbenchV2TranscriptEvent[] {
  const now = new Date().toISOString();
  return [
    {
      eventId: `optimistic:${conversationId}:${idempotencyKey}:user`,
      step,
      type: "user_message",
      role: "user",
      status: "pending",
      payload: { text: message },
      createdAt: now,
    },
    {
      eventId: `optimistic:${conversationId}:${idempotencyKey}:status`,
      step: step + 1,
      type: "assistant_status",
      role: "assistant",
      status: "running",
      payload: { summary: "正在思考" },
      createdAt: now,
    },
  ];
}

export function optimisticRequirementActionEvents({
  conversationId,
  idempotencyKey,
  payload,
  step,
}: {
  conversationId: string;
  idempotencyKey: string;
  payload: WorkbenchV2RequirementActionRequest;
  step: number;
}): WorkbenchV2TranscriptEvent[] {
  if (payload.action !== "confirm" && payload.action !== "add_other") {
    return [];
  }
  const text = typeof payload.text === "string" ? payload.text.trim() : "";
  if (text.length === 0) {
    return [];
  }
  const now = new Date().toISOString();
  return [
    {
      eventId: `optimistic:${conversationId}:${idempotencyKey}:requirement-supplement-user`,
      step,
      type: "user_message",
      role: "user",
      status: "pending",
      payload: { text },
      createdAt: now,
    },
    {
      eventId: `optimistic:${conversationId}:${idempotencyKey}:requirement-supplement-status`,
      step: step + 1,
      type: "assistant_status",
      role: "assistant",
      status: "running",
      payload: {
        phase: "requirement_amendment",
        text: "正在根据补充要求更新需求，请稍候。",
      },
      createdAt: now,
    },
  ];
}

function nextOptimisticStep(
  view: WorkbenchV2ConversationView | undefined,
  optimisticEvents: readonly WorkbenchV2TranscriptEvent[],
): number {
  const persistedStep =
    view?.transcriptEvents.reduce(
      (maxStep, event) => Math.max(maxStep, event.step),
      0,
    ) ?? 0;
  const optimisticStep = optimisticEvents.reduce(
    (maxStep, event) => Math.max(maxStep, event.step),
    0,
  );
  return Math.max(persistedStep, optimisticStep) + 1;
}

function conversationTitleFromMessage(message: string): string {
  const normalized = message.replace(/\s+/g, " ").trim();
  if (normalized.length <= 24) {
    return normalized || "新对话";
  }
  return `${normalized.slice(0, 24)}...`;
}

function safeWorkbenchV2ErrorMessage(error: unknown): string {
  if (error instanceof WorkbenchV2RequestError) {
    if (error.status > 0) {
      return `请求失败，状态码 ${String(error.status)}`;
    }
    return "网络请求失败，请稍后重试。";
  }
  return safeErrorMessage(error);
}
