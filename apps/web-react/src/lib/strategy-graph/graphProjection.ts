import type { Edge, Node } from "@xyflow/react";
import type { StrategyLayoutPosition } from "./graphLayout";

export type AgentGraphNode = {
  nodeId: string;
  kind: string;
  label: string;
  summary: string;
  status: string;
  sourceKind: "cts" | "liepin" | "all";
  activityId?: string | null;
  messageId?: string | null;
};

export type AgentGraphEdge = {
  edgeId: string;
  fromNodeId: string;
  toNodeId: string;
  status?: string | null;
  label?: string | null;
};

export type AgentStrategyGraph = {
  nodes: AgentGraphNode[];
  edges: AgentGraphEdge[];
};

export type StrategyFlowNode = Node<AgentGraphNode, "strategy">;
export type StrategyFlowEdge = Edge<AgentGraphEdge>;

export function projectStrategyGraph(
  graph: AgentStrategyGraph,
  positions: ReadonlyMap<string, StrategyLayoutPosition>,
): {
  nodes: StrategyFlowNode[];
  edges: StrategyFlowEdge[];
} {
  return {
    nodes: graph.nodes.map((node) => {
      const position = positions.get(node.nodeId);
      if (!position) {
        throw new Error(
          `Missing layout position for strategy node ${node.nodeId}`,
        );
      }

      return {
        id: node.nodeId,
        type: "strategy",
        position,
        data: node,
        draggable: false,
        focusable: true,
        selectable: true,
        ariaLabel: `${node.label}: ${node.summary}`,
        ariaRole: "button",
      };
    }),
    edges: graph.edges.map((edge) => ({
      id: edge.edgeId,
      source: edge.fromNodeId,
      target: edge.toNodeId,
      label: edge.label ?? undefined,
      data: edge,
      type: "smoothstep",
      animated: edge.status === "active" || edge.status === "running",
    })),
  };
}
