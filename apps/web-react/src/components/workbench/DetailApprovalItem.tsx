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

function approvalStatusLabel(status: string): string {
  if (status === "not_required") {
    return "无需审批";
  }
  if (status === "pending") {
    return "待审批";
  }
  if (status === "approved") {
    return "已批准";
  }
  if (status === "denied") {
    return "已拒绝";
  }
  if (status === "failed") {
    return "读取失败";
  }
  return status;
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
