import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CandidateCardCandidate } from "./CandidateCard";
import { CandidateQueue } from "./CandidateQueue";

const candidates = [
  {
    candidateId: "candidate_001",
    rank: 1,
    displayName: "候选人 A",
    headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
    company: "某 AI Infra 公司",
    location: "上海",
    education: "本科",
    experienceYears: 10,
    sourceKinds: ["liepin"],
    matchScore: 92,
    matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
    status: "reviewing",
    detailAvailability: "approval_required",
    accessState: "approval_required",
    evidenceLevel: "summary",
  },
  {
    candidateId: "candidate_002",
    rank: 2,
    displayName: "候选人 B",
    headline: "高级后端工程师 / 某企业协作产品 / 上海",
    company: "某企业协作产品",
    location: "上海",
    education: "硕士",
    experienceYears: 8,
    sourceKinds: ["cts"],
    matchScore: 84,
    matchSummary: "RAG 和搜索经验强，Agent 平台经验较少。",
    status: "new",
    detailAvailability: "redacted",
    accessState: "redacted",
    evidenceLevel: "summary",
  },
] satisfies readonly CandidateCardCandidate[];

describe("CandidateQueue", () => {
  afterEach(() => cleanup());

  it("renders the empty candidate state", () => {
    expect.hasAssertions();

    render(<CandidateQueue candidates={[]} />);

    expect(screen.getByRole("status")).toHaveTextContent("暂无候选人简历");
  });

  it("renders populated candidates with a stable queue label", () => {
    expect.hasAssertions();

    render(
      <CandidateQueue
        candidates={candidates}
        selectedCandidateId="candidate_001"
        totalCount={3}
      />,
    );

    const queue = screen.getByRole("region", { name: "候选人队列" });
    expect(within(queue).getByText("候选人 A")).toBeInTheDocument();
    expect(within(queue).getByText("候选人 B")).toBeInTheDocument();
    expect(within(queue).getByText("共 3 位")).toBeInTheDocument();
  });
});
