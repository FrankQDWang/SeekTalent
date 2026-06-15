import ELK from "elkjs/lib/elk.bundled.js";
import type { AgentStrategyGraph } from "./graphProjection";

export type { AgentStrategyGraph } from "./graphProjection";

export type StrategyLayoutPosition = {
  x: number;
  y: number;
};

const elk = new ELK();
const layoutCache = new Map<
  string,
  ReadonlyMap<string, StrategyLayoutPosition>
>();
const nodeWidth = 232;
const nodeHeight = 96;

export function strategyGraphSignature(graph: AgentStrategyGraph): string {
  return JSON.stringify({
    nodes: graph.nodes.map((node) => [
      node.nodeId,
      node.kind,
      node.status,
      node.sourceKind,
    ]),
    edges: graph.edges.map((edge) => [
      edge.edgeId,
      edge.fromNodeId,
      edge.toNodeId,
      edge.status,
    ]),
  });
}

export async function layoutStrategyGraph(
  graph: AgentStrategyGraph,
): Promise<ReadonlyMap<string, StrategyLayoutPosition>> {
  const signature = strategyGraphSignature(graph);
  const cached = layoutCache.get(signature);
  if (cached) {
    return cached;
  }

  const elkGraph = await elk.layout({
    id: "strategy-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "88",
      "elk.layered.spacing.edgeNodeBetweenLayers": "40",
      "elk.spacing.nodeNode": "56",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: graph.nodes.map((node) => ({
      id: node.nodeId,
      width: nodeWidth,
      height: nodeHeight,
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.edgeId,
      sources: [edge.fromNodeId],
      targets: [edge.toNodeId],
    })),
  });

  const positions = new Map<string, StrategyLayoutPosition>();
  for (const child of elkGraph.children ?? []) {
    if (typeof child.x !== "number" || typeof child.y !== "number") {
      throw new Error(
        `ELK did not return a position for strategy node ${child.id}`,
      );
    }
    positions.set(child.id, { x: child.x, y: child.y });
  }

  layoutCache.set(signature, positions);
  return positions;
}
