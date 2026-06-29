import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentStrategyGraph } from "../../lib/strategy-graph/graphProjection";
import { StrategyGraph } from "./StrategyGraph";

const graph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求确认",
      summary: "已确认岗位要求，准备启动多轮猎聘检索。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:round_query:all",
      kind: "phase",
      label: "round_query",
      phase: "query",
      roundNo: 1,
      stage: "round_query",
      summary: "第 1 轮查询策略已生成。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:source_result:liepin",
      kind: "phase",
      label: "liepin source_result",
      phase: "source",
      roundNo: 1,
      stage: "source_result",
      summary: "猎聘返回 16 份安全摘要。",
      status: "running",
      sourceKind: "liepin",
    },
    {
      nodeId: "round:1:phase:scoring:all",
      kind: "phase",
      label: "scoring",
      phase: "scoring",
      roundNo: 1,
      stage: "scoring",
      summary: "Top Pool 正在评分。",
      status: "pending",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:feedback:all",
      kind: "phase",
      label: "feedback",
      phase: "feedback",
      roundNo: 1,
      stage: "feedback",
      summary: "准备下一轮策略。",
      status: "pending",
      sourceKind: "all",
    },
  ],
  edges: [
    {
      edgeId: "requirements->round:1:phase:round_query:all",
      fromNodeId: "requirements",
      toNodeId: "round:1:phase:round_query:all",
      label: "生成检索策略",
    },
    {
      edgeId:
        "round:1:phase:round_query:all->round:1:phase:source_result:liepin",
      fromNodeId: "round:1:phase:round_query:all",
      toNodeId: "round:1:phase:source_result:liepin",
      label: "猎聘检索",
    },
  ],
};

describe("StrategyGraph", () => {
  afterEach(() => cleanup());

  it("renders the designer-style read-only round timeline from BFF graph metadata", () => {
    expect.hasAssertions();

    const { container } = render(
      <StrategyGraph graph={graph} jobTitle="AI Agent 平台工程师" />,
    );

    const region = screen.getByRole("region", { name: "检索策略图" });
    expect(region).toBeVisible();
    expect(screen.getByText("AI Agent 平台工程师")).toBeVisible();
    expect(screen.getByText("需求拆解")).toBeVisible();
    expect(screen.getByText("第 1 轮 · 查询包")).toBeVisible();
    expect(screen.getByText("第 1 轮 · 猎聘检索")).toBeVisible();
    expect(screen.getByText("第 1 轮 · Top Pool")).toBeVisible();
    expect(screen.getByText("第 1 轮 · 下一轮策略")).toBeVisible();
    expect(screen.getByText("猎聘")).toBeVisible();
    expect(container.querySelector(".strategy-graph__timeline")).toBeNull();
    expect(screen.queryByText("第 1 轮")).not.toBeInTheDocument();
    expect(screen.queryByText("第 1 轮检索中")).not.toBeInTheDocument();
    expect(screen.queryByText(/单轮检索|\d+ 轮检索/)).not.toBeInTheDocument();
    expect(screen.queryByText(/CTS/i)).not.toBeInTheDocument();
    expect(container.querySelector(".react-flow")).not.toBeInTheDocument();
    expect(screen.getByLabelText("检索策略图控制")).toBeVisible();
    expect(screen.getByRole("button", { name: "放大策略图" })).toBeVisible();
    expect(screen.getByRole("button", { name: "缩小策略图" })).toBeVisible();
    expect(screen.getByRole("button", { name: "最大化策略图" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "恢复策略图初始位置" }),
    ).toBeVisible();
    expect(
      container.querySelector('[data-edge-id="job-root->strategy-root"]'),
    ).toHaveClass("strategy-graph__edge--root");
    expect(
      container.querySelector('[data-edge-id="job-root->strategy-root"]'),
    ).toHaveAttribute("d", "M 238 213 H 316 V 109 H 340");
    expect(
      container.querySelector(
        '[data-edge-id="requirements->round:1:phase:round_query:all"]',
      ),
    ).toHaveAttribute("d", "M 550 109 H 638");
  });

  it("does not select nodes or open detail UI when clicked", () => {
    expect.hasAssertions();

    render(<StrategyGraph graph={graph} jobTitle="AI Agent 平台工程师" />);

    const node = screen.getByTestId(
      "strategy-node-round:1:phase:source_result:liepin",
    );
    fireEvent.click(node);

    expect(node).not.toHaveAttribute("data-selected");
    expect(
      screen.queryByRole("complementary", { name: /节点详情/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: /节点详情/ }),
    ).not.toBeInTheDocument();
  });
});
