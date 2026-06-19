import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agentWorkbenchCandidateApprovalRequiredDetailFixture,
  agentWorkbenchCandidateDetailFixture,
  agentWorkbenchRunningViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { CandidateDetailDrawer } from "./CandidateDetailDrawer";

const candidate = agentWorkbenchRunningViewFixture.candidates[0] ?? null;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CandidateDetailDrawer", () => {
  it("renders safe candidate detail sections and evidence", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        detail={agentWorkbenchCandidateDetailFixture}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByRole("dialog", { name: "候选人详情" })).toBeVisible();
    expect(screen.getByText("候选人 A")).toBeVisible();
    expect(screen.getByText("工作经历")).toBeVisible();
    expect(screen.getByText("技能匹配")).toBeVisible();
    expect(
      screen.getByText("最近一段经历覆盖 Agent 工具调用平台。"),
    ).toBeVisible();
    expect(
      screen.queryByText("读取完整详情前需要审批"),
    ).not.toBeInTheDocument();
  });

  it("shows approval state without fabricating detail sections", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        detail={agentWorkbenchCandidateApprovalRequiredDetailFixture}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByText("读取完整详情前需要审批")).toBeVisible();
    expect(screen.queryByText("工作经历")).not.toBeInTheDocument();
  });

  it("supports retrying after a detail request failure", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onRetry = vi.fn();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        errorMessage="请求失败，状态码 404"
        onClose={() => undefined}
        onRetry={onRetry}
        open
        status="error"
      />,
    );

    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(screen.getByText("无法读取详情")).toBeVisible();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("closes with Escape", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        detail={agentWorkbenchCandidateDetailFixture}
        onClose={onClose}
        open
        status="ready"
      />,
    );

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
  });
});
