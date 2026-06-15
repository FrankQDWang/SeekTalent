import { Eye, ShieldCheck } from "lucide-react";
import type { AgentWorkbenchCandidateSummary } from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import "./CandidateQueue.css";

export type CandidateCardCandidate = AgentWorkbenchCandidateSummary;

type CandidateCardProps = {
  candidate: CandidateCardCandidate;
  selected?: boolean;
  onViewDetails?: ((candidateId: string) => void) | undefined;
};

function sourceBadgeLabel(
  sourceKind: CandidateCardCandidate["sourceKind"],
): string {
  if (sourceKind === "liepin") {
    return "猎聘";
  }
  if (sourceKind === "cts") {
    return "本地";
  }
  return "多来源";
}

function statusLabel(status: string): string {
  if (status === "new") {
    return "新候选";
  }
  if (status === "reviewing" || status === "pending") {
    return "待复核";
  }
  if (status === "shortlisted" || status === "accepted") {
    return "已入围";
  }
  if (status === "rejected") {
    return "已排除";
  }
  return status;
}

export function CandidateCard({
  candidate,
  onViewDetails,
  selected = false,
}: CandidateCardProps) {
  return (
    <article
      aria-label={candidate.displayName}
      className="candidate-card"
      data-source={candidate.sourceKind}
      data-selected={selected ? "true" : "false"}
    >
      <div className="candidate-card__header">
        <div className="candidate-card__identity">
          <span className="candidate-card__avatar" aria-hidden="true">
            {candidate.displayName.slice(0, 1)}
          </span>
          <div className="candidate-card__name-block">
            <h3>{candidate.displayName}</h3>
            <p>{candidate.headline ?? "候选人安全摘要"}</p>
          </div>
        </div>
        <span className="candidate-card__status">
          {statusLabel(candidate.status)}
        </span>
      </div>

      <div className="candidate-card__meta" aria-label="候选人摘要指标">
        <span>{sourceBadgeLabel(candidate.sourceKind)}</span>
        <span>{candidate.status}</span>
      </div>

      <p className="candidate-card__summary">
        {candidate.matchSummary ?? "BFF 尚未返回匹配摘要。"}
      </p>

      {candidate.matchSummary ? (
        <div className="candidate-card__section" aria-label="安全证据">
          <span className="candidate-card__section-label">安全证据</span>
          <ul className="candidate-card__evidence">
            <li>
              <ShieldCheck aria-hidden="true" size={15} />
              <span>
                <strong>安全摘要</strong>
                <span>{candidate.matchSummary}</span>
              </span>
            </li>
          </ul>
        </div>
      ) : null}

      <div className="candidate-card__footer">
        <Button
          icon={<Eye aria-hidden="true" size={16} />}
          onClick={() => onViewDetails?.(candidate.candidateId)}
          tone="secondary"
        >
          查看详情
        </Button>
      </div>
    </article>
  );
}
