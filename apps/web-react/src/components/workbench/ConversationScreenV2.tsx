import type {
  WorkbenchV2ConversationView,
  WorkbenchV2RequirementActionRequest,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import { MessageComposer } from "./MessageComposer";
import { TranscriptV2 } from "./TranscriptV2";
import "./ConversationScreenV2.css";

type ConversationScreenV2Props = {
  actionErrorMessage?: string | null;
  applyingRequirementAction?: boolean;
  onRequirementAction?:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
  onSubmitMessage?: ((message: string) => Promise<void> | void) | undefined;
  submittingMessage?: boolean;
  view: WorkbenchV2ConversationView;
};

export function ConversationScreenV2({
  actionErrorMessage = null,
  applyingRequirementAction = false,
  onRequirementAction,
  onSubmitMessage,
  submittingMessage = false,
  view,
}: ConversationScreenV2Props) {
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
      <section
        aria-label="对话"
        className="conversation-v2-view__panel"
        role="region"
      >
        <TranscriptV2
          events={view.transcriptEvents}
          onRequirementAction={onRequirementAction}
          requirementActionPending={applyingRequirementAction}
        />
        <MessageComposer
          loading={submittingMessage}
          onSubmit={onSubmitMessage}
          placeholder="输入消息、JD 或下一步招聘需求"
        />
      </section>
    </div>
  );
}

export function hasConversationV2RuntimeSurface(
  view: WorkbenchV2ConversationView,
): boolean {
  return view.runtime !== null || view.conversation.runtimeState !== "idle";
}

export function ConversationScreenV2Side({
  view,
}: {
  view: WorkbenchV2ConversationView;
}) {
  const runtimeState = view.runtime?.state ?? view.conversation.runtimeState;
  const runtimeRunId =
    view.runtime?.runtimeRunId ?? view.conversation.runtimeRunId;
  const latestRuntimeEvent = latestRuntimeTranscriptEvent(
    view.transcriptEvents,
  );

  return (
    <aside aria-label="运行状态" className="conversation-v2-side">
      <div className="conversation-v2-side__header">
        <h2>运行状态</h2>
        <span data-state={runtimeState}>{runtimeState}</span>
      </div>
      <dl className="conversation-v2-side__facts">
        <div>
          <dt>运行 ID</dt>
          <dd>{runtimeRunId ?? "尚未启动"}</dd>
        </div>
      </dl>
      {latestRuntimeEvent ? (
        <section className="conversation-v2-side__latest">
          <h3>{runtimeEventTitle(latestRuntimeEvent)}</h3>
          <p>{runtimeEventText(latestRuntimeEvent)}</p>
        </section>
      ) : null}
    </aside>
  );
}

function latestRuntimeTranscriptEvent(
  events: readonly WorkbenchV2TranscriptEvent[],
): WorkbenchV2TranscriptEvent | null {
  const runtimeEvents = events.filter(
    (event) =>
      event.type === "runtime_progress" ||
      event.type === "runtime_result" ||
      event.type === "error",
  );
  if (runtimeEvents.length === 0) {
    return null;
  }
  return (
    [...runtimeEvents].sort((left, right) => right.step - left.step)[0] ?? null
  );
}

function runtimeEventTitle(event: WorkbenchV2TranscriptEvent): string {
  if (event.type === "runtime_result") {
    return "最新结果";
  }
  if (event.type === "error") {
    return "最新错误";
  }
  return "最新进度";
}

function runtimeEventText(event: WorkbenchV2TranscriptEvent): string {
  return (
    readString(event.payload, "summary") ??
    readString(event.payload, "message") ??
    readString(event.payload, "text") ??
    (event.type === "error" ? "请求失败，请稍后重试。" : "运行状态已更新")
  );
}

function readString(
  record: Record<string, unknown>,
  field: string,
): string | null {
  const value = record[field];
  return typeof value === "string" ? value : null;
}
