import { BrainCircuit, UsersRound } from "lucide-react";
import type { ReactNode } from "react";
import { useId, useState } from "react";
import type {
  AgentWorkbenchCandidateSummary,
  AgentWorkbenchThinkingProcess,
  AgentWorkbenchQueryExecution,
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
          <QueryGroups queryGroups={round.queryGroups} />
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
}: {
  queryGroups: readonly AgentWorkbenchQueryGroup[];
}) {
  return (
    <section aria-label="关键词" className="thinking-query-groups">
      <h3 className="thinking-query-groups__heading">关键词</h3>
      <div className="thinking-query-groups__list">
        {queryGroups.map((queryGroup) => (
          <QueryGroup
            key={queryGroup.queryInstanceId}
            queryGroup={queryGroup}
          />
        ))}
      </div>
    </section>
  );
}

function QueryGroup({ queryGroup }: { queryGroup: AgentWorkbenchQueryGroup }) {
  const laneLabel = queryLaneLabel(queryGroup.laneType);
  const lifecycleLabel = queryLifecycleLabel(queryGroup.lifecycle);

  return (
    <section
      aria-label={`${laneLabel}，${lifecycleLabel}`}
      className="thinking-query-group"
      role="group"
    >
      <div className="thinking-query-group__header">
        <h4>{laneLabel}</h4>
        <span data-lifecycle={queryGroup.lifecycle}>{lifecycleLabel}</span>
      </div>
      {queryGroup.keywordQuery ? (
        <p className="thinking-query-group__keyword">
          {queryGroup.keywordQuery}
        </p>
      ) : null}
      {queryGroup.queryTerms.length > 0 ? (
        <div
          aria-label={`${laneLabel}关键词`}
          className="thinking-query-group__terms"
        >
          {queryGroup.queryTerms.map((term, index) => (
            <span key={`${term}-${String(index)}`}>{term}</span>
          ))}
        </div>
      ) : null}
      <dl
        aria-label={`${laneLabel}汇总`}
        className="thinking-query-group__counts"
      >
        <div>
          <dt>原始</dt>
          <dd>{queryGroup.rawCandidateCount}</dd>
        </div>
        <div>
          <dt>新增</dt>
          <dd>{queryGroup.uniqueCandidateCount}</dd>
        </div>
        <div>
          <dt>重复</dt>
          <dd>{queryGroup.duplicateCandidateCount}</dd>
        </div>
      </dl>
      {queryGroup.executions.length > 0 ? (
        <ul
          aria-label={`${laneLabel}来源执行`}
          className="thinking-query-group__executions"
        >
          {queryGroup.executions.map((execution, index) => (
            <QueryExecution
              execution={execution}
              key={`${execution.sourceKind}-${String(index)}`}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function QueryExecution({
  execution,
}: {
  execution: AgentWorkbenchQueryExecution;
}) {
  return (
    <li>
      <span>{querySourceLabel(execution.sourceKind)}</span>
      <span>{queryExecutionStatusLabel(execution.status)}</span>
      <span>
        原始 {execution.rawCandidateCount}，新增{" "}
        {execution.uniqueCandidateCount}，重复{" "}
        {execution.duplicateCandidateCount}
      </span>
    </li>
  );
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

function queryLaneLabel(laneType: string): string {
  if (laneType === "exploit") {
    return "主检索";
  }
  if (laneType === "generic_explore") {
    return "扩展检索";
  }
  if (laneType === "prf_probe") {
    return "补漏检索";
  }
  return "其他检索";
}

function queryLifecycleLabel(lifecycle: string): string {
  if (lifecycle === "executed") {
    return "已执行";
  }
  if (lifecycle === "planned") {
    return "计划中";
  }
  return "状态待确认";
}

function querySourceLabel(sourceKind: string): string {
  if (sourceKind === "liepin") {
    return "猎聘";
  }
  if (sourceKind === "cts") {
    return "CTS 实验";
  }
  return "其他来源";
}

function queryExecutionStatusLabel(status: string): string {
  if (status === "completed") {
    return "已完成";
  }
  if (status === "partial") {
    return "部分完成";
  }
  if (status === "blocked") {
    return "已阻塞";
  }
  if (status === "failed") {
    return "失败";
  }
  return "状态待确认";
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
