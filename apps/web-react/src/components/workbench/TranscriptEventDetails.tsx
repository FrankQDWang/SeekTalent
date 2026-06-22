import type { AgentWorkbenchTranscriptEvent } from "../../lib/api/agentWorkbenchTypes";

type TranscriptEventDetailsProps = {
  event: AgentWorkbenchTranscriptEvent;
};

export function TranscriptEventDetails({ event }: TranscriptEventDetailsProps) {
  const rows = detailRows(event);

  if (rows.length === 0) {
    return null;
  }

  return (
    <div className="transcript-event-details">
      {rows.map((row) => (
        <div
          className={
            row.visuallyHidden
              ? "transcript-event-details__sr"
              : "transcript-event-details__line"
          }
          key={`${row.label}:${String(row.value)}`}
        >
          {row.value}
        </div>
      ))}
    </div>
  );
}

function detailRows(event: AgentWorkbenchTranscriptEvent): Array<{
  label: string;
  visuallyHidden?: boolean;
  value: string | number;
}> {
  const rows: Array<{
    label: string;
    visuallyHidden?: boolean;
    value: string | number;
  }> = [];

  if (event.payload.summary) {
    for (const [index, line] of event.payload.summary
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean)
      .entries()) {
      rows.push({ label: `summary:${String(index)}`, value: line });
    }
  }

  if (event.itemId.length > 0) {
    rows.push({ label: "item", value: event.itemId, visuallyHidden: true });
  }
  if (event.payload.activityId) {
    rows.push({
      label: "activity",
      value: event.payload.activityId,
      visuallyHidden: true,
    });
  }
  if (event.payload.sourceRuntimeRunId) {
    rows.push({
      label: "runtime",
      value: event.payload.sourceRuntimeRunId,
      visuallyHidden: true,
    });
  }
  if (
    event.payload.missingFromSeq !== undefined &&
    event.payload.missingFromSeq !== null
  ) {
    rows.push({ label: "missing from", value: event.payload.missingFromSeq });
  }
  if (
    event.payload.nextAvailableSeq !== undefined &&
    event.payload.nextAvailableSeq !== null
  ) {
    rows.push({
      label: "next available",
      value: event.payload.nextAvailableSeq,
    });
  }

  return rows;
}
