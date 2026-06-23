import { useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator, type Layout } from "react-resizable-panels";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchRequirementDraftItem,
} from "../../lib/api/agentWorkbenchTypes";
import { MessageComposer } from "./MessageComposer";
import { RequirementReviewPanel } from "./RequirementReviewPanel";
import { StrategyGraph } from "./StrategyGraph";
import { ThinkingProcessRail } from "./ThinkingProcessRail";
import { Transcript } from "./Transcript";
import "./ConversationScreen.css";

export type ConversationScreenCallbacks = {
  actionErrorMessage?: string | null | undefined;
  amendingRequirements?: boolean | undefined;
  confirmingRequirements?: boolean | undefined;
  onAddOtherRequirement?: ((text: string) => Promise<void> | void) | undefined;
  onConfirmRequirements?: (() => void) | undefined;
  onSubmitMessage?: ((message: string) => Promise<void> | void) | undefined;
  onToggleRequirementItem?:
    | ((item: AgentWorkbenchRequirementDraftItem, selected: boolean) => void)
    | undefined;
  submittingMessage?: boolean | undefined;
  updatingRequirementItemIds?: readonly string[] | undefined;
};

type ConversationScreenProps = ConversationScreenCallbacks & {
  view: AgentWorkbenchConversationResponse;
};

type ChatGraphLayout = Layout & {
  chat: number;
  graph: number;
};

export function ConversationScreen({
  actionErrorMessage = null,
  amendingRequirements = false,
  confirmingRequirements = false,
  onAddOtherRequirement,
  onConfirmRequirements,
  onSubmitMessage,
  onToggleRequirementItem,
  submittingMessage = false,
  updatingRequirementItemIds = [],
  view,
}: ConversationScreenProps) {
  const compactWorkspace = useCompactWorkspace();
  const workflowSurfaceVisible = hasConversationWorkflowSurface(view);
  const shouldShowRequirementReview =
    view.pendingActions.allowed.includes("confirm_requirements") ||
    view.pendingActions.pendingRequirementReviewCount > 0;
  const requirementReviewPanel = shouldShowRequirementReview ? (
    <RequirementReviewPanel
      amending={amendingRequirements}
      confirming={confirmingRequirements}
      onAddOther={onAddOtherRequirement}
      onConfirm={onConfirmRequirements}
      onToggleItem={onToggleRequirementItem}
      pendingActions={view.pendingActions}
      requirementDraft={view.requirementDraft}
      updatingItemIds={updatingRequirementItemIds}
    />
  ) : null;
  const [savedLayout, setSavedLayout] = useState<ChatGraphLayout | undefined>(
    () => loadSavedChatGraphLayout(),
  );
  const handleLayoutChanged = useMemo(
    () => (layout: Layout) => {
      const chatGraphLayout = normalizeChatGraphLayout(layout);
      if (chatGraphLayout === undefined) return;
      setSavedLayout(chatGraphLayout);
      persistChatGraphLayout(chatGraphLayout);
    },
    [],
  );
  const layoutPersistenceProps = compactWorkspace
    ? {}
    : {
        ...(savedLayout === undefined ? {} : { defaultLayout: savedLayout }),
        onLayoutChanged: handleLayoutChanged,
      };

  return (
    <>
      <div className="conversation-view">
        <ConversationStatusNotice view={view} />
        {actionErrorMessage ? (
          <section
            className="conversation-view__notice"
            data-tone="warning"
            role="alert"
          >
            <strong>操作失败</strong>
            <span>{actionErrorMessage}</span>
          </section>
        ) : null}
        {workflowSurfaceVisible ? (
          <Group
            className="workspace-group"
            id="chat-graph-layout"
            orientation={compactWorkspace ? "vertical" : "horizontal"}
            {...layoutPersistenceProps}
          >
            <Panel
              className="workspace-panel workspace-panel--chat"
              defaultSize={compactWorkspace ? 320 : 386}
              id="chat"
              maxSize={compactWorkspace ? undefined : "50%"}
              minSize={compactWorkspace ? 240 : 280}
            >
              <section
                aria-label="对话"
                className="conversation-view__panel conversation-view__panel--chat"
                data-panel="chat"
                id="conversation-panel-chat"
                role="region"
              >
                <Transcript groups={view.transcriptGroups}>
                  {requirementReviewPanel}
                </Transcript>
                <MessageComposer
                  disabled={
                    !view.pendingActions.allowed.includes("submit_message")
                  }
                  loading={submittingMessage}
                  onSubmit={onSubmitMessage}
                />
              </section>
            </Panel>
            <Separator
              aria-label="调整对话和策略图宽度"
              className="workspace-separator"
            />
            <Panel
              className="workspace-panel workspace-panel--graph"
              id="graph"
              minSize={compactWorkspace ? 320 : 400}
            >
              <section
                aria-label="策略图面板"
                className="conversation-view__panel conversation-view__panel--graph"
                data-panel="graph"
                id="conversation-panel-graph"
                role="region"
              >
                <StrategyGraph
                  graph={view.strategyGraph}
                  jobTitle={view.conversation.title}
                />
              </section>
            </Panel>
          </Group>
        ) : (
          <div
            className="conversation-view__workspace"
            data-workflow-surface="hidden"
          >
            <section
              aria-label="对话"
              className="conversation-view__panel conversation-view__panel--chat"
              data-panel="chat"
              id="conversation-panel-chat"
              role="region"
            >
              <Transcript groups={view.transcriptGroups}>
                {requirementReviewPanel}
              </Transcript>
              <MessageComposer
                disabled={
                  !view.pendingActions.allowed.includes("submit_message")
                }
                loading={submittingMessage}
                onSubmit={onSubmitMessage}
              />
            </section>
          </div>
        )}
      </div>
    </>
  );
}

function useCompactWorkspace(): boolean {
  const [compact, setCompact] = useState(() => compactWorkspaceMatches());

  useEffect(() => {
    const media = compactWorkspaceMedia();
    if (media === null) {
      return;
    }
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return compact;
}

function compactWorkspaceMatches(): boolean {
  return compactWorkspaceMedia()?.matches ?? false;
}

function compactWorkspaceMedia(): MediaQueryList | null {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return null;
  }
  return window.matchMedia("(max-width: 1080px)");
}

export function hasConversationWorkflowSurface(
  view: AgentWorkbenchConversationResponse,
): boolean {
  return (
    view.strategyGraph.nodes.length > 0 ||
    view.strategyGraph.edges.length > 0 ||
    view.thinkingProcess.rounds.length > 0 ||
    view.candidates.length > 0 ||
    view.detailApprovals.length > 0 ||
    view.reviewArtifacts.length > 0 ||
    view.finalSummary != null
  );
}

const CHAT_GRAPH_LAYOUT_STORAGE_KEY = "chat-graph-layout";

function loadSavedChatGraphLayout(): ChatGraphLayout | undefined {
  try {
    if (typeof localStorage === "undefined") return undefined;
    const stored = localStorage.getItem(CHAT_GRAPH_LAYOUT_STORAGE_KEY);
    if (stored === null) return undefined;
    const parsed = JSON.parse(stored) as unknown;
    const layout = normalizeChatGraphLayout(parsed);
    if (layout !== undefined) return layout;
    localStorage.removeItem(CHAT_GRAPH_LAYOUT_STORAGE_KEY);
    return undefined;
  } catch {
    return undefined;
  }
}

function normalizeChatGraphLayout(
  layout: unknown,
): ChatGraphLayout | undefined {
  if (layout === null || typeof layout !== "object") {
    return undefined;
  }
  const values = layout as Record<string, unknown>;
  if (
    !isPositiveFiniteNumber(values.chat) ||
    !isPositiveFiniteNumber(values.graph)
  ) {
    return undefined;
  }
  return { chat: values.chat, graph: values.graph };
}

function isPositiveFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function persistChatGraphLayout(layout: ChatGraphLayout) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(CHAT_GRAPH_LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // storage unavailable
  }
}

export function ConversationScreenSide({
  defaultTab,
  onViewCandidateDetails,
  view,
}: {
  defaultTab?: "candidates" | "thinking";
  onViewCandidateDetails?: ((candidateId: string) => void) | undefined;
  view: AgentWorkbenchConversationResponse;
}) {
  return (
    <ThinkingProcessRail
      candidates={view.candidates}
      onViewCandidateDetails={onViewCandidateDetails}
      thinkingProcess={view.thinkingProcess}
      {...(defaultTab === undefined ? {} : { defaultTab })}
    />
  );
}

function ConversationStatusNotice({
  view,
}: {
  view: AgentWorkbenchConversationResponse;
}) {
  const notice = conversationNotice(view);

  if (notice === null) {
    return null;
  }

  return (
    <section
      aria-label="任务状态提示"
      className="conversation-view__notice"
      data-tone={notice.tone}
    >
      <strong>{notice.title}</strong>
      <span>{notice.text}</span>
    </section>
  );
}

function conversationNotice(view: AgentWorkbenchConversationResponse): {
  text: string;
  title: string;
  tone: "info" | "success" | "warning";
} | null {
  const status = view.conversation.status;
  const primaryAction = view.pendingActions.primary;

  if (
    status === "permission_denied" ||
    view.reasonCode === "permission_denied"
  ) {
    return {
      title: "来源授权需要处理",
      text: primaryAction ?? "来源连接已过期，请重新授权后继续检索。",
      tone: "warning",
    };
  }

  if (status === "disconnected") {
    return {
      title: "流式连接已断开",
      text: "当前显示最近稳定快照，事件流恢复后会继续追加。",
      tone: "warning",
    };
  }

  if (status === "failed") {
    return {
      title: "本轮运行失败",
      text: primaryAction ?? "已保留可投影的安全事实，可查看失败工具并重试。",
      tone: "warning",
    };
  }

  if (status === "completed") {
    return {
      title: "最终名单已生成",
      text: view.finalSummary?.text ?? "候选人 shortlist 已准备好导出。",
      tone: "success",
    };
  }

  if (status === "archived") {
    return {
      title: "任务已归档",
      text: "此任务当前为只读查看。",
      tone: "info",
    };
  }

  return null;
}
