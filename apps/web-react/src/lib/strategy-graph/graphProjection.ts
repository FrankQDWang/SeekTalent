import type {
  AgentWorkbenchGraphEdge,
  AgentWorkbenchGraphNode,
} from "../api/agentWorkbenchTypes";

export type AgentGraphNode = AgentWorkbenchGraphNode;
export type AgentGraphEdge = AgentWorkbenchGraphEdge & {
  status?: string | null;
};

export type AgentStrategyGraph = {
  nodes: AgentGraphNode[];
  edges: AgentGraphEdge[];
};

export type StrategyTimelineNode = {
  node: AgentGraphNode;
  displayTitle: string;
  metadata: string[];
  x: number;
  y: number;
  width: number;
  height: number;
};

export type StrategyTimelineEdge = {
  edge: AgentGraphEdge;
  path: string;
};

export type StrategyTimelineProjection = {
  nodes: StrategyTimelineNode[];
  edges: StrategyTimelineEdge[];
  width: number;
  height: number;
};

export type StrategyTimelineProjectionOptions = {
  reserveRootColumn?: boolean;
};

const NODE_WIDTH = 210;
const NODE_HEIGHT = 90;
const ROUND_Y_START = 64;
const ROUND_Y_GAP = 168;
const START_X = 28;
const REQUIREMENTS_X_WITH_ROOT = 340;
const ROUND_STAGE_START_X = 638;
const STAGE_X_GAP = 286;
const CANVAS_PADDING = 36;
const NEXT_ROUND_LOOP_LEFT_GAP = 52;
const NEXT_ROUND_LOOP_TOP_GAP = 24;

const stageOrder = new Map<string, number>([
  ["round_query", 0],
  ["query", 0],
  ["source_result", 1],
  ["source", 1],
  ["merge", 2],
  ["dedupe", 2],
  ["scoring", 3],
  ["top_pool", 3],
  ["feedback", 4],
  ["reflection", 4],
  ["final_summary", 5],
  ["final", 5],
]);

export function projectStrategyTimelineGraph(
  graph: AgentStrategyGraph,
  options: StrategyTimelineProjectionOptions = {},
): StrategyTimelineProjection {
  const rootNodes = graph.nodes.filter((node) => node.roundNo == null);
  const requirements = rootNodes.find((node) => node.kind === "requirements");
  const messageRoot = rootNodes.find((node) => node.kind === "message");
  const reserveRootColumn = options.reserveRootColumn === true || !!messageRoot;
  const rounds = sortedRoundNos(graph.nodes);
  const visibleNodes: StrategyTimelineNode[] = [];

  const firstRoundY = ROUND_Y_START;
  if (messageRoot) {
    visibleNodes.push(timelineNode(messageRoot, START_X, firstRoundY + 96));
  }
  if (requirements) {
    visibleNodes.push(
      timelineNode(
        requirements,
        reserveRootColumn ? REQUIREMENTS_X_WITH_ROOT : START_X,
        firstRoundY,
      ),
    );
  }

  for (const [roundIndex, roundNo] of rounds.entries()) {
    const roundNodes = graph.nodes.filter((node) => node.roundNo === roundNo);
    const stages = visibleRoundNodes(roundNodes);
    const y = ROUND_Y_START + roundIndex * ROUND_Y_GAP;
    for (const [stageIndex, node] of stages.entries()) {
      visibleNodes.push(
        timelineNode(node, ROUND_STAGE_START_X + stageIndex * STAGE_X_GAP, y),
      );
    }
  }

  const positioned = new Map(
    visibleNodes.map((item) => [item.node.nodeId, item]),
  );
  const visibleEdges = graph.edges.flatMap((edge) => {
    const from = positioned.get(edge.fromNodeId);
    const to = positioned.get(edge.toNodeId);
    if (!from || !to) {
      return [];
    }
    return [{ edge, path: edgePath(from, to) }];
  });

  const extents = visibleNodes.reduce(
    (bounds, node) => ({
      width: Math.max(bounds.width, node.x + node.width + CANVAS_PADDING),
      height: Math.max(bounds.height, node.y + node.height + CANVAS_PADDING),
    }),
    { width: 960, height: 420 },
  );
  return {
    nodes: visibleNodes,
    edges: visibleEdges,
    width: extents.width,
    height: extents.height,
  };
}

function sortedRoundNos(nodes: AgentGraphNode[]): number[] {
  return Array.from(
    new Set(
      nodes
        .map((node) => node.roundNo)
        .filter((roundNo): roundNo is number => typeof roundNo === "number"),
    ),
  ).sort((left, right) => left - right);
}

function visibleRoundNodes(nodes: AgentGraphNode[]): AgentGraphNode[] {
  const phaseNodes = nodes
    .filter((node) => node.kind === "phase" || node.kind === "final")
    .sort(compareRoundStageNodes);
  if (phaseNodes.length > 0) {
    return phaseNodes;
  }

  const laneNodes = nodes
    .filter((node) => node.kind === "lane")
    .sort(compareRoundStageNodes);
  if (laneNodes.length > 0) {
    return laneNodes;
  }

  return nodes.filter((node) => node.kind === "round");
}

function compareRoundStageNodes(left: AgentGraphNode, right: AgentGraphNode) {
  return (
    stageRank(left) - stageRank(right) ||
    left.nodeId.localeCompare(right.nodeId)
  );
}

function stageRank(node: AgentGraphNode): number {
  return (
    stageOrder.get(node.stage ?? "") ?? stageOrder.get(node.phase ?? "") ?? 99
  );
}

function timelineNode(
  node: AgentGraphNode,
  x: number,
  y: number,
): StrategyTimelineNode {
  return {
    node,
    displayTitle: displayTitle(node),
    metadata: metadata(node),
    x,
    y,
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  };
}

function displayTitle(node: AgentGraphNode): string {
  if (node.kind === "message") {
    return node.label;
  }
  if (node.kind === "requirements") {
    return "需求拆解";
  }
  if (node.roundNo == null) {
    return node.label;
  }
  const roundLabel = `第 ${String(node.roundNo)} 轮`;
  const stage = node.stage ?? node.phase ?? "";
  if (stage === "round_query" || stage === "query") {
    return `${roundLabel} · 查询包`;
  }
  if (stage === "source_result" || stage === "source") {
    return `${roundLabel} · ${sourceTitle(node.sourceKind)}检索`;
  }
  if (stage === "merge" || stage === "dedupe") {
    return `${roundLabel} · 去重合并`;
  }
  if (stage === "scoring" || stage === "top_pool") {
    return `${roundLabel} · Top Pool`;
  }
  if (stage === "feedback" || stage === "reflection") {
    return `${roundLabel} · 下一轮策略`;
  }
  if (stage === "final_summary" || stage === "final") {
    return "最终短名单";
  }
  if (node.kind === "lane") {
    return `${roundLabel} · ${sourceTitle(node.sourceKind)}${node.laneType ?? "通道"}`;
  }
  return node.label;
}

function metadata(node: AgentGraphNode): string[] {
  return [
    statusLabel(node.status),
    node.sourceKind === "liepin" ? "猎聘" : null,
    node.sourceKind === "cts" ? "CTS 实验" : null,
  ].filter((item): item is string => item !== null);
}

function sourceTitle(sourceKind: AgentGraphNode["sourceKind"]): string {
  if (sourceKind === "liepin") {
    return "猎聘";
  }
  if (sourceKind === "cts") {
    return "CTS";
  }
  return "";
}

function edgePath(
  from: StrategyTimelineNode,
  to: StrategyTimelineNode,
): string {
  if (isNextRoundLoopEdge(from.node, to.node)) {
    return nextRoundLoopPath(from, to);
  }

  const startX = from.x + from.width;
  const startY = from.y + from.height / 2;
  const endX = to.x;
  const endY = to.y + to.height / 2;
  if (Math.abs(startY - endY) < 4 && endX > startX) {
    return ["M", startX, startY, "H", endX].map(String).join(" ");
  }
  const elbowX = Math.max(startX + 36, Math.min(startX + 132, endX - 48));
  return ["M", startX, startY, "H", elbowX, "V", endY, "H", endX]
    .map(String)
    .join(" ");
}

function isNextRoundLoopEdge(
  from: AgentGraphNode,
  to: AgentGraphNode,
): boolean {
  return (
    typeof from.roundNo === "number" &&
    typeof to.roundNo === "number" &&
    to.roundNo === from.roundNo + 1 &&
    isFeedbackStage(from) &&
    isQueryStage(to)
  );
}

function isFeedbackStage(node: AgentGraphNode): boolean {
  const stage = node.stage ?? node.phase ?? "";
  return stage === "feedback" || stage === "reflection";
}

function isQueryStage(node: AgentGraphNode): boolean {
  const stage = node.stage ?? node.phase ?? "";
  return stage === "round_query" || stage === "query";
}

function nextRoundLoopPath(
  from: StrategyTimelineNode,
  to: StrategyTimelineNode,
): string {
  const startX = from.x + from.width / 2;
  const endX = to.x;
  const endY = to.y + to.height / 2;
  const leftX = Math.max(CANVAS_PADDING, endX - NEXT_ROUND_LOOP_LEFT_GAP);
  const gapTop = from.y + from.height;
  const gapBottom = to.y;
  const routeY =
    gapBottom > gapTop
      ? Math.max(gapTop + 12, gapBottom - NEXT_ROUND_LOOP_TOP_GAP)
      : gapTop;

  return ["M", startX, routeY, "H", leftX, "V", endY, "H", endX]
    .map(String)
    .join(" ");
}

export function statusLabel(status: string): string {
  if (status === "completed") {
    return "已完成";
  }
  if (status === "running") {
    return "运行中";
  }
  if (status === "blocked") {
    return "已阻塞";
  }
  if (status === "partial") {
    return "部分完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "cancelled") {
    return "已取消";
  }
  return "待开始";
}
