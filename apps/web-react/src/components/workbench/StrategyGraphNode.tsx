import {
  BriefcaseBusiness,
  CheckCircle2,
  CircleDashed,
  ClipboardCheck,
  Clock3,
  FileCheck2,
  Globe2,
  ListChecks,
  PauseCircle,
  Search,
  Star,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { StrategyTimelineNode } from "../../lib/strategy-graph/graphProjection";
import "./StrategyGraphNode.css";

const kindIcons: Record<string, LucideIcon> = {
  activity: Search,
  approval: FileCheck2,
  candidate: Star,
  final: FileCheck2,
  lane: ListChecks,
  message: BriefcaseBusiness,
  phase: Search,
  requirements: ClipboardCheck,
  round: CircleDashed,
};

const stageIcons: Record<string, LucideIcon> = {
  feedback: ListChecks,
  final: FileCheck2,
  final_summary: FileCheck2,
  merge: ListChecks,
  round_query: Search,
  scoring: Star,
  source_result: Globe2,
};

const statusIcons: Record<string, LucideIcon> = {
  blocked: PauseCircle,
  cancelled: PauseCircle,
  completed: CheckCircle2,
  failed: XCircle,
  partial: PauseCircle,
  pending: CircleDashed,
  running: Clock3,
};

export function StrategyGraphNode({ item }: { item: StrategyTimelineNode }) {
  const node = item.node;
  const Icon = stageIcons[node.stage ?? ""] ?? kindIcons[node.kind] ?? Search;
  const StatusIcon = statusIcons[node.status] ?? CircleDashed;

  return (
    <article
      aria-label={`${item.displayTitle}: ${node.summary}`}
      className="strategy-graph-node"
      data-kind={node.kind}
      data-source={node.sourceKind}
      data-stage={node.stage ?? node.phase ?? ""}
      data-status={node.status}
      data-testid={`strategy-node-${node.nodeId}`}
      style={{
        height: item.height,
        left: item.x,
        top: item.y,
        width: item.width,
      }}
    >
      <div className="strategy-graph-node__heading">
        <span className="strategy-graph-node__kind" aria-hidden="true">
          <Icon size={16} strokeWidth={2.3} />
        </span>
        <strong>{item.displayTitle}</strong>
      </div>
      <p>{node.summary}</p>
      <div className="strategy-graph-node__meta">
        <span className="strategy-graph-node__status">
          <StatusIcon size={13} strokeWidth={2.4} aria-hidden="true" />
          {item.metadata[0]}
        </span>
        {item.metadata.slice(1).map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
    </article>
  );
}
