import { BrainCircuit, UsersRound } from "lucide-react";
import type { ReactNode } from "react";
import { useId, useState } from "react";
import type {
  AgentWorkbenchCandidateSummary,
  AgentWorkbenchThinkingProcess,
  AgentWorkbenchQueryGroup,
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
  selectedCandidateId?: string | null | undefined;
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
  selectedCandidateId = null,
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
            selectedCandidateId={selectedCandidateId}
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
        <h2>第 {round.roundNo} 轮</h2>
        <em>
          {round.status === "running" ? "运行中" : statusLabel(round.status)}
        </em>
      </div>
      <div className="thinking-round__cards">
        {round.queryGroups.length > 0 ? (
          <QueryGroups
            queryGroups={round.queryGroups}
            roundNo={round.roundNo}
          />
        ) : null}
        {round.cards.filter(isNarrativeCard).map((card) => (
          <section className="thinking-card" key={card.title}>
            <h3>{thinkingCardTitle(card.title)}</h3>
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

function QueryGroups({
  queryGroups,
  roundNo,
}: {
  queryGroups: readonly AgentWorkbenchQueryGroup[];
  roundNo: number;
}) {
  const paths = selectQueryPaths(queryGroups, roundNo);
  return (
    <div aria-label="检索路径" className="thinking-query-paths" role="group">
      {paths.map(({ label, queryGroup }) => (
        <div aria-label={label} key={label} role="group">
          <strong>{label}</strong>
          <span>{deduplicateTerms(queryGroup.queryTerms).join("、")}</span>
        </div>
      ))}
    </div>
  );
}

function selectQueryPaths(
  queryGroups: readonly AgentWorkbenchQueryGroup[],
  roundNo: number,
) {
  const main = queryGroups.find(({ laneType }) => laneType === "exploit");
  const expansion =
    roundNo === 1
      ? undefined
      : queryGroups.find(({ laneType }) =>
          ["generic_explore", "prf_probe"].includes(laneType),
        );
  return [
    ...(main ? [{ label: "主路径", queryGroup: main }] : []),
    ...(expansion ? [{ label: "扩展路径", queryGroup: expansion }] : []),
  ];
}

function deduplicateTerms(terms: readonly string[]): string[] {
  const seen = new Set<string>();
  return terms.flatMap((term) => {
    const display = term.trim().replace(/\s+/g, " ");
    const key = display.toLocaleLowerCase();
    if (!display || seen.has(key)) return [];
    seen.add(key);
    return [display];
  });
}

function isNarrativeCard({ title }: { title: string }): boolean {
  const normalized = title.trim().toLowerCase();
  return !["keyword", "keywords", "search_keywords", "关键词"].includes(
    normalized,
  );
}

function thinkingCardTitle(title: string): ReactNode {
  const normalized = title.trim().toLowerCase();
  if (
    normalized === "observation" ||
    normalized === "observations" ||
    normalized === "result" ||
    normalized === "results"
  ) {
    return (
      <>
        <span>observation</span>
        <span>（结果）</span>
      </>
    );
  }
  if (
    normalized === "reflection" ||
    normalized === "reflections" ||
    normalized === "next_round_changes" ||
    normalized === "反思和下一轮变更"
  ) {
    return "反思和下一轮变更";
  }
  return "补充信息";
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
