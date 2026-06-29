import { Eye } from "lucide-react";
import type { AgentWorkbenchCandidateSummary } from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import { candidateSourceLabel } from "./candidateSource";
import "./CandidateQueue.css";

export type CandidateCardCandidate = AgentWorkbenchCandidateSummary;

type CandidateCardProps = {
  candidate: CandidateCardCandidate;
  selected?: boolean;
  onViewDetails?: ((candidateId: string) => void) | undefined;
};

export function CandidateCard({
  candidate,
  onViewDetails,
  selected = false,
}: CandidateCardProps) {
  const facts = [
    typeof candidate.age === "number" ? `${String(candidate.age)}岁` : null,
    candidate.city ?? candidate.location,
    candidate.education,
    typeof (candidate.workYears ?? candidate.experienceYears) === "number"
      ? `工作${String(candidate.workYears ?? candidate.experienceYears)}年`
      : null,
  ].filter((fact): fact is string => Boolean(fact));
  const headline = candidateHeadline(candidate);
  const score =
    typeof candidate.matchScore === "number"
      ? `${String(candidate.matchScore)}分`
      : null;
  const status = candidateStatusLabel(candidate.status);
  const sourceLabel =
    candidate.sourceLabel?.trim() ||
    candidateSourceLabel(candidate.sourceKinds);
  const avatarLabel =
    candidate.avatarLabel?.trim() || candidate.displayName.slice(0, 1);

  return (
    <article
      aria-label={candidate.displayName}
      className="candidate-card"
      data-avatar-color={candidate.avatarColorKey ?? "default"}
      data-rank={candidate.rank}
      data-source={candidate.sourceKinds?.join(" ") ?? "unknown"}
      data-selected={selected ? "true" : "false"}
    >
      <div className="candidate-card__header">
        <div className="candidate-card__identity">
          <span className="candidate-card__avatar" aria-hidden="true">
            {avatarLabel}
          </span>
          <div className="candidate-card__name-block">
            <h3>{candidate.displayName}</h3>
            <p>{headline}</p>
          </div>
        </div>
        <div className="candidate-card__badges">
          {status ? <span data-status={candidate.status}>{status}</span> : null}
          <span>{sourceLabel}</span>
        </div>
      </div>

      {facts.length > 0 ? (
        <div className="candidate-card__chips" aria-label="候选人基础信息">
          {facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </div>
      ) : null}

      {candidate.matchSummary ? (
        <p className="candidate-card__summary">{candidate.matchSummary}</p>
      ) : null}

      <div className="candidate-card__footer">
        {score ? <span className="candidate-card__score">{score}</span> : null}
        <Button
          className="candidate-card__detail-button"
          icon={<Eye aria-hidden="true" size={16} />}
          onClick={() => onViewDetails?.(candidate.candidateId)}
          tone="primary"
        >
          查看详情
        </Button>
      </div>
    </article>
  );
}

function candidateHeadline(candidate: CandidateCardCandidate): string {
  const currentTitle = candidate.currentTitle?.trim();
  const currentCompany = candidate.currentCompany?.trim();
  if (currentTitle && currentCompany) {
    return `${currentTitle} · ${currentCompany}`;
  }
  if (currentTitle || currentCompany) {
    return currentTitle || currentCompany || "";
  }
  const headline = candidate.headline?.trim();
  const company = candidate.company?.trim();
  if (headline && company && !headline.includes(company)) {
    return `${headline} · ${company}`;
  }
  return headline || company || "候选人详情待补充";
}

function candidateStatusLabel(
  status: CandidateCardCandidate["status"],
): string | null {
  if (status === "fit") {
    return "推荐";
  }
  if (status === "reviewing" || status === "maybe") {
    return "待复核";
  }
  if (status === "not_fit") {
    return "暂不推荐";
  }
  return status ? "已评分" : null;
}
