import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CandidateCard } from "./CandidateCard";

const candidateFixture = {
  candidateId: "candidate_001",
  displayName: "候选人 A",
  headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
  matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
  sourceKind: "liepin",
  status: "reviewing",
} as const;

describe("CandidateCard", () => {
  afterEach(() => cleanup());

  it("renders the safe candidate summary and evidence", () => {
    expect.hasAssertions();

    render(<CandidateCard candidate={candidateFixture} />);

    expect(
      screen.getByRole("article", { name: "候选人 A" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("平台后端负责人 / 某 AI Infra 公司 / 上海"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("有 Agent 工具调用平台和 RAG 检索链路经验。"),
    ).toHaveLength(2);
    expect(screen.getByText("猎聘")).toBeInTheDocument();
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
