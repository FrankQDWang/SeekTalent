import {
  AlertTriangle,
  FileText,
  LockKeyhole,
  RefreshCw,
  X,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type {
  AgentWorkbenchCandidateDetailResponse,
  AgentWorkbenchCandidateSummary,
} from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import { candidateSourceLabel } from "./candidateSource";
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
  const headline = candidateDetailHeadline(detail, candidate);
  const profileChips = candidateDetailChips(detail, candidate);
  const jobStatus = detail?.jobStatus ?? candidate?.jobStatus ?? null;
  const sourceKinds = detail?.sourceKinds ?? candidate?.sourceKinds ?? [];
  const sourceLabel =
    sourceKinds.length > 0 ? `${candidateSourceLabel(sourceKinds)}来源` : null;

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
          <div className="candidate-detail-drawer__profile">
            <div className="candidate-detail-drawer__title-row">
              <h2>{title}</h2>
              {jobStatus ? (
                <span className="candidate-detail-drawer__job-status">
                  {jobStatus}
                </span>
              ) : null}
            </div>
            <p>{headline}</p>
            {profileChips.length > 0 ? (
              <div
                aria-label="候选人基础信息"
                className="candidate-detail-drawer__profile-chips"
              >
                {profileChips.map((chip) => (
                  <span key={chip}>{chip}</span>
                ))}
              </div>
            ) : null}
          </div>
          <div className="candidate-detail-drawer__actions">
            <Button
              aria-label="关闭候选人详情"
              className="candidate-detail-drawer__close"
              icon={<X aria-hidden="true" size={16} />}
              onClick={onClose}
            />
            {sourceLabel ? (
              <span
                aria-label="候选人来源已记录"
                className="candidate-detail-drawer__source-action"
              >
                {sourceLabel}
              </span>
            ) : null}
          </div>
        </header>

        {status === "loading" || status === "idle" ? (
          <CandidateDetailSkeleton />
        ) : status === "error" ? (
          <CandidateDetailError message={errorMessage} onRetry={onRetry} />
        ) : detail ? (
          <CandidateDetailBody detail={detail} />
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
  detail,
}: {
  detail: AgentWorkbenchCandidateDetailResponse;
}) {
  const access = accessStateCopy(detail.accessState, detail.reasonCode);
  const showDetailSections =
    detail.accessState === "allowed" && detail.sections.length > 0;

  return (
    <div
      aria-label="候选人详情内容"
      className="candidate-detail-drawer__body"
      tabIndex={0}
    >
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
            <CandidateDetailSection key={section.title} section={section} />
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
          className="candidate-detail-section"
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

function CandidateDetailSection({
  section,
}: {
  section: AgentWorkbenchCandidateDetailResponse["sections"][number];
}) {
  const isSkillSection = section.title.includes("技能");
  return (
    <section className="candidate-detail-section">
      <h3>{section.title}</h3>
      <ul data-style={isSkillSection ? "chips" : "list"}>
        {section.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
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

function candidateDetailHeadline(
  detail: AgentWorkbenchCandidateDetailResponse | null,
  candidate: AgentWorkbenchCandidateSummary | null,
): string {
  const headline = detail?.headline?.trim() ?? candidate?.headline?.trim();
  const company = detail?.company?.trim() ?? candidate?.company?.trim();
  if (headline && company && !headline.includes(company)) {
    return `${headline} · ${company}`;
  }
  return headline || company || "读取安全详情";
}

function candidateDetailChips(
  detail: AgentWorkbenchCandidateDetailResponse | null,
  candidate: AgentWorkbenchCandidateSummary | null,
): string[] {
  const activeStatus = detail?.activeStatus ?? candidate?.activeStatus ?? null;
  const gender = detail?.gender ?? candidate?.gender ?? null;
  const age = detail?.age ?? candidate?.age ?? null;
  const location = detail?.location ?? candidate?.location ?? null;
  const education = detail?.education ?? candidate?.education ?? null;
  const experienceYears =
    detail?.experienceYears ?? candidate?.experienceYears ?? null;
  return [
    activeStatus,
    gender,
    typeof age === "number" ? `${String(age)}岁` : null,
    location,
    education,
    typeof experienceYears === "number"
      ? `工作${String(experienceYears)}年`
      : null,
  ].filter((chip): chip is string => Boolean(chip));
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
