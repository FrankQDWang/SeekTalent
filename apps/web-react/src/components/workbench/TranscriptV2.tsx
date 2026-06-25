import type {
  WorkbenchV2RequirementActionRequest,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import { RequirementFormEvent } from "./RequirementFormEvent";
import "./TranscriptV2.css";

type TranscriptV2Props = {
  events: readonly WorkbenchV2TranscriptEvent[];
  requirementActionPending?: boolean;
  onRequirementAction?:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
};

export function TranscriptV2({
  events,
  requirementActionPending = false,
  onRequirementAction,
}: TranscriptV2Props) {
  const orderedEvents = [...events].sort(
    (left, right) => left.step - right.step,
  );

  if (orderedEvents.length === 0) {
    return (
      <section
        aria-label="Agent transcript"
        className="transcript-v2"
        data-state="empty"
      >
        <div className="transcript-v2__empty" role="status">
          对话记录尚未生成
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Agent transcript" className="transcript-v2">
      {orderedEvents.map((event) => (
        <TranscriptV2Event
          event={event}
          key={event.eventId}
          onRequirementAction={onRequirementAction}
          requirementActionPending={requirementActionPending}
        />
      ))}
    </section>
  );
}

function TranscriptV2Event({
  event,
  onRequirementAction,
  requirementActionPending,
}: {
  event: WorkbenchV2TranscriptEvent;
  requirementActionPending: boolean;
  onRequirementAction:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
}) {
  if (
    event.type === "requirement_form" ||
    event.type === "requirement_form_confirmed"
  ) {
    return (
      <RequirementFormEvent
        actionPending={requirementActionPending}
        event={event}
        onAction={onRequirementAction}
      />
    );
  }

  const content = eventContent(event);
  if (content === null) {
    return null;
  }

  if (event.type === "user_message" || event.type === "assistant_message") {
    return (
      <article
        aria-label={event.type === "user_message" ? "用户消息" : "助手消息"}
        className="transcript-v2__turn"
        data-role={event.type === "user_message" ? "user" : "assistant"}
      >
        <div className="transcript-v2__speaker">
          {event.type === "user_message" ? "你" : "助手"}
        </div>
        <p>{content}</p>
      </article>
    );
  }

  return (
    <article
      aria-label={eventLabel(event)}
      className="transcript-v2__event"
      data-status={event.status}
      data-type={event.type}
    >
      <span>{eventLabel(event)}</span>
      <p>{content}</p>
    </article>
  );
}

function eventContent(event: WorkbenchV2TranscriptEvent): string | null {
  const text =
    readString(event.payload, "text") ??
    readString(event.payload, "message") ??
    readString(event.payload, "summary") ??
    readString(event.payload, "title");

  if (event.type === "assistant_status") {
    return dedupeStatusText(text ?? "正在处理");
  }

  if (text !== null && text.trim().length > 0) {
    return text;
  }

  if (event.type === "runtime_progress") {
    return "运行中";
  }
  if (event.type === "runtime_result") {
    return "运行结果已更新";
  }
  if (event.type === "context_summary") {
    return "上下文已更新";
  }
  if (event.type === "error") {
    return "请求失败，请稍后重试。";
  }
  return null;
}

function eventLabel(event: WorkbenchV2TranscriptEvent): string {
  if (event.type === "assistant_status") {
    return "状态";
  }
  if (event.type === "runtime_progress") {
    return "运行进度";
  }
  if (event.type === "runtime_result") {
    return "运行结果";
  }
  if (event.type === "context_summary") {
    return "上下文";
  }
  if (event.type === "error") {
    return "错误";
  }
  return "事件";
}

function dedupeStatusText(text: string): string {
  const seen = new Set<string>();
  const lines = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .filter((line) => {
      if (seen.has(line)) {
        return false;
      }
      seen.add(line);
      return true;
    });
  return lines.length > 0 ? lines.join("\n") : "正在处理";
}

function readString(
  record: Record<string, unknown>,
  field: string,
): string | null {
  const value = record[field];
  return typeof value === "string" ? value : null;
}
