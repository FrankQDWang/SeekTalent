import { useLayoutEffect, useMemo, useRef } from "react";
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
  const transcriptRef = useRef<HTMLElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const orderedEvents = useMemo(
    () => [...events].sort((left, right) => left.step - right.step),
    [events],
  );
  const latestRequirementFormEventId =
    latestRequirementFormEvent(orderedEvents)?.eventId ?? null;
  const latestEventId = orderedEvents.at(-1)?.eventId ?? null;

  useLayoutEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript === null || !shouldStickToBottomRef.current) {
      return;
    }
    transcript.scrollTop = transcript.scrollHeight;
  }, [latestEventId, orderedEvents.length]);

  function handleScroll() {
    const transcript = transcriptRef.current;
    if (transcript === null) {
      return;
    }
    const distanceFromBottom =
      transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
    shouldStickToBottomRef.current = distanceFromBottom <= 96;
  }

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
    <section
      aria-label="Agent transcript"
      className="transcript-v2"
      onScroll={handleScroll}
      ref={transcriptRef}
    >
      {orderedEvents.map((event) => (
        <TranscriptV2Event
          event={event}
          latestRequirementFormEventId={latestRequirementFormEventId}
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
  latestRequirementFormEventId,
  onRequirementAction,
  requirementActionPending,
}: {
  event: WorkbenchV2TranscriptEvent;
  latestRequirementFormEventId: string | null;
  requirementActionPending: boolean;
  onRequirementAction:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
}) {
  if (
    event.type === "requirement_form" ||
    event.type === "requirement_form_confirmed"
  ) {
    if (event.eventId !== latestRequirementFormEventId) {
      return null;
    }
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
    const longUserMessage =
      event.type === "user_message" && isLongUserMessage(content);
    return (
      <article
        aria-label={event.type === "user_message" ? "用户消息" : "助手消息"}
        className="transcript-v2__turn"
        data-length={longUserMessage ? "long" : "normal"}
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
    <TranscriptV2ActivityEvent
      content={content}
      event={event}
      label={eventLabel(event)}
    />
  );
}

function TranscriptV2ActivityEvent({
  content,
  event,
  label,
}: {
  content: string;
  event: WorkbenchV2TranscriptEvent;
  label: string;
}) {
  return (
    <article
      aria-label={label}
      className="transcript-v2__event"
      data-status={event.status}
      data-type={event.type}
    >
      <span aria-hidden="true" className="transcript-v2__event-marker" />
      <div className="transcript-v2__event-body">
        <span>{label}</span>
        <p>{content}</p>
      </div>
    </article>
  );
}

function latestRequirementFormEvent(
  orderedEvents: readonly WorkbenchV2TranscriptEvent[],
): WorkbenchV2TranscriptEvent | null {
  for (let index = orderedEvents.length - 1; index >= 0; index -= 1) {
    const event = orderedEvents[index];
    if (
      event?.type === "requirement_form" ||
      event?.type === "requirement_form_confirmed"
    ) {
      return event;
    }
  }
  return null;
}

function eventContent(event: WorkbenchV2TranscriptEvent): string | null {
  if (event.type === "runtime_result" && !hasDisplayableRuntimeResult(event)) {
    return null;
  }

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

function isLongUserMessage(text: string): boolean {
  return text.length > 220 || text.includes("\n");
}

function hasDisplayableRuntimeResult(
  event: WorkbenchV2TranscriptEvent,
): boolean {
  const state = readString(event.payload, "state");
  const summary = readString(event.payload, "summary");
  const facts = event.payload["facts"];
  if (Array.isArray(facts) && facts.length > 0) {
    return true;
  }
  if (state === "completed") {
    return true;
  }
  return (
    typeof summary === "string" &&
    summary.trim().length > 0 &&
    summary.trim() !== "当前还没有运行结果。"
  );
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
