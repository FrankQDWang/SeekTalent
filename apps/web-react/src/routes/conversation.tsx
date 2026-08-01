import { createRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConversationList } from "../components/workbench/ConversationList";
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
  useRecheckWorkbenchV2Runtime,
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
  clearPendingInitialTurn,
  readPendingInitialTurn,
  writePendingInitialTurn,
  type PendingInitialTurn,
} from "../lib/pendingInitialTurn";
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

  return (
    <ExistingWorkbenchV2ConversationFlow
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
  const [pendingInitialTurn, setPendingInitialTurn] =
    useState<PendingInitialTurn | null>(readPendingInitialTurn);
  const recoveryStarted = useRef(false);

  const runPendingInitialTurn = useCallback(
    async (pending: PendingInitialTurn) => {
      setPendingInitialTurn(pending);
      try {
        const result = await createConversationMutation.mutateAsync({
          message: pending.message,
          idempotencyKey: pending.idempotencyKey,
        });
        clearPendingInitialTurn(pending.idempotencyKey);
        setPendingInitialTurn(null);
        void navigate({
          params: { conversationId: result.conversation.conversationId },
          to: "/conversations/$conversationId",
          replace: true,
        });
      } catch (error) {
        setPendingInitialTurn(null);
        setRecoveredHomeMessage(pending.message);
        setHomeErrorMessage(safeWorkbenchV2ErrorMessage(error));
        throw error;
      }
    },
    [createConversationMutation, navigate],
  );

  useEffect(() => {
    if (pendingInitialTurn === null || recoveryStarted.current) {
      return;
    }
    recoveryStarted.current = true;
    void runPendingInitialTurn(pendingInitialTurn).catch(() => undefined);
  }, [pendingInitialTurn, runPendingInitialTurn]);

  const onHomeSubmit = async (input: HomeStartPanelSubmitInput) => {
    setHomeErrorMessage(null);
    setRecoveredHomeMessage("");
    const stored = readPendingInitialTurn();
    const pending =
      stored?.message === input.message
        ? stored
        : {
            idempotencyKey: createIdempotencyKey(),
            message: input.message,
            startedAt: new Date().toISOString(),
          };
    if (!writePendingInitialTurn(pending)) {
      setRecoveredHomeMessage(input.message);
      setHomeErrorMessage(
        "浏览器无法保存当前任务，请允许本地存储后再开始寻才。",
      );
      throw new Error("workbench_v2_pending_turn_storage_unavailable");
    }
    recoveryStarted.current = true;
    await runPendingInitialTurn(pending);
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
              statusSummary:
                "正在解析招聘需求，可安全刷新或关闭页面，任务不会重复创建。",
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
  const runtimeRecoveryMutation = useRecheckWorkbenchV2Runtime(conversationId);
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

  const onRuntimeRecovery = async () => {
    setActionErrorMessage(null);
    try {
      await runtimeRecoveryMutation.mutateAsync({
        idempotencyKey: createIdempotencyKey(),
      });
    } catch (error) {
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
            onRuntimeRecovery={onRuntimeRecovery}
            onSubmitMessage={onSubmitMessage}
            optimisticEvents={optimisticEvents}
            runtimeRecoveryPending={runtimeRecoveryMutation.isPending}
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
  statusSummary = "正在思考",
  step,
}: {
  conversationId: string;
  idempotencyKey: string;
  message: string;
  statusSummary?: string;
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
      payload: { summary: statusSummary },
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
    if (error.reasonCode === "liepin_browser_lane_reconciliation_required") {
      return "上一轮猎聘浏览器操作正在等待对账；请等待对账或联系支持，不要重试。";
    }
    if (error.status > 0) {
      return `请求失败，状态码 ${String(error.status)}`;
    }
    return "网络请求失败，请稍后重试。";
  }
  return "请求失败，请稍后重试。";
}
