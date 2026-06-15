import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DetailApprovalItem, type DetailApproval } from "./DetailApprovalItem";

const candidate = {
  candidateId: "candidate_001",
  displayName: "候选人 A",
  headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
} as const;

function approval(status: DetailApproval["status"]): DetailApproval {
  return {
    approvalId: `approval_${status}`,
    candidateId: "candidate_001",
    reason: "读取完整简历详情以确认最近项目。",
    status,
  };
}

describe("DetailApprovalItem", () => {
  afterEach(() => cleanup());

  it("renders pending detail approval actions", () => {
    expect.hasAssertions();

    render(
      <DetailApprovalItem
        approval={approval("pending")}
        candidate={candidate}
      />,
    );

    const item = screen.getByRole("article", { name: "候选人 A 详情审批" });
    expect(within(item).getByText("待审批")).toBeInTheDocument();
    expect(
      within(item).getByRole("button", { name: "批准读取详情" }),
    ).toBeEnabled();
    expect(
      within(item).getByRole("button", { name: "拒绝读取详情" }),
    ).toBeEnabled();
  });

  it.each([
    ["accepted", "已接受"],
    ["rejected", "已拒绝"],
    ["applied", "已应用"],
  ] as const)("renders the %s public approval state", (status, label) => {
    expect.hasAssertions();

    render(
      <DetailApprovalItem approval={approval(status)} candidate={candidate} />,
    );

    const item = screen.getByRole("article", { name: "候选人 A 详情审批" });
    expect(within(item).getByText(label)).toBeInTheDocument();
    expect(
      within(item).queryByRole("button", { name: "批准读取详情" }),
    ).not.toBeInTheDocument();
    expect(
      within(item).queryByRole("button", { name: "拒绝读取详情" }),
    ).not.toBeInTheDocument();
  });
});
