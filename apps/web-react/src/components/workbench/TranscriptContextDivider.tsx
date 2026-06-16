import { ScissorsLineDashed } from "lucide-react";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";

type TranscriptContextDividerProps = {
  group: AgentWorkbenchTranscriptGroup;
};

export function TranscriptContextDivider({
  group,
}: TranscriptContextDividerProps) {
  const summary = group.events[0]?.summary ?? group.events[0]?.payload.summary;

  return (
    <div
      aria-label={group.title}
      className="transcript-context-divider"
      role="separator"
    >
      <span aria-hidden="true" />
      <div>
        <ScissorsLineDashed aria-hidden="true" size={16} />
        <strong>{group.title}</strong>
        {summary ? <p>{summary}</p> : null}
      </div>
      <span aria-hidden="true" />
    </div>
  );
}
