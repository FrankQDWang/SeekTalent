import { BrainCircuit, UsersRound } from "lucide-react";
import { useId, useState } from "react";
import type {
  AgentWorkbenchCandidateSummary,
  AgentWorkbenchThinkingProcess,
  AgentWorkbenchThinkingProcessRound,
} from "../../lib/api/agentWorkbenchTypes";
import { Tabs } from "../primitives/Tabs";
import { CandidateQueue } from "./CandidateQueue";
import "./ThinkingProcessRail.css";

type ThinkingProcessRailTab = "candidates" | "thinking";

type ThinkingProcessRailProps = {
  candidates: readonly AgentWorkbenchCandidateSummary[];
  defaultTab?: ThinkingProcessRailTab;
  onViewCandidateDetails?: ((candidateId: string) => void) | undefined;
  thinkingProcess: AgentWorkbenchThinkingProcess;
};

const tabLabels: Record<ThinkingProcessRailTab, string> = {
  candidates: "候选人",
  thinking: "思考过程",
};

export function ThinkingProcessRail({
  candidates,
  defaultTab = "thinking",
  onViewCandidateDetails,
  thinkingProcess,
}: ThinkingProcessRailProps) {
  const [activeTab, setActiveTab] =
    useState<ThinkingProcessRailTab>(defaultTab);
  const railId = useId();

  return (
    <aside aria-label="运行右栏" className="thinking-process-rail">
      <Tabs
        ariaLabel="右栏视图"
        className="thinking-process-rail__tabs"
        getPanelId={(tab) => `${railId}-${tab}`}
        idPrefix={railId}
        onValueChange={setActiveTab}
        tabClassName="thinking-process-rail__tab"
        tabs={[
          {
            icon: <UsersRound aria-hidden="true" size={15} />,
            label: tabLabels.candidates,
            value: "candidates",
          },
          {
            icon: <BrainCircuit aria-hidden="true" size={15} />,
            label: tabLabels.thinking,
            value: "thinking",
          },
        ]}
        value={activeTab}
      />

      {activeTab === "candidates" ? (
        <section
          aria-label="候选人"
          aria-labelledby={`${railId}-candidates-tab`}
          className="thinking-process-rail__panel"
          id={`${railId}-candidates`}
          role="tabpanel"
        >
          <CandidateQueue
            candidates={candidates}
            onViewDetails={onViewCandidateDetails}
            totalCount={candidates.length}
          />
        </section>
      ) : (
        <section
          aria-label="思考过程"
          aria-labelledby={`${railId}-thinking-tab`}
          className="thinking-process-rail__panel"
          id={`${railId}-thinking`}
          role="tabpanel"
        >
          <ThinkingTimeline thinkingProcess={thinkingProcess} />
        </section>
      )}
    </aside>
  );
}

function ThinkingTimeline({
  thinkingProcess,
}: {
  thinkingProcess: AgentWorkbenchThinkingProcess;
}) {
  if (thinkingProcess.rounds.length === 0) {
    return (
      <div className="thinking-timeline__empty" role="status">
        思考过程尚未生成
      </div>
    );
  }

  return (
    <div className="thinking-timeline">
      {thinkingProcess.rounds.map((round) => (
        <ThinkingRound
          active={round.roundNo === thinkingProcess.activeRoundNo}
          key={round.roundNo}
          round={round}
        />
      ))}
    </div>
  );
}

function ThinkingRound({
  active,
  round,
}: {
  active: boolean;
  round: AgentWorkbenchThinkingProcessRound;
}) {
  return (
    <article className="thinking-round" data-active={active ? "true" : "false"}>
      <span className="thinking-round__dot" aria-hidden="true" />
      <div className="thinking-round__header">
        <span>第 {round.roundNo} 轮</span>
        <em>
          {round.status === "running" ? "运行中" : statusLabel(round.status)}
        </em>
      </div>
      <div className="thinking-round__cards">
        {round.cards.map((card) => (
          <section className="thinking-card" key={card.title}>
            <h3>{card.title}</h3>
            <p>{card.text}</p>
            {card.terms.length > 0 ? (
              <div className="thinking-card__terms">
                {card.terms.map((term) => (
                  <span key={term}>{term}</span>
                ))}
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </article>
  );
}

function statusLabel(status: string): string {
  if (status === "completed") {
    return "已完成";
  }
  if (status === "blocked") {
    return "已阻塞";
  }
  if (status === "partial") {
    return "部分完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "cancelled") {
    return "已取消";
  }
  return "待处理";
}
