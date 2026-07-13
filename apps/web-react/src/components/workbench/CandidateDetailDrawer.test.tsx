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

const candidate = requireCandidate();

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CandidateDetailDrawer", () => {
  it("renders WTS detail sections without exposing raw evidence copy", () => {
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
    expect(screen.queryByLabelText("候选人来源已记录")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("近30天内活跃")).toBeVisible();
    expect(screen.getByText("男")).toBeVisible();
    expect(screen.getByText("32岁")).toBeVisible();
    expect(screen.getByText("上海")).toBeVisible();
    expect(screen.getByText("本科")).toBeVisible();
    expect(screen.getByText("工作10年")).toBeVisible();
    expect(screen.getByText("暂无候选人详情")).toBeVisible();
    expect(
      screen.queryByText("多次通过流程重构提升任务完成率。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "可独立主导 0-1 产品体验搭建，擅长拆解复杂 B 端业务流程。",
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("读取完整详情前需要审批"),
    ).not.toBeInTheDocument();
  });

  it("renders WTS structured fields and one labeled link per source reference", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={{
          ...candidate,
          avatarLabel: "吴",
          avatarColorKey: "avatar-0",
          currentTitle: "资深体验设计工程师",
          currentCompany: "平安集团",
          sourceLabel: "猎聘",
        }}
        detail={{
          ...agentWorkbenchCandidateDetailFixture,
          avatarLabel: "吴",
          avatarColorKey: "avatar-0",
          activeStatus: "近30天内活跃",
          currentTitle: "资深体验设计工程师",
          currentCompany: "平安集团",
          gender: "男",
          age: 32,
          location: "上海",
          education: "本科",
          workYears: 10,
          sourceLabel: "猎聘",
          sourceReferences: [
            {
              sourceKind: "liepin",
              displayLabel: "猎聘",
              url: "https://example.test/candidate/1",
            },
            {
              sourceKind: "zhaopin",
              displayLabel: "智联招聘",
              url: "https://zhaopin.example.test/candidate/1",
            },
          ],
          match: {
            summary: "可独立主导 0-1 产品体验搭建，擅长拆解复杂 B 端业务流程。",
            strengths: ["搭建可量化体验度量体系", "具备完整设计系统经验"],
            weaknesses: ["AI 产品体验设计项目未在简历中明确体现"],
            score: 92,
            fitBucket: "strong_fit",
          },
          jobIntention: {
            expectedRole: "高端设计职位，设计，设计经理/主管",
            expectedIndustry: "互联网，其他",
            expectedCity: "上海",
            expectedSalary: "20-24k*14薪",
          },
          workExperience: [
            {
              dateRange: "2019.06-至今（7年）",
              company: "平安好医",
              title: "用户体验设计专家",
              description: "提供 B 端及 C 端体验设计方案。",
            },
          ],
          projectExperience: [
            {
              dateRange: "2020.05-至今（6年1个月）",
              name: "助力 C 端业务增长",
              role: "项目职务：-",
              description: "通过设计调研提升转化率。",
            },
          ],
          educationExperience: [
            {
              dateRange: "2011.09-2014.07（2年10个月）",
              school: "华东师范大学",
              major: "工业设计",
              degree: "硕士",
            },
          ],
          skills: ["技能标签1", "技能标签2"],
          sections: [],
        }}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(screen.getByText("吴")).toBeVisible();
    expect(screen.getByText("吴")).toHaveAttribute(
      "data-avatar-color",
      "avatar-0",
    );
    expect(screen.getByText("资深体验设计工程师 · 平安集团")).toBeVisible();
    expect(screen.getByRole("link", { name: "猎聘" })).toHaveAttribute(
      "href",
      "https://example.test/candidate/1",
    );
    expect(screen.getByRole("link", { name: "智联招聘" })).toHaveAttribute(
      "href",
      "https://zhaopin.example.test/candidate/1",
    );
    expect(screen.getByText("匹配程度")).toBeVisible();
    expect(screen.getByText(/推荐理由：可独立主导/)).toBeVisible();
    expect(screen.queryAllByText(/候选人强项/)).toHaveLength(0);
    expect(screen.queryAllByText(/候选人弱项/)).toHaveLength(0);
    expect(screen.getByText("求职意向")).toBeVisible();
    expect(
      screen.getByText("期望岗位：高端设计职位，设计，设计经理/主管"),
    ).toBeVisible();
    expect(screen.getByText("工作经历")).toBeVisible();
    expect(screen.getByText("平安好医 | 用户体验设计专家")).toBeVisible();
    expect(screen.getByText("项目经历")).toBeVisible();
    expect(screen.getByText("助力 C 端业务增长 | 项目职务：-")).toBeVisible();
    expect(screen.getByText("教育经历")).toBeVisible();
    expect(screen.getByText("华东师范大学 工业设计 硕士")).toBeVisible();
    expect(screen.getByText("技能标签")).toBeVisible();
    expect(screen.getByText("技能标签1")).toBeVisible();
  });

  it("does not fall back to legacy sections when structured WTS detail fields are missing", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={candidate}
        detail={{
          ...agentWorkbenchCandidateDetailFixture,
          match: null,
          jobIntention: null,
          workExperience: [],
          projectExperience: [],
          educationExperience: [],
          skills: [],
          sections: [
            {
              title: "工作经历",
              items: ["2019.06-至今 平安好医 | 用户体验设计专家"],
            },
          ],
        }}
        onClose={() => undefined}
        open
        status="ready"
      />,
    );

    expect(
      screen.queryByText("2019.06-至今 平安好医 | 用户体验设计专家"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("暂无候选人详情")).toBeVisible();
  });

  it("omits unsafe source reference URLs", () => {
    expect.hasAssertions();

    for (const url of [
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "https://user:secret@example.test/candidate/1",
      "not a url",
    ]) {
      const { unmount } = render(
        <CandidateDetailDrawer
          candidate={candidate}
          detail={{
            ...agentWorkbenchCandidateDetailFixture,
            sourceLabel: "猎聘",
            sourceReferences: [
              { sourceKind: "liepin", displayLabel: "猎聘", url },
            ],
          }}
          onClose={() => undefined}
          open
          status="ready"
        />,
      );

      expect(screen.queryByRole("link", { name: "猎聘" })).toBeNull();
      expect(screen.queryByLabelText("候选人来源已记录")).toBeNull();
      unmount();
    }
  });

  it("labels CTS-only candidate sources without claiming Liepin", () => {
    expect.hasAssertions();

    render(
      <CandidateDetailDrawer
        candidate={{
          ...candidate,
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

    expect(screen.queryByLabelText("候选人来源已记录")).not.toBeInTheDocument();
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

function requireCandidate() {
  const firstCandidate = agentWorkbenchRunningViewFixture.candidates[0];
  if (firstCandidate === undefined) {
    throw new Error(
      "agentWorkbenchRunningViewFixture must include a candidate",
    );
  }
  return firstCandidate;
}
