import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agentWorkbenchCompletedViewFixture,
  agentWorkbenchPermissionDeniedViewFixture,
  agentWorkbenchRequirementReviewViewFixture,
  agentWorkbenchRunningViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "./ConversationScreen";

vi.mock("./StrategyGraph", () => ({
  StrategyGraph: () => <section aria-label="检索策略图" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("ConversationScreen", () => {
  it("renders the live workbench view and submits composer messages", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmitMessage = vi.fn();

    render(
      <ConversationScreen
        onSubmitMessage={onSubmitMessage}
        view={agentWorkbenchRunningViewFixture}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("输入下一步要求"),
      "继续收紧关键词",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getByLabelText("检索策略图")).toBeVisible();
    expect(screen.getByLabelText("Agent transcript")).toBeVisible();
    expect(onSubmitMessage).toHaveBeenCalledWith("继续收紧关键词");
  });

  it("exposes requirement confirmation as a callback", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onConfirmRequirements = vi.fn();

    render(
      <ConversationScreen
        onConfirmRequirements={onConfirmRequirements}
        view={agentWorkbenchRequirementReviewViewFixture}
      />,
    );

    await user.click(screen.getByRole("button", { name: "确认需求" }));

    expect(onConfirmRequirements).toHaveBeenCalledOnce();
    expect(screen.getByRole("region", { name: "需求确认" })).toBeVisible();
    expect(screen.queryByLabelText("检索策略图")).not.toBeInTheDocument();
  });

  it("keeps requirement review inside the transcript flow", () => {
    expect.hasAssertions();

    render(
      <ConversationScreen view={agentWorkbenchRequirementReviewViewFixture} />,
    );

    const transcript = screen.getByLabelText("Agent transcript");
    expect(
      within(transcript).getByRole("button", { name: "确认需求" }),
    ).toBeVisible();
  });

  it("shows permission failure as a stable screen state", () => {
    expect.hasAssertions();

    render(
      <ConversationScreen view={agentWorkbenchPermissionDeniedViewFixture} />,
    );

    expect(screen.getByText("来源授权需要处理")).toBeVisible();
    expect(screen.getByPlaceholderText("输入下一步要求")).toBeDisabled();
  });

  it("shows completed runs as export-ready screen state", () => {
    expect.hasAssertions();

    render(<ConversationScreen view={agentWorkbenchCompletedViewFixture} />);

    expect(screen.getByText("最终名单已生成")).toBeVisible();
    expect(
      screen.getAllByText("第一轮推荐 2 位候选人，候选人 A 为强匹配。").length,
    ).toBeGreaterThan(0);
  });

  it("mounts the strategy graph only when the compact graph panel is active", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const addEventListener = vi.fn();
    const removeEventListener = vi.fn();

    vi.stubGlobal("matchMedia", (query: string) => ({
      addEventListener,
      addListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: true,
      media: query,
      onchange: null,
      removeEventListener,
      removeListener: vi.fn(),
    }));

    render(<ConversationScreen view={agentWorkbenchRunningViewFixture} />);

    expect(screen.queryByLabelText("检索策略图")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Graph" }));

    expect(screen.getByLabelText("检索策略图")).toBeVisible();
    expect(addEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
  });
});

describe("ConversationScreenSide", () => {
  it("keeps candidate detail navigation behind a callback", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onViewCandidateDetails = vi.fn();

    render(
      <ConversationScreenSide
        onViewCandidateDetails={onViewCandidateDetails}
        view={agentWorkbenchRunningViewFixture}
      />,
    );

    await user.click(screen.getByRole("tab", { name: "候选人" }));
    const detailsButtons = screen.getAllByRole("button", { name: "查看详情" });
    expect(detailsButtons.length).toBeGreaterThan(0);
    await user.click(detailsButtons[0] as HTMLElement);

    expect(onViewCandidateDetails).toHaveBeenCalledWith("candidate_001");
  });
});
