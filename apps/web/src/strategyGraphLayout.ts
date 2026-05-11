import ELK from 'elkjs/lib/elk.bundled.js';
import type { ElkNode } from 'elkjs/lib/elk.bundled.js';
import { Position, type Edge, type Node } from '@xyflow/react';

import type { RecruiterGraphEdge, RecruiterGraphNode, RecruiterLane } from './recruiterAnimation';

export type StrategyGraphNodeData = { graphNode: RecruiterGraphNode; selected: boolean };
export type StrategyGraphEdgeData = { graphEdge: RecruiterGraphEdge };
export type StrategyFlowNode = Node<StrategyGraphNodeData, 'strategy'>;
export type StrategyFlowEdge = Edge<StrategyGraphEdgeData>;
export type LaidOutStrategyGraph = { nodes: StrategyFlowNode[]; edges: StrategyFlowEdge[] };

type GraphBounds = { width: number; height: number };
type GraphPosition = { x: number; y: number };

export const NODE_WIDTH = 168;
export const NODE_HEIGHT = 74;

const LANE_Y_RATIOS: Record<RecruiterLane, number> = {
  shared: 0.42,
  cts: 0.22,
  liepin: 0.62,
};

const ROOT_ID = 'strategy-root';
const FINAL_SHORTLIST_ID = 'final-shortlist';
const GRAPH_INSET = 34;

export function toElkGraph(nodes: RecruiterGraphNode[], edges: RecruiterGraphEdge[]): ElkNode {
  return {
    id: ROOT_ID,
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.spacing.nodeNode': '42',
      'elk.layered.spacing.nodeNodeBetweenLayers': '62',
      'elk.edgeRouting': 'ORTHOGONAL',
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: edges.map((edge) => ({
      id: edgeId(edge),
      sources: [edge.from],
      targets: [edge.to],
    })),
  };
}

export async function layoutStrategyGraph(
  nodes: RecruiterGraphNode[],
  edges: RecruiterGraphEdge[],
  bounds: GraphBounds,
): Promise<LaidOutStrategyGraph> {
  try {
    const elk = new ELK();
    const laidOut = await elk.layout(toElkGraph(nodes, edges));
    const rawPositions = new Map<string, GraphPosition>();

    for (const child of laidOut.children ?? []) {
      if (typeof child.x === 'number' && typeof child.y === 'number') {
        rawPositions.set(child.id, { x: child.x, y: child.y });
      }
    }

    if (rawPositions.size === 0) {
      return fallbackLayout(nodes, edges, bounds);
    }

    return {
      nodes: flowNodes(nodes, stackLanePositions(rawPositions, nodes, bounds)),
      edges: flowEdges(edges),
    };
  } catch {
    return fallbackLayout(nodes, edges, bounds);
  }
}

export function fallbackLayout(
  nodes: RecruiterGraphNode[],
  edges: RecruiterGraphEdge[],
  bounds: GraphBounds,
): LaidOutStrategyGraph {
  const rawPositions = new Map(nodes.map((node) => [node.id, percentPosition(node, bounds)]));

  return {
    nodes: flowNodes(nodes, stackLanePositions(rawPositions, nodes, bounds)),
    edges: flowEdges(edges),
  };
}

export function stackLanePositions(
  rawPositions: Map<string, GraphPosition>,
  nodes: RecruiterGraphNode[],
  bounds: GraphBounds,
): Map<string, GraphPosition> {
  const hasCts = nodes.some((node) => node.lane === 'cts');
  const hasLiepin = nodes.some((node) => node.lane === 'liepin');
  const hasMultipleSourceLanes = hasCts && hasLiepin;
  const maxX = Math.max(
    1,
    ...nodes.map((node) => rawPositions.get(node.id)?.x ?? percentPosition(node, bounds).x),
  );
  const rightX = Math.max(GRAPH_INSET, bounds.width - NODE_WIDTH - GRAPH_INSET);
  const availableWidth = Math.max(1, rightX - GRAPH_INSET);
  const maxY = Math.max(GRAPH_INSET, bounds.height - NODE_HEIGHT - GRAPH_INSET);
  const positions = new Map<string, GraphPosition>();

  for (const node of nodes) {
    const rawPosition = rawPositions.get(node.id) ?? percentPosition(node, bounds);
    const lane = node.lane ?? 'shared';
    const scaledX =
      node.id === FINAL_SHORTLIST_ID
        ? rightX
        : GRAPH_INSET + (rawPosition.x / maxX) * availableWidth;
    const stackedY = hasMultipleSourceLanes ? LANE_Y_RATIOS[lane] * bounds.height : rawPosition.y;

    positions.set(node.id, {
      x: clamp(scaledX, GRAPH_INSET, rightX),
      y: clamp(stackedY, GRAPH_INSET, maxY),
    });
  }

  return positions;
}

function flowNodes(nodes: RecruiterGraphNode[], positions: Map<string, GraphPosition>): StrategyFlowNode[] {
  return nodes.map((node) => ({
    id: node.id,
    type: 'strategy',
    position: positions.get(node.id) ?? { x: GRAPH_INSET, y: GRAPH_INSET },
    data: { graphNode: node, selected: false },
    draggable: false,
    selected: false,
    selectable: true,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  }));
}

function flowEdges(edges: RecruiterGraphEdge[]): StrategyFlowEdge[] {
  return edges.map((edge) => ({
    id: edgeId(edge),
    source: edge.from,
    target: edge.to,
    type: 'smoothstep',
    label: edge.label,
    data: { graphEdge: edge },
    className: `strategy-flow-edge ${edge.tone}`,
  }));
}

function percentPosition(node: RecruiterGraphNode, bounds: GraphBounds): GraphPosition {
  const xRatio = normalizePercent(node.x);
  const yRatio = normalizePercent(node.y);

  return {
    x: xRatio * Math.max(0, bounds.width - NODE_WIDTH),
    y: yRatio * Math.max(0, bounds.height - NODE_HEIGHT),
  };
}

function edgeId(edge: RecruiterGraphEdge): string {
  return `${edge.from}->${edge.to}`;
}

function normalizePercent(value: number): number {
  return value <= 1 ? value : value / 100;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
