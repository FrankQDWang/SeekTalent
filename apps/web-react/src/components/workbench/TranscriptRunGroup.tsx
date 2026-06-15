import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
} from "lucide-react";
import type {
  AgentWorkbenchTranscriptEvent,
  AgentWorkbenchTranscriptGroup,
} from "../../lib/api/agentWorkbenchTypes";
import { TranscriptToolEvent } from "./TranscriptToolEvent";

type TranscriptRunGroupProps = {
  collapsed: boolean;
  group: AgentWorkbenchTranscriptGroup;
  onToggle: () => void;
};

export function TranscriptRunGroup({
  collapsed,
  group,
  onToggle,
}: TranscriptRunGroupProps) {
  return (
    <section className="transcript-run-group" data-status={group.status}>
      <button
        aria-expanded={!collapsed}
        className="transcript-run-group__toggle"
        onClick={onToggle}
        type="button"
      >
        {collapsed ? (
          <ChevronRight aria-hidden="true" size={16} />
        ) : (
          <ChevronDown aria-hidden="true" size={16} />
        )}
        <span>{group.title}</span>
        <StatusGlyph status={group.status} />
      </button>

      {!collapsed ? (
        <div className="transcript-run-group__events">
          {group.events.map((event) => (
            <TranscriptEvent event={event} key={event.eventId} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function TranscriptEvent({ event }: { event: AgentWorkbenchTranscriptEvent }) {
  if (isToolLikeEvent(event)) {
    return <TranscriptToolEvent event={event} />;
  }

  return (
    <article
      aria-label={event.label}
      className="transcript-event"
      data-kind={event.payload.kind}
      data-status={event.status ?? "pending"}
    >
      <div className="transcript-event__marker">
        <StatusGlyph status={event.status ?? "pending"} />
      </div>
      <div className="transcript-event__body">
        <div className="transcript-event__header">
          <h3>{event.label}</h3>
          <time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time>
        </div>
        {(event.summary ?? event.payload.summary) ? (
          <p>{event.summary ?? event.payload.summary}</p>
        ) : null}
      </div>
    </article>
  );
}

function isToolLikeEvent(event: AgentWorkbenchTranscriptEvent): boolean {
  return (
    event.payload.kind === "tool" ||
    event.payload.kind === "command" ||
    event.payload.kind === "source_search" ||
    event.kind.startsWith("tool.") ||
    event.kind.startsWith("sourceSearch.") ||
    event.kind.startsWith("webSearch.") ||
    event.kind.startsWith("command.")
  );
}

function StatusGlyph({ status }: { status: string }) {
  if (status === "running" || status === "pending") {
    return (
      <LoaderCircle
        aria-hidden="true"
        className="transcript-status-icon"
        size={15}
      />
    );
  }
  if (status === "failed" || status === "cancelled") {
    return (
      <CircleAlert
        aria-hidden="true"
        className="transcript-status-icon"
        size={15}
      />
    );
  }
  return (
    <CircleCheck
      aria-hidden="true"
      className="transcript-status-icon"
      size={15}
    />
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
