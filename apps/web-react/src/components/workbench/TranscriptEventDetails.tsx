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
    <dl className="transcript-event-details">
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function detailRows(event: AgentWorkbenchTranscriptEvent): Array<{
  label: string;
  value: string | number;
}> {
  const rows: Array<{ label: string; value: string | number }> = [];

  if (event.itemId.length > 0) {
    rows.push({ label: "item", value: event.itemId });
  }
  if (event.payload.activityId) {
    rows.push({ label: "activity", value: event.payload.activityId });
  }
  if (event.payload.sourceRuntimeRunId) {
    rows.push({ label: "runtime", value: event.payload.sourceRuntimeRunId });
  }
  if (event.payload.summary) {
    rows.push({ label: "summary", value: event.payload.summary });
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
