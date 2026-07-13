import { AlertTriangle, LockKeyhole, RefreshCw, X } from "lucide-react";
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
  const sourceReferences = (detail?.sourceReferences ?? []).flatMap(
    (reference) => {
      const url = safeExternalSourceUrl(reference.url);
      const displayLabel = reference.displayLabel.trim();
      return url && displayLabel ? [{ ...reference, displayLabel, url }] : [];
    },
  );
  const avatarLabel = candidateAvatarLabel(detail, candidate);
  const avatarColorKey =
    detail?.avatarColorKey ?? candidate?.avatarColorKey ?? "default";

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
          <span
            aria-hidden="true"
            className="candidate-detail-drawer__avatar"
            data-avatar-color={avatarColorKey}
          >
            {avatarLabel}
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
            {sourceReferences.map((reference) => (
              <a
                className="candidate-detail-drawer__source-action"
                href={reference.url}
                key={`${reference.sourceKind}:${reference.url}`}
                rel="noreferrer"
                target="_blank"
              >
                {reference.displayLabel}
              </a>
            ))}
            <Button
              aria-label="关闭候选人详情"
              className="candidate-detail-drawer__close"
              icon={<X aria-hidden="true" size={16} />}
              onClick={onClose}
            />
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
  const sections = buildStructuredSections(detail);

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

      {sections.length > 0 ? (
        <div className="candidate-detail-drawer__sections">
          {sections.map((section) => (
            <CandidateDetailSection key={section.title} section={section} />
          ))}
        </div>
      ) : detail.accessState === "allowed" ? (
        <section className="candidate-detail-drawer__state" role="status">
          <strong>暂无候选人详情</strong>
          <p>当前还没有可展示的履历信息。</p>
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
      text: "审批完成后，这里会刷新为完整候选人详情。",
    };
  }
  if (accessState === "redacted") {
    return {
      title: "详情暂时不可用",
      text: "当前来源暂未返回完整候选人详情。",
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
  const currentTitle =
    detail?.currentTitle?.trim() ?? candidate?.currentTitle?.trim();
  const currentCompany =
    detail?.currentCompany?.trim() ?? candidate?.currentCompany?.trim();
  if (currentTitle && currentCompany) {
    return `${currentTitle} · ${currentCompany}`;
  }
  if (currentTitle || currentCompany) {
    return currentTitle || currentCompany || "";
  }
  const headline = detail?.headline?.trim() ?? candidate?.headline?.trim();
  const company =
    detail?.company?.trim() ??
    detail?.currentCompany?.trim() ??
    candidate?.company?.trim() ??
    candidate?.currentCompany?.trim();
  if (headline && company && !headline.includes(company)) {
    return `${headline} · ${company}`;
  }
  return headline || company || "候选人详情待补充";
}

function candidateDetailChips(
  detail: AgentWorkbenchCandidateDetailResponse | null,
  candidate: AgentWorkbenchCandidateSummary | null,
): string[] {
  const activeStatus = detail?.activeStatus ?? candidate?.activeStatus ?? null;
  const gender = detail?.gender ?? candidate?.gender ?? null;
  const age = detail?.age ?? candidate?.age ?? null;
  const location =
    detail?.city ??
    detail?.location ??
    candidate?.city ??
    candidate?.location ??
    null;
  const education = detail?.education ?? candidate?.education ?? null;
  const experienceYears =
    detail?.workYears ??
    detail?.experienceYears ??
    candidate?.workYears ??
    candidate?.experienceYears ??
    null;
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

function candidateAvatarLabel(
  detail: AgentWorkbenchCandidateDetailResponse | null,
  candidate: AgentWorkbenchCandidateSummary | null,
): string {
  return (
    detail?.avatarLabel?.trim() ||
    candidate?.avatarLabel?.trim() ||
    detail?.displayName.slice(0, 1) ||
    candidate?.displayName.slice(0, 1) ||
    "候"
  );
}

function buildStructuredSections(
  detail: AgentWorkbenchCandidateDetailResponse,
): AgentWorkbenchCandidateDetailResponse["sections"] {
  return [
    matchSection(detail.match),
    jobIntentionSection(detail.jobIntention),
    timelineSection(
      "工作经历",
      detail.workExperience,
      formatWorkExperienceItem,
    ),
    timelineSection(
      "项目经历",
      detail.projectExperience,
      formatProjectExperienceItem,
    ),
    timelineSection(
      "教育经历",
      detail.educationExperience,
      formatEducationExperienceItem,
    ),
    skillsSection(detail.skills),
  ].filter(
    (
      section,
    ): section is AgentWorkbenchCandidateDetailResponse["sections"][number] =>
      section !== null,
  );
}

function matchSection(
  match: AgentWorkbenchCandidateDetailResponse["match"],
): AgentWorkbenchCandidateDetailResponse["sections"][number] | null {
  if (!match) {
    return null;
  }
  return match.summary
    ? { title: "匹配程度", items: [`推荐理由：${match.summary}`] }
    : null;
}

function jobIntentionSection(
  jobIntention: AgentWorkbenchCandidateDetailResponse["jobIntention"],
): AgentWorkbenchCandidateDetailResponse["sections"][number] | null {
  if (!jobIntention) {
    return null;
  }
  const items = [
    jobIntention.expectedRole ? `期望岗位：${jobIntention.expectedRole}` : null,
    jobIntention.expectedIndustry
      ? `期望行业：${jobIntention.expectedIndustry}`
      : null,
    jobIntention.expectedCity ? `期望地点：${jobIntention.expectedCity}` : null,
    jobIntention.expectedSalary
      ? `期望薪资：${jobIntention.expectedSalary}`
      : null,
  ].filter((item): item is string => Boolean(item));
  return items.length > 0 ? { title: "求职意向", items } : null;
}

function timelineSection(
  title: string,
  items: AgentWorkbenchCandidateDetailResponse["workExperience"],
  formatItem: (item: NonNullable<typeof items>[number]) => string[],
): AgentWorkbenchCandidateDetailResponse["sections"][number] | null {
  const normalizedItems = (items ?? []).flatMap(formatItem);
  return normalizedItems.length > 0 ? { title, items: normalizedItems } : null;
}

function formatWorkExperienceItem(
  item: NonNullable<
    AgentWorkbenchCandidateDetailResponse["workExperience"]
  >[number],
): string[] {
  return compactLines([
    item.dateRange,
    joinWithSeparator(" | ", [item.company, item.title]),
    item.description,
  ]);
}

function formatProjectExperienceItem(
  item: NonNullable<
    AgentWorkbenchCandidateDetailResponse["projectExperience"]
  >[number],
): string[] {
  return compactLines([
    item.dateRange,
    joinWithSeparator(" | ", [item.name, item.role]),
    item.description,
  ]);
}

function formatEducationExperienceItem(
  item: NonNullable<
    AgentWorkbenchCandidateDetailResponse["educationExperience"]
  >[number],
): string[] {
  return compactLines([
    item.dateRange,
    joinWithSpace([item.school, item.major, item.degree]),
  ]);
}

function skillsSection(
  skills: AgentWorkbenchCandidateDetailResponse["skills"],
): AgentWorkbenchCandidateDetailResponse["sections"][number] | null {
  const items = (skills ?? []).filter((skill): skill is string =>
    Boolean(skill),
  );
  return items.length > 0 ? { title: "技能标签", items } : null;
}

function compactLines(lines: Array<string | null | undefined>): string[] {
  return lines
    .map((line) => line?.trim())
    .filter((line): line is string => Boolean(line));
}

function safeExternalSourceUrl(
  value: string | null | undefined,
): string | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  try {
    const url = new URL(trimmed);
    return (url.protocol === "http:" || url.protocol === "https:") &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function joinWithSeparator(
  separator: string,
  parts: Array<string | null | undefined>,
): string | null {
  const values = parts
    .map((part) => part?.trim())
    .filter((part): part is string => Boolean(part));
  return values.length > 0 ? values.join(separator) : null;
}

function joinWithSpace(parts: Array<string | null | undefined>): string | null {
  return joinWithSeparator(" ", parts);
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
