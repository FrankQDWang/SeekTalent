import type {
  WorkbenchV2ConversationView,
  WorkbenchV2RequirementActionRequest,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import { MessageComposer } from "./MessageComposer";
import { StrategyGraph } from "./StrategyGraph";
import { ThinkingProcessRail } from "./ThinkingProcessRail";
import { TranscriptV2 } from "./TranscriptV2";
import "./ConversationScreenV2.css";

type ConversationScreenV2Props = {
  actionErrorMessage?: string | null;
  applyingRequirementAction?: boolean;
  onRequirementAction?:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
  onSubmitMessage?: ((message: string) => Promise<void> | void) | undefined;
  optimisticEvents?: readonly WorkbenchV2TranscriptEvent[] | undefined;
  submittingMessage?: boolean;
  view: WorkbenchV2ConversationView;
};

const EMPTY_STRATEGY_GRAPH = {
  nodes: [],
  edges: [],
} satisfies NonNullable<WorkbenchV2ConversationView["strategyGraph"]>;

export function ConversationScreenV2({
  actionErrorMessage = null,
  applyingRequirementAction = false,
  onRequirementAction,
  onSubmitMessage,
  optimisticEvents = [],
  submittingMessage = false,
  view,
}: ConversationScreenV2Props) {
  const transcriptEvents = mergeTranscriptEvents(
    view.transcriptEvents,
    optimisticEvents,
  );
  const workflowSurfaceVisible = hasWorkbenchV2WorkflowSurface(view);
  const workflowJobTitle = workbenchV2WorkflowJobTitle(view);

  return (
    <div className="conversation-v2-view">
      {actionErrorMessage ? (
        <section
          className="conversation-v2-view__notice"
          data-tone="warning"
          role="alert"
        >
          <strong>操作失败</strong>
          <span>{actionErrorMessage}</span>
        </section>
      ) : null}
      <div
        className="conversation-v2-view__workspace"
        data-workflow-surface={workflowSurfaceVisible ? "visible" : "hidden"}
      >
        <section
          aria-label="对话"
          className="conversation-v2-view__panel conversation-v2-view__panel--chat"
          role="region"
        >
          <TranscriptV2
            events={transcriptEvents}
            onRequirementAction={onRequirementAction}
            requirementActionPending={applyingRequirementAction}
          />
          <MessageComposer
            disabled={onSubmitMessage === undefined || submittingMessage}
            loading={false}
            onSubmit={onSubmitMessage}
            placeholder="输入消息、JD 或下一步招聘需求"
          />
        </section>
        {workflowSurfaceVisible ? (
          <section
            aria-label="策略图面板"
            className="conversation-v2-view__panel conversation-v2-view__panel--graph"
            role="region"
          >
            <StrategyGraph
              graph={view.strategyGraph ?? EMPTY_STRATEGY_GRAPH}
              jobTitle={workflowJobTitle}
            />
          </section>
        ) : null}
      </div>
    </div>
  );
}

export function ConversationScreenV2Side({
  onViewCandidateDetails,
  selectedCandidateId = null,
  view,
}: {
  onViewCandidateDetails?: ((candidateId: string) => void) | undefined;
  selectedCandidateId?: string | null | undefined;
  view: WorkbenchV2ConversationView;
}) {
  if (!hasWorkbenchV2WorkflowSurface(view)) {
    return null;
  }
  return (
    <ThinkingProcessRail
      candidates={view.candidates ?? []}
      defaultTab={
        (view.candidates?.length ?? 0) > 0 ? "candidates" : "thinking"
      }
      onViewCandidateDetails={onViewCandidateDetails}
      selectedCandidateId={selectedCandidateId}
      thinkingProcess={
        view.thinkingProcess ?? { activeRoundNo: null, rounds: [] }
      }
    />
  );
}

function mergeTranscriptEvents(
  persistedEvents: readonly WorkbenchV2TranscriptEvent[],
  optimisticEvents: readonly WorkbenchV2TranscriptEvent[],
): WorkbenchV2TranscriptEvent[] {
  if (optimisticEvents.length === 0) {
    return [...persistedEvents];
  }
  const persistedIds = new Set(persistedEvents.map((event) => event.eventId));
  return [
    ...persistedEvents,
    ...optimisticEvents.filter((event) => !persistedIds.has(event.eventId)),
  ];
}

export function hasWorkbenchV2WorkflowSurface(
  view: WorkbenchV2ConversationView,
): boolean {
  return (
    view.transcriptEvents.some(
      (event) => event.type === "requirement_form_confirmed",
    ) ||
    view.conversation.runtimeState !== "idle" ||
    (view.strategyGraph?.nodes.length ?? 0) > 0 ||
    (view.strategyGraph?.edges.length ?? 0) > 0 ||
    (view.thinkingProcess?.rounds.length ?? 0) > 0 ||
    (view.candidates?.length ?? 0) > 0
  );
}

function workbenchV2WorkflowJobTitle(
  view: WorkbenchV2ConversationView,
): string {
  const latestRequirementTitle = [
    view.requirementForm,
    ...[...view.transcriptEvents]
      .reverse()
      .filter(
        (event) =>
          event.type === "requirement_form_confirmed" ||
          event.type === "requirement_form",
      )
      .map((event) => event.payload),
  ]
    .map(runtimeInputJobTitle)
    .find((title) => title !== null);

  return latestRequirementTitle ?? view.conversation.title;
}

function runtimeInputJobTitle(payload: unknown): string | null {
  if (!isRecord(payload)) {
    return null;
  }
  const runtimeInput = payload.runtimeInput;
  if (isRecord(runtimeInput)) {
    return readNonEmptyString(runtimeInput.jobTitle);
  }
  return readNonEmptyString(payload.jobTitle);
}

function readNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
