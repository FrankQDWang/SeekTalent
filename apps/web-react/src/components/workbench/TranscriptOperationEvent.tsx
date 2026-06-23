import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  SquareTerminal,
} from "lucide-react";
import { useState } from "react";
import type { AgentWorkbenchTranscriptEvent } from "../../lib/api/agentWorkbenchTypes";
import { TranscriptEventDetails } from "./TranscriptEventDetails";

type TranscriptOperationEventProps = {
  event: AgentWorkbenchTranscriptEvent;
};

export function TranscriptOperationEvent({
  event,
}: TranscriptOperationEventProps) {
  const [expanded, setExpanded] = useState(false);
  const status = event.status ?? "pending";

  return (
    <article
      aria-label={event.label}
      className="transcript-operation-event"
      data-kind={event.payload.kind}
      data-status={status}
    >
      <div className="transcript-operation-event__icon">
        {status === "running" || status === "pending" ? (
          <LoaderCircle aria-hidden="true" size={15} />
        ) : status === "failed" || status === "cancelled" ? (
          <CircleAlert aria-hidden="true" size={15} />
        ) : (
          <SquareTerminal aria-hidden="true" size={15} />
        )}
      </div>
      <div className="transcript-operation-event__body">
        <button
          aria-expanded={expanded}
          className="transcript-operation-event__button"
          onClick={() => setExpanded((value) => !value)}
          type="button"
        >
          <span>{event.label}</span>
          {expanded ? (
            <ChevronDown aria-hidden="true" size={16} />
          ) : (
            <ChevronRight aria-hidden="true" size={16} />
          )}
        </button>
        {(event.summary ?? event.payload.summary) ? (
          <p>{event.summary ?? event.payload.summary}</p>
        ) : null}
        <button
          className="transcript-operation-event__detail-toggle"
          onClick={() => setExpanded((value) => !value)}
          type="button"
        >
          {expanded ? `收起${event.label}详情` : `展开${event.label}详情`}
        </button>
        {expanded ? <TranscriptEventDetails event={event} /> : null}
      </div>
    </article>
  );
}
