import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CandidateCardCandidate } from "./CandidateCard";
import { DetailApprovalQueue } from "./DetailApprovalQueue";

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
] satisfies readonly CandidateCardCandidate[];

const approvals = [
  {
    approvalId: "approval_candidate_001",
    candidateId: "candidate_001",
    status: "pending",
    reason: "读取完整简历详情以确认最近项目。",
  },
] as const;

describe("DetailApprovalQueue", () => {
  afterEach(() => cleanup());

  it("renders the empty approval state", () => {
    expect.hasAssertions();

    render(<DetailApprovalQueue approvals={[]} candidates={[]} />);

    expect(screen.getByRole("status")).toHaveTextContent("暂无详情审批");
  });

  it("renders pending detail approval actions for the matched candidate", () => {
    expect.hasAssertions();

    render(
      <DetailApprovalQueue approvals={approvals} candidates={candidates} />,
    );

    const item = screen.getByRole("article", { name: "候选人 A 详情审批" });
    expect(
      within(item).getByText("读取完整简历详情以确认最近项目。"),
    ).toBeInTheDocument();
    expect(
      within(item).getByRole("button", { name: "批准读取详情" }),
    ).toBeEnabled();
    expect(
      within(item).getByRole("button", { name: "拒绝读取详情" }),
    ).toBeEnabled();
  });
});
