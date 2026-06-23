import { useMemo, useRef, useState, useEffect } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
} from "lucide-react";
import { prepare, layout } from "@chenglou/pretext";
import type {
  AgentWorkbenchTranscriptEvent,
  AgentWorkbenchTranscriptGroup,
} from "../../lib/api/agentWorkbenchTypes";
import { TranscriptOperationEvent } from "./TranscriptOperationEvent";

const FONT_STRING = "14px system-ui, -apple-system, sans-serif";
const MIN_PRETEXT_CHARS = 200;

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
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  if (isOperationLikeEvent(event)) {
    return <TranscriptOperationEvent event={event} />;
  }

  const summaryText = event.summary ?? event.payload.summary ?? null;
  const shouldMeasure =
    summaryText !== null && summaryText.length > MIN_PRETEXT_CHARS;

  const prepared = useMemo(
    () => (shouldMeasure ? prepare(summaryText, FONT_STRING) : null),
    [summaryText, shouldMeasure],
  );

  const measuredHeight = useMemo(() => {
    if (prepared === null || containerWidth === null) return undefined;
    const { height } = layout(prepared, containerWidth, 20);
    return height;
  }, [prepared, containerWidth]);

  useEffect(() => {
    if (!shouldMeasure) return;
    const element = bodyRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [shouldMeasure]);

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
      <div className="transcript-event__body" ref={bodyRef}>
        <div className="transcript-event__header">
          <h3>{event.label}</h3>
          <time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time>
        </div>
        {summaryText !== null ? (
          <p
            style={
              measuredHeight !== undefined
                ? { minHeight: measuredHeight }
                : undefined
            }
          >
            {summaryText}
          </p>
        ) : null}
      </div>
    </article>
  );
}

function isOperationLikeEvent(event: AgentWorkbenchTranscriptEvent): boolean {
  return (
    event.payload.kind === "operation" ||
    event.payload.kind === "command" ||
    event.payload.kind === "source_search" ||
    event.kind.startsWith("operation.") ||
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
