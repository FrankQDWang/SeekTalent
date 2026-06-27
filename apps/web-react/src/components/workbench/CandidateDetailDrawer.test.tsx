import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
        detail={{
          ...agentWorkbenchCandidateDetailFixture,
          activeStatus: "近30天内活跃",
          age: 32,
          company: "平安集团",
          education: "本科",
          experienceYears: 10,
          gender: "男",
          jobStatus: "在职，看看新机会",
          location: "上海",
        }}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByRole("dialog", { name: "候选人详情" })).toBeVisible();
    expect(screen.getByText("吴所谓")).toBeVisible();
    expect(screen.getByText("在职，看看新机会")).toBeVisible();
    expect(screen.getByLabelText("候选人来源已记录")).toHaveTextContent(
      "猎聘来源",
    );
    expect(screen.getByText("近30天内活跃")).toBeVisible();
    expect(screen.getByText("男")).toBeVisible();
    expect(screen.getByText("32岁")).toBeVisible();
    expect(screen.getByText("上海")).toBeVisible();
    expect(screen.getByText("本科")).toBeVisible();
    expect(screen.getByText("工作10年")).toBeVisible();
    expect(screen.getByText("工作经历")).toBeVisible();
    expect(screen.getByText("匹配程度")).toBeVisible();
    expect(screen.getByText("多次通过流程重构提升任务完成率。")).toBeVisible();
    expect(
      screen.queryByText("读取完整详情前需要审批"),
    ).not.toBeInTheDocument();
  });

  it("labels CTS-only candidate sources without claiming Liepin", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={{
          ...candidate!,
          sourceKinds: ["cts"],
        }}
        detail={{
          ...agentWorkbenchCandidateDetailFixture,
          sourceKinds: ["cts"],
        }}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByLabelText("候选人来源已记录")).toHaveTextContent(
      "CTS 实验来源",
    );
    expect(screen.queryByText("猎聘来源")).not.toBeInTheDocument();
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

  it("keeps implementation reason codes out of recruiter-facing copy", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        detail={{
          ...agentWorkbenchCandidateApprovalRequiredDetailFixture,
          accessState: "denied",
          detailAvailability: "unavailable",
          reasonCode: "permission_denied",
        }}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByText("详情暂时不可用")).toBeVisible();
    expect(screen.getByText("请重试或检查来源权限。")).toBeVisible();
    expect(screen.queryByText(/permission_denied/)).not.toBeInTheDocument();
    expect(screen.queryByText(/后端/)).not.toBeInTheDocument();
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

  it("moves focus into the modal drawer, traps Tab, and restores trigger focus on close", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();

    render(<CandidateDetailDrawerHarness />);

    const trigger = screen.getByRole("button", { name: "打开候选人详情" });
    await user.click(trigger);
    const closeButton = screen.getByRole("button", { name: "关闭候选人详情" });
    expect(closeButton).toHaveFocus();

    await user.tab();
    expect(screen.getByLabelText("候选人详情内容")).toHaveFocus();

    await user.tab();
    expect(closeButton).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });

  it("does not recapture focus when an open drawer rerenders with a new close handler", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const firstClose = vi.fn();
    const secondClose = vi.fn();
    const { rerender } = render(
      <CandidateDetailDrawer
        candidate={candidate}
        errorMessage="请求失败，状态码 404"
        onClose={firstClose}
        onRetry={() => undefined}
        open
        status="error"
      />,
    );
    const retryButton = screen.getByRole("button", { name: "重试" });

    await user.tab();
    expect(retryButton).toHaveFocus();

    rerender(
      <CandidateDetailDrawer
        candidate={candidate}
        errorMessage="请求失败，状态码 404"
        onClose={secondClose}
        onRetry={() => undefined}
        open
        status="error"
      />,
    );

    expect(retryButton).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(firstClose).not.toHaveBeenCalled();
    expect(secondClose).toHaveBeenCalledOnce();
  });
});

function CandidateDetailDrawerHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        打开候选人详情
      </button>
      <CandidateDetailDrawer
        candidate={candidate}
        detail={agentWorkbenchCandidateDetailFixture}
        onClose={() => setOpen(false)}
        open={open}
        status="ready"
      />
    </>
  );
}
