import { Check, Clock3, X } from "lucide-react";
import type { AgentWorkbenchDetailApproval } from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import type { CandidateCardCandidate } from "./CandidateCard";
import "./DetailApprovalQueue.css";

export type DetailApproval = AgentWorkbenchDetailApproval;

type DetailApprovalItemProps = {
  approval: DetailApproval;
  candidate?:
    | Pick<CandidateCardCandidate, "candidateId" | "displayName" | "headline">
    | undefined;
  onApprove?: ((approvalId: string) => void) | undefined;
  onDeny?: ((approvalId: string) => void) | undefined;
};

function approvalStatusLabel(status: DetailApproval["status"]): string {
  const labels: Record<DetailApproval["status"], string> = {
    pending: "待审批",
    accepted: "已接受",
    rejected: "已拒绝",
    applied: "已应用",
  };
  return labels[status];
}

export function DetailApprovalItem({
  approval,
  candidate,
  onApprove,
  onDeny,
}: DetailApprovalItemProps) {
  const candidateName = candidate?.displayName ?? approval.candidateId;
  const title = candidate?.headline ?? "候选人详情";

  return (
    <article
      aria-label={`${candidateName} 详情审批`}
      className="detail-approval-item"
      data-status={approval.status}
    >
      <div className="detail-approval-item__header">
        <div>
          <h3>{candidateName}</h3>
          <p>{title}</p>
        </div>
        <span>{approvalStatusLabel(approval.status)}</span>
      </div>

      <dl className="detail-approval-item__facts">
        <div>
          <dt>审批原因</dt>
          <dd>{approval.reason}</dd>
        </div>
      </dl>

      {approval.status === "pending" ? (
        <div className="detail-approval-item__actions">
          <Button
            icon={<Check aria-hidden="true" size={16} />}
            onClick={() => onApprove?.(approval.approvalId)}
            tone="primary"
          >
            批准读取详情
          </Button>
          <Button
            icon={<X aria-hidden="true" size={16} />}
            onClick={() => onDeny?.(approval.approvalId)}
            tone="secondary"
          >
            拒绝读取详情
          </Button>
        </div>
      ) : (
        <p className="detail-approval-item__resolved">
          <Clock3 aria-hidden="true" size={15} />
          <span>处理状态: {approvalStatusLabel(approval.status)}</span>
        </p>
      )}
    </article>
  );
}
