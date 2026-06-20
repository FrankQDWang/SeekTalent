import { Eye, ShieldCheck } from "lucide-react";
import type { AgentWorkbenchCandidateSummary } from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import "./CandidateQueue.css";

export type CandidateCardCandidate = AgentWorkbenchCandidateSummary;
type CandidateSourceKind = NonNullable<
  CandidateCardCandidate["sourceKinds"]
>[number];

type CandidateCardProps = {
  candidate: CandidateCardCandidate;
  selected?: boolean;
  onViewDetails?: ((candidateId: string) => void) | undefined;
};

const sourceLabels: Record<CandidateSourceKind, string> = {
  cts: "本地",
  liepin: "猎聘",
};

function sourceBadgeLabel(
  sourceKinds: readonly CandidateSourceKind[] | null | undefined,
): string {
  const uniqueKinds = [...new Set(sourceKinds ?? [])];
  if (uniqueKinds.length > 1) {
    return "多来源";
  }
  const [sourceKind] = uniqueKinds;
  return sourceKind ? sourceLabels[sourceKind] : "来源待确认";
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

function detailAvailabilityLabel(
  detailAvailability: CandidateCardCandidate["detailAvailability"],
): string {
  if (detailAvailability === "available") {
    return "详情可读";
  }
  if (detailAvailability === "approval_required") {
    return "需审批";
  }
  if (detailAvailability === "redacted") {
    return "已脱敏";
  }
  return "详情不可用";
}

function matchScoreLabel(matchScore: number | null | undefined): string | null {
  if (typeof matchScore !== "number" || !Number.isFinite(matchScore)) {
    return null;
  }
  const normalized = matchScore <= 1 ? matchScore * 100 : matchScore;
  return `${String(Math.round(normalized))}%`;
}

export function CandidateCard({
  candidate,
  onViewDetails,
  selected = false,
}: CandidateCardProps) {
  const scoreLabel = matchScoreLabel(candidate.matchScore);
  const facts = [
    candidate.company,
    candidate.location,
    candidate.education,
    typeof candidate.experienceYears === "number"
      ? `${String(candidate.experienceYears)} 年经验`
      : null,
  ].filter((fact): fact is string => Boolean(fact));

  return (
    <article
      aria-label={candidate.displayName}
      className="candidate-card"
      data-source={candidate.sourceKinds?.join(" ") ?? "unknown"}
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
        <span>#{candidate.rank}</span>
        {scoreLabel ? (
          <span className="candidate-card__score">
            匹配 <strong>{scoreLabel}</strong>
          </span>
        ) : null}
        <span>{sourceBadgeLabel(candidate.sourceKinds)}</span>
        <span>{detailAvailabilityLabel(candidate.detailAvailability)}</span>
      </div>

      {facts.length > 0 ? (
        <div className="candidate-card__chips" aria-label="候选人基础信息">
          {facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </div>
      ) : null}

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
