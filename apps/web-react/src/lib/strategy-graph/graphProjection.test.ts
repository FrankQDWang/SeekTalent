import { describe, expect, it } from "vitest";
import {
  projectStrategyTimelineGraph,
  type AgentStrategyGraph,
} from "./graphProjection";

const strategyGraph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求确认",
      summary: "已确认岗位要求",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:round_query:all",
      kind: "phase",
      label: "round_query",
      summary: "第 1 轮查询策略已生成。",
      status: "completed",
      roundNo: 1,
      phase: "query",
      stage: "round_query",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:source_result:liepin",
      kind: "phase",
      label: "liepin source_result",
      summary: "猎聘返回 16 份安全摘要。",
      status: "running",
      roundNo: 1,
      phase: "source",
      stage: "source_result",
      sourceKind: "liepin",
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

const multiRoundGraph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "round:1:phase:round_query:all",
      kind: "phase",
      label: "round_query",
      roundNo: 1,
      stage: "round_query",
      summary: "第 1 轮查询策略已生成。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:source_result:liepin",
      kind: "phase",
      label: "source_result",
      roundNo: 1,
      stage: "source_result",
      summary: "猎聘返回候选人。",
      status: "completed",
      sourceKind: "liepin",
    },
    {
      nodeId: "round:1:phase:scoring:all",
      kind: "phase",
      label: "scoring",
      roundNo: 1,
      stage: "scoring",
      summary: "Top Pool 已更新。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:1:phase:feedback:all",
      kind: "phase",
      label: "feedback",
      roundNo: 1,
      stage: "feedback",
      summary: "准备下一轮策略。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round:2:phase:round_query:all",
      kind: "phase",
      label: "round_query",
      roundNo: 2,
      stage: "round_query",
      summary: "第 2 轮查询策略已生成。",
      status: "pending",
      sourceKind: "all",
    },
  ],
  edges: [
    {
      edgeId: "round:1:phase:feedback:all->round:2:phase:round_query:all",
      fromNodeId: "round:1:phase:feedback:all",
      label: "下一轮策略",
      toNodeId: "round:2:phase:round_query:all",
    },
  ],
};

describe("projectStrategyTimelineGraph", () => {
  it("projects BFF nodes into the WTS round-stage timeline without CTS by default", () => {
    expect.hasAssertions();

    const projected = projectStrategyTimelineGraph(strategyGraph);

    expect(projected.nodes.map((node) => node.displayTitle)).toEqual([
      "需求拆解",
      "第 1 轮 · 查询包",
      "第 1 轮 · 猎聘检索",
    ]);
    expect(projected.nodes[2]?.metadata).toEqual(["运行中", "猎聘"]);
    expect(projected).not.toHaveProperty("rounds");
    expect(projected).not.toHaveProperty("activeLabel");
    expect(projected).not.toHaveProperty("progressPercent");
    expect(projected.nodes.some((node) => node.node.sourceKind === "cts")).toBe(
      false,
    );
  });

  it("only draws edges whose endpoints are visible timeline nodes", () => {
    expect.hasAssertions();

    const projected = projectStrategyTimelineGraph(strategyGraph);

    expect(projected.edges.map((edge) => edge.edge.edgeId)).toEqual([
      "requirements->round:1:phase:round_query:all",
      "round:1:phase:round_query:all->round:1:phase:source_result:liepin",
    ]);
    expect(projected.edges[0]?.path).toContain("H");
  });

  it("reserves a root column when the workbench renders a job root card", () => {
    expect.hasAssertions();

    const projected = projectStrategyTimelineGraph(strategyGraph, {
      reserveRootColumn: true,
    });

    expect(projected.nodes[0]?.displayTitle).toBe("需求拆解");
    expect(projected.nodes[0]?.x).toBe(340);
    expect(projected.edges[0]?.path).toBe("M 550 109 H 638");
  });

  it("routes next-round feedback edges as the designer outside loop", () => {
    expect.hasAssertions();

    const projected = projectStrategyTimelineGraph(multiRoundGraph);

    expect(projected.edges[0]?.path).toBe("M 1601 208 H 586 V 277 H 638");
  });
});
