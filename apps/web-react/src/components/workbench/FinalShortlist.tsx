import { Download, ListChecks } from "lucide-react";
import type { AgentWorkbenchFinalSummary } from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import "./FinalShortlist.css";

export type FinalShortlistSummary = AgentWorkbenchFinalSummary;

type FinalShortlistProps = {
  errorMessage?: string;
  loading?: boolean;
  status?: "ready" | "error";
  summary: FinalShortlistSummary | null;
  onExport?: () => void;
};

function FinalShortlistSkeleton() {
  return (
    <section
      aria-label="最终候选名单"
      className="final-shortlist"
      data-state="loading"
    >
      <div className="final-shortlist__header">
        <span>最终名单</span>
        <em>生成中</em>
      </div>
      <div aria-hidden="true" className="final-shortlist__skeleton">
        <span />
        <span />
        <span />
      </div>
    </section>
  );
}

export function FinalShortlist({
  errorMessage = "最终名单生成失败。",
  loading = false,
  onExport,
  status = "ready",
  summary,
}: FinalShortlistProps) {
  if (loading) {
    return <FinalShortlistSkeleton />;
  }

  if (status === "error") {
    return (
      <section
        aria-label="最终候选名单"
        className="final-shortlist"
        data-state="error"
      >
        <div className="final-shortlist__header">
          <span>最终名单</span>
        </div>
        <p className="final-shortlist__message" role="alert">
          {errorMessage}
        </p>
      </section>
    );
  }

  if (summary === null || summary.text.length === 0) {
    return (
      <section
        aria-label="最终候选名单"
        className="final-shortlist"
        data-state="empty"
      >
        <div className="final-shortlist__header">
          <span>最终名单</span>
          <em>未生成</em>
        </div>
        <div className="final-shortlist__empty" role="status">
          <ListChecks aria-hidden="true" size={28} />
          <strong>最终名单尚未生成</strong>
          <span>完成候选人复核后会生成可导出的安全摘要。</span>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-label="最终候选名单"
      className="final-shortlist"
      data-state="ready"
    >
      <div className="final-shortlist__header">
        <span>最终名单</span>
        <em>{summary.summaryId}</em>
      </div>
      <div className="final-shortlist__intro">
        <h2>最终安全摘要</h2>
        <p>{summary.text}</p>
      </div>
      <div className="final-shortlist__footer">
        <span>安全摘要可导出</span>
        <Button
          icon={<Download aria-hidden="true" size={16} />}
          onClick={onExport}
          tone="primary"
        >
          导出名单
        </Button>
      </div>
    </section>
  );
}
