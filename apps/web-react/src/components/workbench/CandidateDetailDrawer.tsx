import {
  AlertTriangle,
  BadgeCheck,
  FileText,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type {
  AgentWorkbenchCandidateDetailResponse,
  AgentWorkbenchCandidateSummary,
} from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import "./CandidateDetailDrawer.css";

type CandidateDetailDrawerStatus = "idle" | "loading" | "error" | "ready";

type CandidateDetailDrawerProps = {
  candidate?: AgentWorkbenchCandidateSummary | null;
  detail?: AgentWorkbenchCandidateDetailResponse | null;
  errorMessage?: string | undefined;
  onClose: () => void;
  onRetry?: (() => void) | undefined;
  open: boolean;
  status: CandidateDetailDrawerStatus;
};

type CandidateSourceKind = NonNullable<
  AgentWorkbenchCandidateSummary["sourceKinds"]
>[number];

const sourceLabels: Record<CandidateSourceKind, string> = {
  cts: "CTS 实验",
  liepin: "猎聘",
};

const focusableSelector = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function CandidateDetailDrawer({
  candidate = null,
  detail = null,
  errorMessage = "候选人详情暂时不可用。",
  onClose,
  onRetry,
  open,
  status,
}: CandidateDetailDrawerProps) {
  const drawerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) {
      return;
    }
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    const focusable = () => focusableDrawerElements(drawerRef.current);
    focusable()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const elements = focusable();
      if (elements.length === 0) {
        event.preventDefault();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const title = detail?.displayName ?? candidate?.displayName ?? "候选人详情";
  const headline = detail?.headline ?? candidate?.headline ?? "读取安全详情";

  return (
    <div className="candidate-detail-drawer__backdrop">
      <aside
        aria-label="候选人详情"
        aria-modal="true"
        className="candidate-detail-drawer"
        ref={drawerRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="candidate-detail-drawer__header">
          <span className="candidate-detail-drawer__avatar" aria-hidden="true">
            {title.slice(0, 1)}
          </span>
          <div>
            <p>候选人详情</p>
            <h2>{title}</h2>
            <span>{headline}</span>
          </div>
          <Button
            aria-label="关闭候选人详情"
            className="candidate-detail-drawer__close"
            icon={<X aria-hidden="true" size={16} />}
            onClick={onClose}
          />
        </header>

        {status === "loading" || status === "idle" ? (
          <CandidateDetailSkeleton />
        ) : status === "error" ? (
          <CandidateDetailError message={errorMessage} onRetry={onRetry} />
        ) : detail ? (
          <CandidateDetailBody candidate={candidate} detail={detail} />
        ) : (
          <CandidateDetailError message="候选人详情暂时不可用。" />
        )}
      </aside>
    </div>
  );
}

function CandidateDetailSkeleton() {
  return (
    <div
      aria-label="候选人详情读取中"
      className="candidate-detail-drawer__skeleton"
      role="status"
    >
      {[0, 1, 2, 3].map((item) => (
        <span key={item} />
      ))}
    </div>
  );
}

function CandidateDetailError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: (() => void) | undefined;
}) {
  return (
    <section className="candidate-detail-drawer__state" role="alert">
      <AlertTriangle aria-hidden="true" size={22} />
      <strong>无法读取详情</strong>
      <p>{message}</p>
      {onRetry ? (
        <Button
          icon={<RefreshCw aria-hidden="true" size={15} />}
          onClick={onRetry}
        >
          重试
        </Button>
      ) : null}
    </section>
  );
}

function CandidateDetailBody({
  candidate,
  detail,
}: {
  candidate: AgentWorkbenchCandidateSummary | null;
  detail: AgentWorkbenchCandidateDetailResponse;
}) {
  const scoreLabel = matchScoreLabel(
    detail.matchScore ?? candidate?.matchScore,
  );
  const sourceKinds = detail.sourceKinds ?? candidate?.sourceKinds ?? [];
  const access = accessStateCopy(detail.accessState, detail.reasonCode);
  const showDetailSections =
    detail.accessState === "allowed" && detail.sections.length > 0;

  return (
    <div
      aria-label="候选人详情内容"
      className="candidate-detail-drawer__body"
      tabIndex={0}
    >
      <section
        aria-label="候选人详情状态"
        className="candidate-detail-drawer__summary"
      >
        <div>
          <BadgeCheck aria-hidden="true" size={18} />
          <span>{detailAvailabilityLabel(detail.detailAvailability)}</span>
        </div>
        <div>
          <ShieldCheck aria-hidden="true" size={18} />
          <span>{evidenceLevelLabel(detail.evidenceLevel)}</span>
        </div>
        {scoreLabel ? (
          <div>
            <FileText aria-hidden="true" size={18} />
            <span>匹配 {scoreLabel}</span>
          </div>
        ) : null}
      </section>

      <section
        aria-label="候选人来源"
        className="candidate-detail-drawer__chips"
      >
        {sourceKinds.length > 0 ? (
          sourceKinds.map((sourceKind) => (
            <span key={sourceKind}>{sourceLabels[sourceKind]}</span>
          ))
        ) : (
          <span>来源待确认</span>
        )}
      </section>

      {detail.accessState === "allowed" ? null : (
        <section
          aria-label="候选人详情访问状态"
          className="candidate-detail-drawer__access"
        >
          <LockKeyhole aria-hidden="true" size={20} />
          <div>
            <strong>{access.title}</strong>
            <p>{access.text}</p>
          </div>
        </section>
      )}

      {showDetailSections ? (
        <div className="candidate-detail-drawer__sections">
          {detail.sections.map((section) => (
            <section className="candidate-detail-section" key={section.title}>
              <h3>{section.title}</h3>
              <ul>
                {section.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : detail.accessState === "allowed" ? (
        <section className="candidate-detail-drawer__state" role="status">
          <FileText aria-hidden="true" size={22} />
          <strong>暂无详情段落</strong>
          <p>暂时没有可展示的简历段落。</p>
        </section>
      ) : null}

      {detail.evidence.length > 0 ? (
        <section
          aria-label="候选人详情证据"
          className="candidate-detail-evidence"
        >
          <h3>证据</h3>
          <ul>
            {detail.evidence.map((evidence) => (
              <li key={evidence}>{evidence}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function matchScoreLabel(matchScore: number | null | undefined): string | null {
  if (typeof matchScore !== "number" || !Number.isFinite(matchScore)) {
    return null;
  }
  const normalized = matchScore <= 1 ? matchScore * 100 : matchScore;
  return `${String(Math.round(normalized))}%`;
}

function detailAvailabilityLabel(
  availability: AgentWorkbenchCandidateDetailResponse["detailAvailability"],
): string {
  if (availability === "available") {
    return "详情可读";
  }
  if (availability === "approval_required") {
    return "需要审批";
  }
  if (availability === "redacted") {
    return "安全脱敏";
  }
  return "详情不可用";
}

function evidenceLevelLabel(
  evidenceLevel: AgentWorkbenchCandidateDetailResponse["evidenceLevel"],
): string {
  if (evidenceLevel === "detail") {
    return "详细证据";
  }
  if (evidenceLevel === "final") {
    return "最终证据";
  }
  if (evidenceLevel === "summary") {
    return "摘要证据";
  }
  return "证据待确认";
}

function accessStateCopy(
  accessState: AgentWorkbenchCandidateDetailResponse["accessState"],
  reasonCode: string | null | undefined,
): { text: string; title: string } {
  if (accessState === "approval_required") {
    return {
      title: "读取完整详情前需要审批",
      text: "当前只显示安全摘要。审批完成后，这里会刷新为可读详情。",
    };
  }
  if (accessState === "redacted") {
    return {
      title: "详情已按来源策略脱敏",
      text: "当前候选人的原始简历字段未进入产品界面，只展示可公开复核的摘要。",
    };
  }
  if (reasonCode) {
    return {
      title: "详情暂时不可用",
      text: "请重试或检查来源权限。",
    };
  }
  return {
    title: "详情不可读取",
    text: "当前候选人没有可展示的详情权限或详情证据。",
  };
}

function focusableDrawerElements(drawer: HTMLElement | null): HTMLElement[] {
  if (drawer === null) {
    return [];
  }
  return Array.from(
    drawer.querySelectorAll<HTMLElement>(focusableSelector),
  ).filter(
    (element) => !element.hasAttribute("disabled") && element.tabIndex !== -1,
  );
}
