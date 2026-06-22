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
      nodeId: "round:1",
      kind: "round",
      label: "Round 1",
      summary: "AI agent platform engineer",
      status: "running",
      roundNo: 1,
      phase: "round",
      stage: "round_summary",
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
    expect(projected.rounds).toEqual([
      expect.objectContaining({
        label: "第 1 轮",
        roundNo: 1,
        state: "active",
      }),
    ]);
    expect(projected.activeLabel).toBe("第 1 轮检索中");
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
});
