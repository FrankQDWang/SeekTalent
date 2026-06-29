import { Search } from "lucide-react";
import { CandidateCard, type CandidateCardCandidate } from "./CandidateCard";
import "./CandidateQueue.css";

export type CandidateQueueStatus = "loading" | "ready" | "error";

type CandidateQueueProps = {
  candidates: readonly CandidateCardCandidate[];
  errorMessage?: string;
  selectedCandidateId?: string | null;
  status?: CandidateQueueStatus;
  totalCount?: number;
  onViewDetails?: ((candidateId: string) => void) | undefined;
};

function CandidateQueueSkeleton() {
  return (
    <section
      aria-label="候选人队列"
      className="candidate-queue"
      data-state="loading"
    >
      <div className="candidate-queue__header">
        <span className="candidate-queue__title">候选人</span>
        <span className="candidate-queue__count">读取中</span>
      </div>
      {[0, 1, 2].map((item) => (
        <div
          aria-hidden="true"
          className="candidate-card candidate-card--skeleton"
          key={item}
        >
          <span />
          <span />
          <span />
        </div>
      ))}
    </section>
  );
}

export function CandidateQueue({
  candidates,
  errorMessage = "候选人列表暂时不可用。",
  onViewDetails,
  selectedCandidateId = null,
  status = "ready",
  totalCount = candidates.length,
}: CandidateQueueProps) {
  if (status === "loading") {
    return <CandidateQueueSkeleton />;
  }

  if (status === "error") {
    return (
      <section
        aria-label="候选人队列"
        className="candidate-queue"
        data-state="error"
      >
        <div className="candidate-queue__header">
          <span className="candidate-queue__title">候选人</span>
        </div>
        <p className="candidate-queue__message" role="alert">
          {errorMessage}
        </p>
      </section>
    );
  }

  if (candidates.length === 0) {
    return (
      <section
        aria-label="候选人队列"
        className="candidate-queue"
        data-state="empty"
      >
        <div className="candidate-queue__header">
          <span className="candidate-queue__title">候选人</span>
          <span className="candidate-queue__count">共 0 位</span>
        </div>
        <div className="candidate-queue__empty" role="status">
          <Search aria-hidden="true" size={28} />
          <strong>暂无候选人简历</strong>
          <span>检索到候选人后会显示在这里。</span>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-label="候选人队列"
      className="candidate-queue"
      data-state="populated"
    >
      <div className="candidate-queue__header">
        <span className="candidate-queue__title">候选人</span>
        <span className="candidate-queue__count">共 {totalCount} 位</span>
      </div>
      <div className="candidate-queue__list">
        {candidates.map((candidate) => (
          <CandidateCard
            candidate={candidate}
            key={candidate.candidateId}
            onViewDetails={onViewDetails}
            selected={candidate.candidateId === selectedCandidateId}
          />
        ))}
      </div>
    </section>
  );
}
