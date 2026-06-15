import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2,
  CircleDashed,
  ClipboardCheck,
  Clock3,
  FileCheck2,
  ListChecks,
  MessageSquareText,
  PauseCircle,
  Search,
  ShieldCheck,
  Sparkles,
  Star,
  UsersRound,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { StrategyFlowNode } from "../../lib/strategy-graph/graphProjection";
import "./StrategyGraphNode.css";

const sourceLabels = {
  all: "全来源",
  cts: "CTS",
  liepin: "猎聘",
} as const;

const kindIcons: Record<string, LucideIcon> = {
  activity: Search,
  approval: ShieldCheck,
  candidate: UsersRound,
  final: FileCheck2,
  final_summary: ListChecks,
  message: MessageSquareText,
  requirements: ClipboardCheck,
};

const statusIcons: Record<string, LucideIcon> = {
  blocked: PauseCircle,
  cancelled: PauseCircle,
  completed: CheckCircle2,
  failed: XCircle,
  "not-started": CircleDashed,
  pending: CircleDashed,
  running: Clock3,
  succeeded: CheckCircle2,
  superseded: PauseCircle,
  "waiting-for-user": Star,
  waiting_for_user: Star,
};

const statusLabels: Record<string, string> = {
  blocked: "已阻塞",
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
  "not-started": "未开始",
  pending: "未开始",
  running: "运行中",
  succeeded: "已完成",
  superseded: "已替换",
  "waiting-for-user": "等待确认",
  waiting_for_user: "等待确认",
};

function normalizedStatus(status: string): string {
  return status in statusLabels ? status : "pending";
}

export function StrategyGraphNode({
  data,
  selected,
}: NodeProps<StrategyFlowNode>) {
  const status = normalizedStatus(data.status);
  const StatusIcon = statusIcons[status] ?? Sparkles;
  const KindIcon = kindIcons[data.kind] ?? Sparkles;

  return (
    <article
      className="strategy-graph-node"
      data-selected={selected ? "true" : "false"}
      data-status={status}
      data-testid={`strategy-node-${data.nodeId}`}
    >
      <Handle
        className="strategy-graph-node__handle"
        type="target"
        position={Position.Left}
      />
      <div className="strategy-graph-node__heading">
        <span className="strategy-graph-node__kind" aria-hidden="true">
          <KindIcon size={15} strokeWidth={2.3} />
        </span>
        <strong>{data.label}</strong>
      </div>
      <p>{data.summary}</p>
      <div className="strategy-graph-node__meta">
        <span className="strategy-graph-node__status">
          <StatusIcon size={13} strokeWidth={2.4} aria-hidden="true" />
          {statusLabels[status]}
        </span>
        <span>{sourceLabels[data.sourceKind]}</span>
      </div>
      <Handle
        className="strategy-graph-node__handle"
        type="source"
        position={Position.Right}
      />
    </article>
  );
}
