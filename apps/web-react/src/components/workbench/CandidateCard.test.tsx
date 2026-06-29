import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CandidateCardCandidate } from "./CandidateCard";
import { CandidateCard } from "./CandidateCard";

const candidateFixture = {
  candidateId: "candidate_001",
  rank: 1,
  displayName: "候选人 A",
  avatarLabel: "吴",
  avatarColorKey: "avatar-0",
  sourceLabel: "猎聘",
  currentTitle: "资深体验设计工程师",
  currentCompany: "小米科技",
  headline: "平台后端负责人",
  company: "某 AI Infra 公司",
  age: 32,
  location: "上海",
  education: "本科",
  workYears: 10,
  experienceYears: 10,
  sourceKinds: ["liepin"],
  matchScore: 92,
  matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
  status: "reviewing",
  detailAvailability: "approval_required",
  accessState: "approval_required",
  evidenceLevel: "summary",
} satisfies CandidateCardCandidate;

describe("CandidateCard", () => {
  afterEach(() => cleanup());

  it("renders a compact safe candidate profile", () => {
    expect.hasAssertions();

    render(<CandidateCard candidate={candidateFixture} />);

    expect(
      screen.getByRole("article", { name: "候选人 A" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "候选人 A" })).toHaveAttribute(
      "data-avatar-color",
      "avatar-0",
    );
    expect(screen.getByText("吴")).toBeInTheDocument();
    expect(
      screen.getByText("资深体验设计工程师 · 小米科技"),
    ).toBeInTheDocument();
    expect(screen.getByText("猎聘")).toBeInTheDocument();
    expect(screen.getByText("待复核")).toBeInTheDocument();
    expect(screen.getByText("92分")).toBeInTheDocument();
    expect(screen.getByText("32岁")).toBeInTheDocument();
    expect(screen.getByText("上海")).toBeInTheDocument();
    expect(screen.getByText("本科")).toBeInTheDocument();
    expect(screen.getByText("工作10年")).toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "候选人 A" }),
    ).not.toHaveTextContent("有 Agent 工具调用平台和 RAG 检索链路经验。");
  });

  it("ignores forbidden raw provider, auth, and resume fields when present", () => {
    expect.hasAssertions();

    const candidateWithRawFields = {
      ...candidateFixture,
      rawProviderPayload: "rawProviderPayload",
      providerAuthUrl: "providerAuthUrl",
      cookie: "cookie",
      storageToken: "storageToken",
      authHeader: "authHeader",
      rawResumeBody: "rawResumeBody",
    };

    const { container } = render(
      <CandidateCard candidate={candidateWithRawFields} />,
    );

    expect(container).not.toHaveTextContent(
      /rawProviderPayload|providerAuthUrl|cookie|storageToken|authHeader|rawResumeBody/,
    );
  });

  it("exposes a detail action without opening raw provider data", () => {
    expect.hasAssertions();

    render(<CandidateCard candidate={candidateFixture} />);

    const article = screen.getByRole("article", { name: "候选人 A" });
    expect(
      within(article).getByRole("button", { name: "查看详情" }),
    ).toBeEnabled();
  });
});
