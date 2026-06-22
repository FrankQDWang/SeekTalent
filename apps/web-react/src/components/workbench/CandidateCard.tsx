import { Eye } from "lucide-react";
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
  cts: "CTS 实验",
  liepin: "猎聘",
};

function sourceBadgeLabel(
  sourceKinds: readonly CandidateSourceKind[] | null | undefined,
): string {
  const uniqueKinds = [...new Set(sourceKinds ?? [])];
  if (uniqueKinds.includes("liepin")) {
    return sourceLabels.liepin;
  }
  if (uniqueKinds.includes("cts")) {
    return sourceLabels.cts;
  }
  const [sourceKind] = uniqueKinds;
  return sourceKind ? sourceLabels[sourceKind] : "来源待确认";
}

export function CandidateCard({
  candidate,
  onViewDetails,
  selected = false,
}: CandidateCardProps) {
  const facts = [
    candidate.location,
    candidate.education,
    typeof candidate.experienceYears === "number"
      ? `工作${String(candidate.experienceYears)}年`
      : null,
  ].filter((fact): fact is string => Boolean(fact));

  return (
    <article
      aria-label={candidate.displayName}
      className="candidate-card"
      data-rank={candidate.rank}
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
        <span className="candidate-card__source">
          {sourceBadgeLabel(candidate.sourceKinds)}
        </span>
      </div>

      {facts.length > 0 ? (
        <div className="candidate-card__chips" aria-label="候选人基础信息">
          {facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </div>
      ) : null}

      <div className="candidate-card__footer">
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
