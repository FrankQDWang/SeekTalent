import { FileCheck2 } from "lucide-react";
import type { CandidateCardCandidate } from "./CandidateCard";
import { DetailApprovalItem, type DetailApproval } from "./DetailApprovalItem";
import "./DetailApprovalQueue.css";

export type DetailApprovalQueueStatus = "loading" | "ready" | "error";

type DetailApprovalQueueProps = {
  approvals: readonly DetailApproval[];
  candidates: readonly CandidateCardCandidate[];
  errorMessage?: string;
  status?: DetailApprovalQueueStatus;
  onApprove?: ((approvalId: string) => void) | undefined;
  onDeny?: ((approvalId: string) => void) | undefined;
};

function DetailApprovalSkeleton() {
  return (
    <section
      aria-label="详情审批队列"
      className="detail-approval-queue"
      data-state="loading"
    >
      <div className="detail-approval-queue__header">
        <span>详情审批</span>
        <em>读取中</em>
      </div>
      <div
        aria-hidden="true"
        className="detail-approval-item detail-approval-item--skeleton"
      >
        <span />
        <span />
        <span />
      </div>
    </section>
  );
}

export function DetailApprovalQueue({
  approvals,
  candidates,
  errorMessage = "详情审批暂时不可用。",
  onApprove,
  onDeny,
  status = "ready",
}: DetailApprovalQueueProps) {
  if (status === "loading") {
    return <DetailApprovalSkeleton />;
  }

  if (status === "error") {
    return (
      <section
        aria-label="详情审批队列"
        className="detail-approval-queue"
        data-state="error"
      >
        <div className="detail-approval-queue__header">
          <span>详情审批</span>
        </div>
        <p className="detail-approval-queue__message" role="alert">
          {errorMessage}
        </p>
      </section>
    );
  }

  if (approvals.length === 0) {
    return (
      <section
        aria-label="详情审批队列"
        className="detail-approval-queue"
        data-state="empty"
      >
        <div className="detail-approval-queue__header">
          <span>详情审批</span>
          <em>0 项</em>
        </div>
        <div className="detail-approval-queue__empty" role="status">
          <FileCheck2 aria-hidden="true" size={28} />
          <strong>暂无详情审批</strong>
          <span>需要敏感或高成本读取时会出现审批项。</span>
        </div>
      </section>
    );
  }

  const candidateById = new Map(
    candidates.map((candidate) => [candidate.candidateId, candidate]),
  );

  return (
    <section
      aria-label="详情审批队列"
      className="detail-approval-queue"
      data-state="populated"
    >
      <div className="detail-approval-queue__header">
        <span>详情审批</span>
        <em>{approvals.length} 项</em>
      </div>
      <div className="detail-approval-queue__list">
        {approvals.map((approval) => (
          <DetailApprovalItem
            approval={approval}
            candidate={candidateById.get(approval.candidateId)}
            key={approval.approvalId}
            onApprove={onApprove}
            onDeny={onDeny}
          />
        ))}
      </div>
    </section>
  );
}
