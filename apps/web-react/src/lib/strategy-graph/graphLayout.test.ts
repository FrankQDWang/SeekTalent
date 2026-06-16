import { describe, expect, it, vi } from "vitest";
import {
  layoutStrategyGraph,
  strategyGraphSignature,
  type AgentStrategyGraph,
} from "./graphLayout";

type MockElkGraph = {
  children?: Array<{ id: string }>;
  layoutOptions?: { [key: string]: string };
};

type MockElkLayout = {
  id: string;
  children?: Array<{ id: string; x: number; y: number }>;
};

const layoutMock = vi.hoisted(() =>
  vi.fn((graph: MockElkGraph): Promise<MockElkLayout> => {
    const children = graph.children?.map((child, index) => ({
      id: child.id,
      x: 11 + index * 37,
      y: 19 + index * 41,
    }));

    return Promise.resolve(
      children === undefined
        ? { id: "strategy-root" }
        : { id: "strategy-root", children },
    );
  }),
);

vi.mock("elkjs/lib/elk.bundled.js", () => ({
  default: vi.fn().mockImplementation(function Elk() {
    return {
      layout: layoutMock,
    };
  }),
}));

const graph: AgentStrategyGraph = {
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
    },
  ],
  edges: [
    {
      edgeId: "requirements->round_1_query",
      fromNodeId: "requirements",
      toNodeId: "round_1_query",
    },
  ],
};

describe("strategyGraphSignature", () => {
  it("uses stable topology and status fields instead of display text", () => {
    expect.hasAssertions();

    const renamedGraph: AgentStrategyGraph = {
      ...graph,
      nodes: graph.nodes.map((node) => ({
        ...node,
        label: `${node.label} changed`,
      })),
    };

    expect(strategyGraphSignature(renamedGraph)).toBe(
      strategyGraphSignature(graph),
    );
  });
});

describe("layoutStrategyGraph", () => {
  it("uses ELK layered layout and returns ELK positions", async () => {
    expect.hasAssertions();

    const positions = await layoutStrategyGraph(graph);

    const layoutInput = layoutMock.mock.calls[0]?.[0];
    expect(layoutInput?.layoutOptions?.["elk.algorithm"]).toBe("layered");
    expect(layoutInput?.layoutOptions?.["elk.direction"]).toBe("RIGHT");
    expect(positions.get("requirements")).toEqual({ x: 11, y: 19 });
    expect(positions.get("round_1_query")).toEqual({ x: 48, y: 60 });
  });

  it("caches layout by graph signature", async () => {
    expect.hasAssertions();

    const cacheGraph: AgentStrategyGraph = {
      ...graph,
      nodes: graph.nodes.map((node) => ({
        ...node,
        nodeId: `cache_${node.nodeId}`,
      })),
      edges: [
        {
          edgeId: "cache_requirements->cache_round_1_query",
          fromNodeId: "cache_requirements",
          toNodeId: "cache_round_1_query",
        },
      ],
    };

    layoutMock.mockClear();
    const firstPositions = await layoutStrategyGraph(cacheGraph);
    const cachedPositions = await layoutStrategyGraph({
      ...cacheGraph,
      nodes: cacheGraph.nodes.map((node) => ({
        ...node,
        summary: `${node.summary} updated`,
      })),
    });

    expect(cachedPositions).toBe(firstPositions);
    expect(layoutMock).toHaveBeenCalledTimes(1);
  });
});
