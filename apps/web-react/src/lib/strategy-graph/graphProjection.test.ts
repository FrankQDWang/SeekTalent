import { describe, expect, it } from "vitest";
import {
  projectStrategyGraph,
  type AgentStrategyGraph,
} from "./graphProjection";

const strategyGraph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求拆解",
      summary: "已确认岗位要求",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round_1_query",
      kind: "activity",
      label: "第 1 轮 · 查询包",
      summary: "第 1 轮查询策略已生成",
      status: "running",
      sourceKind: "liepin",
      activityId: "activity_round_1_query",
    },
  ],
  edges: [
    {
      edgeId: "requirements->round_1_query",
      fromNodeId: "requirements",
      toNodeId: "round_1_query",
      label: "生成检索策略",
    },
  ],
};

describe("projectStrategyGraph", () => {
  it("keeps stable BFF node and edge ids", () => {
    expect.hasAssertions();

    const positions = new Map([
      ["requirements", { x: 24, y: 32 }],
      ["round_1_query", { x: 320, y: 32 }],
    ]);

    const projected = projectStrategyGraph(strategyGraph, positions);

    expect(projected.nodes.map((node) => node.id)).toEqual([
      "requirements",
      "round_1_query",
    ]);
    expect(projected.edges.map((edge) => edge.id)).toEqual([
      "requirements->round_1_query",
    ]);
    expect(projected.edges[0]).toMatchObject({
      source: "requirements",
      target: "round_1_query",
      label: "生成检索策略",
    });
  });

  it("throws when ELK layout positions are missing", () => {
    expect.hasAssertions();

    expect(() => projectStrategyGraph(strategyGraph, new Map())).toThrow(
      /Missing layout position for strategy node requirements/,
    );
  });
});
