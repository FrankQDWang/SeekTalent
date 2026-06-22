import { useEffect, useState } from "react";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchRequirementDraftItem,
} from "../../lib/api/agentWorkbenchTypes";
import { Tabs } from "../primitives/Tabs";
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
  onViewCandidateDetails?: ((candidateId: string) => void) | undefined;
  submittingMessage?: boolean | undefined;
  updatingRequirementItemIds?: readonly string[] | undefined;
};

type ConversationScreenProps = ConversationScreenCallbacks & {
  view: AgentWorkbenchConversationResponse;
};

type WorkPanel = "chat" | "graph" | "candidates" | "final";

export function ConversationScreen({
  actionErrorMessage = null,
  amendingRequirements = false,
  confirmingRequirements = false,
  onAddOtherRequirement,
  onConfirmRequirements,
  onSubmitMessage,
  onToggleRequirementItem,
  onViewCandidateDetails,
  submittingMessage = false,
  updatingRequirementItemIds = [],
  view,
}: ConversationScreenProps) {
  const [activePanel, setActivePanel] = useState<WorkPanel>("chat");
  const compactWorkspace = useCompactWorkspace();
  const workflowSurfaceVisible = hasConversationWorkflowSurface(view);
  const shouldMountGraph = !compactWorkspace || activePanel === "graph";
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
          <Tabs
            ariaLabel="工作区"
            className="conversation-view__tabs"
            getPanelId={(panel) => `conversation-panel-${panel}`}
            idPrefix="conversation"
            onValueChange={setActivePanel}
            tabClassName="conversation-view__tab"
            tabs={workPanels.map((panel) => ({
              label: panel.label,
              value: panel.id,
            }))}
            value={activePanel}
          />
        ) : null}
        <div
          className="conversation-view__workspace"
          data-active-panel={activePanel}
          data-workflow-surface={workflowSurfaceVisible ? "visible" : "hidden"}
        >
          <section
            aria-labelledby="conversation-chat-tab"
            className="conversation-view__panel conversation-view__panel--chat"
            data-panel="chat"
            id="conversation-panel-chat"
            role="tabpanel"
          >
            <Transcript groups={view.transcriptGroups}>
              {requirementReviewPanel}
            </Transcript>
            <MessageComposer
              disabled={!view.pendingActions.allowed.includes("submit_message")}
              loading={submittingMessage}
              onSubmit={onSubmitMessage}
            />
          </section>
          {workflowSurfaceVisible ? (
            <>
              <section
                aria-labelledby="conversation-graph-tab"
                className="conversation-view__panel conversation-view__panel--graph"
                data-panel="graph"
                id="conversation-panel-graph"
                role="tabpanel"
              >
                {shouldMountGraph ? (
                  <StrategyGraph
                    graph={view.strategyGraph}
                    jobTitle={view.conversation.title}
                    key={
                      activePanel === "graph"
                        ? "graph-active"
                        : "graph-inactive"
                    }
                  />
                ) : null}
              </section>
              <section
                aria-labelledby="conversation-candidates-tab"
                className="conversation-view__panel conversation-view__panel--candidates"
                data-panel="candidates"
                id="conversation-panel-candidates"
                role="tabpanel"
              >
                <ConversationScreenSide
                  onViewCandidateDetails={onViewCandidateDetails}
                  view={view}
                />
              </section>
              <section
                aria-labelledby="conversation-final-tab"
                className="conversation-view__panel conversation-view__panel--final"
                data-panel="final"
                id="conversation-panel-final"
                role="tabpanel"
              >
                <FinalReviewPanel view={view} />
              </section>
            </>
          ) : null}
        </div>
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

const workPanels = [
  { id: "chat", label: "Chat" },
  { id: "graph", label: "Graph" },
  { id: "candidates", label: "Candidates" },
  { id: "final", label: "Final" },
] as const satisfies Array<{ id: WorkPanel; label: string }>;

export function ConversationScreenSide({
  defaultTab,
  onViewCandidateDetails,
  view,
}: Pick<ConversationScreenProps, "onViewCandidateDetails" | "view"> & {
  defaultTab?: "candidates" | "thinking";
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

function FinalReviewPanel({
  view,
}: {
  view: AgentWorkbenchConversationResponse;
}) {
  return (
    <section aria-label="最终名单" className="conversation-view__final-panel">
      <div className="conversation-view__final-summary">
        <strong>{view.finalSummary?.summaryId ?? "Final shortlist"}</strong>
        <p>
          {view.finalSummary?.text ??
            "最终名单会在候选人证据和审批完成后生成。"}
        </p>
      </div>
      <div className="conversation-view__artifacts" aria-label="审查产物">
        {view.reviewArtifacts.length === 0 ? (
          <span>暂无审查产物</span>
        ) : (
          view.reviewArtifacts.map((artifact) => (
            <article
              className="conversation-view__artifact"
              key={artifact.artifactId}
            >
              <strong>{artifact.title}</strong>
              <p>{artifact.safeSummary}</p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
