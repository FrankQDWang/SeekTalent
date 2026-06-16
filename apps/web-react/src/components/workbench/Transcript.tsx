import { useMemo, useState } from "react";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";
import { TranscriptContextDivider } from "./TranscriptContextDivider";
import { TranscriptRunGroup } from "./TranscriptRunGroup";
import "./Transcript.css";

type TranscriptProps = {
  defaultCollapsedGroupIds?: readonly string[];
  groups: readonly AgentWorkbenchTranscriptGroup[];
};

export function Transcript({
  defaultCollapsedGroupIds = [],
  groups,
}: TranscriptProps) {
  const initialCollapsed = useMemo(
    () => new Set(defaultCollapsedGroupIds),
    [defaultCollapsedGroupIds],
  );
  const [collapsedGroupIds, setCollapsedGroupIds] = useState(initialCollapsed);

  function toggleGroup(groupId: string) {
    setCollapsedGroupIds((current) => {
      const next = new Set(current);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }

  if (groups.length === 0) {
    return (
      <section
        aria-label="Agent transcript"
        className="transcript"
        data-state="empty"
      >
        <div className="transcript__empty" role="status">
          对话记录尚未生成
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Agent transcript" className="transcript">
      {groups.map((group) =>
        isContextGroup(group) ? (
          <TranscriptContextDivider group={group} key={group.groupId} />
        ) : (
          <TranscriptRunGroup
            collapsed={collapsedGroupIds.has(group.groupId)}
            group={group}
            key={group.groupId}
            onToggle={() => toggleGroup(group.groupId)}
          />
        ),
      )}
    </section>
  );
}

function isContextGroup(group: AgentWorkbenchTranscriptGroup): boolean {
  return group.events.every((event) => event.kind === "context.compacted");
}
