import ELK from 'elkjs/lib/elk.bundled.js';
import type { ELK as ElkInstance, ElkNode } from 'elkjs/lib/elk.bundled.js';
import { Position, type Edge, type Node } from '@xyflow/react';

import type { RecruiterGraphEdge, RecruiterGraphNode, RecruiterLane } from './recruiterAnimation';

export type StrategyGraphNodeData = {
  graphNode: RecruiterGraphNode;
  selected: boolean;
  onSelectNode?: (node: RecruiterGraphNode) => void;
};
export type StrategyGraphEdgeData = { graphEdge: RecruiterGraphEdge };
export type StrategyFlowNode = Node<StrategyGraphNodeData, 'strategy'>;
export type StrategyFlowEdge = Edge<StrategyGraphEdgeData>;
export type LaidOutStrategyGraph = { nodes: StrategyFlowNode[]; edges: StrategyFlowEdge[] };
export type StrategyGraphLayoutRunner = (graph: ElkNode) => Promise<ElkNode>;

type GraphBounds = { width: number; height: number };
type GraphPosition = { x: number; y: number };
type ManualPositionMergeInput = {
  current: Map<string, GraphPosition>;
  manual: Map<string, GraphPosition>;
  currentGraphIdentity: string;
  nextGraphIdentity: string;
  nextNodeIds: string[];
};
type ManualPositionMergeResult = {
  positions: Map<string, GraphPosition>;
  manualPositions: Map<string, GraphPosition>;
};

export const NODE_WIDTH = 168;
export const NODE_HEIGHT = 74;

const LANE_Y_RATIOS: Record<RecruiterLane, number> = {
  shared: 0.42,
  cts: 0.22,
  liepin: 0.62,
};

const ROOT_ID = 'strategy-root';
const START_NODE_IDS = new Set(['start', 'job']);
const FINAL_SHORTLIST_ID = 'final-shortlist';
const GRAPH_INSET = 34;
const CTS_ROUND_START_X = GRAPH_INSET + 230;
const CTS_ROUND_COLUMN_GAP = 192;
const CTS_ROUND_ROW_GAP = 132;
const COLLISION_GAP = 18;
let elkInstance: ElkInstance | null = null;
let testLayoutRunner: StrategyGraphLayoutRunner | null = null;

export function setStrategyGraphLayoutRunnerForTests(runner: StrategyGraphLayoutRunner | null) {
  testLayoutRunner = runner;
}

export function disposeStrategyGraphLayoutRunner() {
  elkInstance?.terminateWorker();
  elkInstance = null;
  testLayoutRunner = null;
}

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
    const laidOut = await runElkLayout(toElkGraph(nodes, edges));
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

function runElkLayout(graph: ElkNode): Promise<ElkNode> {
  if (testLayoutRunner) {
    return testLayoutRunner(graph);
  }
  elkInstance ??= new ELK();
  return elkInstance.layout(graph) as Promise<ElkNode>;
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
  const hasCtsRoundNodes = nodes.some((node) => ctsRoundPosition(node, bounds, hasMultipleSourceLanes));
  const maxX = Math.max(
    1,
    ...nodes.map((node) => rawPositions.get(node.id)?.x ?? percentPosition(node, bounds).x),
  );
  const viewportRightX = Math.max(GRAPH_INSET, bounds.width - NODE_WIDTH - GRAPH_INSET);
  const ctsFlowRightX = CTS_ROUND_START_X + CTS_ROUND_COLUMN_GAP * 3 + NODE_WIDTH + GRAPH_INSET;
  const rightX = hasCtsRoundNodes ? Math.max(viewportRightX, ctsFlowRightX) : viewportRightX;
  const availableWidth = Math.max(1, rightX - GRAPH_INSET);
  const maxY = Math.max(GRAPH_INSET, bounds.height - NODE_HEIGHT - GRAPH_INSET);
  const positions = new Map<string, GraphPosition>();

  for (const node of nodes) {
    const anchorPosition = anchorNodePosition(node, bounds, rightX);
    if (anchorPosition) {
      positions.set(node.id, anchorPosition);
      continue;
    }

    const ctsPosition = ctsRoundPosition(node, bounds, hasMultipleSourceLanes);
    if (ctsPosition) {
      positions.set(node.id, ctsPosition);
      continue;
    }

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

  return separateOverlappingNodes(positions, nodes);
}

export function mergeManualNodePositions(input: ManualPositionMergeInput): ManualPositionMergeResult {
  if (input.currentGraphIdentity !== input.nextGraphIdentity) {
    return {
      positions: new Map(input.current),
      manualPositions: new Map(),
    };
  }

  const nextNodeIds = new Set(input.nextNodeIds);
  const manualPositions = new Map(
    [...input.manual.entries()].filter(([nodeId]) => nextNodeIds.has(nodeId)),
  );
  const positions = new Map(input.current);

  for (const [nodeId, position] of manualPositions) {
    positions.set(nodeId, position);
  }

  return { positions, manualPositions };
}

function anchorNodePosition(
  node: RecruiterGraphNode,
  bounds: GraphBounds,
  rightX: number,
): GraphPosition | null {
  if (START_NODE_IDS.has(node.id)) {
    return {
      x: GRAPH_INSET,
      y: verticalCenter(bounds),
    };
  }

  if (node.id === FINAL_SHORTLIST_ID) {
    return {
      x: rightX,
      y: verticalCenter(bounds),
    };
  }

  return null;
}

function verticalCenter(bounds: GraphBounds): number {
  return Math.max(GRAPH_INSET, (bounds.height - NODE_HEIGHT) / 2);
}

function separateOverlappingNodes(
  positions: Map<string, GraphPosition>,
  nodes: RecruiterGraphNode[],
): Map<string, GraphPosition> {
  const orderedNodes = [...nodes].sort((left, right) => {
    const leftAnchored = isAnchorNode(left);
    const rightAnchored = isAnchorNode(right);
    if (leftAnchored !== rightAnchored) {
      return leftAnchored ? -1 : 1;
    }
    const leftPosition = positions.get(left.id);
    const rightPosition = positions.get(right.id);
    return (leftPosition?.x ?? 0) - (rightPosition?.x ?? 0) || (leftPosition?.y ?? 0) - (rightPosition?.y ?? 0);
  });
  const separated = new Map<string, GraphPosition>();

  for (const node of orderedNodes) {
    const position = positions.get(node.id);
    if (!position) {
      continue;
    }
    if (isAnchorNode(node)) {
      separated.set(node.id, position);
      continue;
    }

    let nextPosition = position;
    while ([...separated.values()].some((placed) => rectanglesOverlap(nextPosition, placed))) {
      nextPosition = {
        x: nextPosition.x,
        y: nextPosition.y + NODE_HEIGHT + COLLISION_GAP,
      };
    }
    separated.set(node.id, nextPosition);
  }

  return separated;
}

function isAnchorNode(node: RecruiterGraphNode): boolean {
  return START_NODE_IDS.has(node.id) || node.id === FINAL_SHORTLIST_ID;
}

function rectanglesOverlap(left: GraphPosition, right: GraphPosition): boolean {
  return (
    left.x < right.x + NODE_WIDTH &&
    left.x + NODE_WIDTH > right.x &&
    left.y < right.y + NODE_HEIGHT &&
    left.y + NODE_HEIGHT > right.y
  );
}

function ctsRoundPosition(
  node: RecruiterGraphNode,
  bounds: GraphBounds,
  hasMultipleSourceLanes: boolean,
): GraphPosition | null {
  const match = /^cts-round-(\d+)-(query|result|score|reflect)$/.exec(node.id);
  if (!match) {
    return null;
  }
  const roundNo = Number(match[1] ?? '1');
  const stage = (match[2] ?? 'query') as 'query' | 'result' | 'score' | 'reflect';
  const stageIndex = { query: 0, result: 1, score: 2, reflect: 3 }[stage];
  const firstRowY = hasMultipleSourceLanes ? LANE_Y_RATIOS.cts * bounds.height : GRAPH_INSET + 96;
  return {
    x: CTS_ROUND_START_X + stageIndex * CTS_ROUND_COLUMN_GAP,
    y: Math.max(GRAPH_INSET, firstRowY + Math.max(0, roundNo - 1) * CTS_ROUND_ROW_GAP),
  };
}

function flowNodes(nodes: RecruiterGraphNode[], positions: Map<string, GraphPosition>): StrategyFlowNode[] {
  return nodes.map((node) => ({
    id: node.id,
    type: 'strategy',
    position: positions.get(node.id) ?? { x: GRAPH_INSET, y: GRAPH_INSET },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    style: { width: NODE_WIDTH, height: NODE_HEIGHT },
    data: { graphNode: node, selected: false },
    draggable: true,
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
