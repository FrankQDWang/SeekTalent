import ELK from 'elkjs/lib/elk.bundled.js';
import type { ELK as ElkInstance, ElkNode } from 'elkjs/lib/elk.bundled.js';
import { Position, type Edge, type Node } from '@xyflow/svelte';

import type { RecruiterGraphEdge, RecruiterGraphNode, RecruiterLane } from './recruiterAnimation';

export type StrategyGraphNodeData = Record<string, unknown> & {
	graphNode: RecruiterGraphNode;
	selected: boolean;
	onSelectNode?: (node: RecruiterGraphNode) => void;
};
export type StrategyGraphEdgeData = Record<string, unknown> & { graphEdge: RecruiterGraphEdge };
export type StrategyFlowNode = Node<StrategyGraphNodeData, 'strategy'>;
export type StrategyFlowEdge = Edge<StrategyGraphEdgeData>;
export type LaidOutStrategyGraph = {
	nodes: StrategyFlowNode[];
	edges: StrategyFlowEdge[];
	contentWidth?: number;
	contentHeight?: number;
};
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

export const NODE_WIDTH = 212;
export const NODE_HEIGHT = 96;

const LANE_Y_RATIOS: Record<RecruiterLane, number> = {
	shared: 0.42,
	cts: 0.22,
	liepin: 0.62
};

const ROOT_ID = 'strategy-root';
const START_NODE_IDS = new Set(['start', 'job']);
const FINAL_SHORTLIST_ID = 'final-shortlist';
const GRAPH_INSET = 34;
const BUSINESS_COLUMN_GAP = 42;
const BUSINESS_ROW_GAP = 28;
const BUSINESS_STAGE_STEP = NODE_WIDTH + BUSINESS_COLUMN_GAP;
const BUSINESS_STAGE_X = {
	start: GRAPH_INSET,
	requirements: GRAPH_INSET + BUSINESS_STAGE_STEP,
	query: GRAPH_INSET + BUSINESS_STAGE_STEP * 2,
	source: GRAPH_INSET + BUSINESS_STAGE_STEP * 3,
	merge: GRAPH_INSET + BUSINESS_STAGE_STEP * 4,
	score: GRAPH_INSET + BUSINESS_STAGE_STEP * 5,
	feedback: GRAPH_INSET + BUSINESS_STAGE_STEP * 6,
	final: GRAPH_INSET + BUSINESS_STAGE_STEP * 7
};
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
			'elk.edgeRouting': 'ORTHOGONAL'
		},
		children: nodes.map((node) => ({
			id: node.id,
			width: NODE_WIDTH,
			height: NODE_HEIGHT
		})),
		edges: edges.map((edge) => ({
			id: edgeId(edge),
			sources: [edge.from],
			targets: [edge.to]
		}))
	};
}

export async function layoutStrategyGraph(
	nodes: RecruiterGraphNode[],
	edges: RecruiterGraphEdge[],
	bounds: GraphBounds
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

		const positions = stackLanePositions(rawPositions, nodes, bounds);
		return {
			nodes: flowNodes(nodes, positions),
			edges: flowEdges(edges),
			...contentBounds(positions, bounds)
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
	bounds: GraphBounds
): LaidOutStrategyGraph {
	const rawPositions = new Map(nodes.map((node) => [node.id, percentPosition(node, bounds)]));
	const positions = stackLanePositions(rawPositions, nodes, bounds);

	return {
		nodes: flowNodes(nodes, positions),
		edges: flowEdges(edges),
		...contentBounds(positions, bounds)
	};
}

export function stackLanePositions(
	rawPositions: Map<string, GraphPosition>,
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds
): Map<string, GraphPosition> {
	const hasCts = nodes.some((node) => node.lane === 'cts');
	const hasLiepin = nodes.some((node) => node.lane === 'liepin');
	const hasMultipleSourceLanes = hasCts && hasLiepin;
	const businessLayout = businessWorkflowLayout(nodes, bounds);
	const hasBusinessLayout = businessLayout.size > 0;
	const maxX = Math.max(
		1,
		...nodes.map((node) => rawPositions.get(node.id)?.x ?? percentPosition(node, bounds).x)
	);
	const viewportRightX = Math.max(GRAPH_INSET, bounds.width - NODE_WIDTH - GRAPH_INSET);
	const businessRightX = hasBusinessLayout ? BUSINESS_STAGE_X.final : viewportRightX;
	const rightX = Math.max(viewportRightX, businessRightX);
	const availableWidth = Math.max(1, rightX - GRAPH_INSET);
	const maxY = Math.max(GRAPH_INSET, bounds.height - NODE_HEIGHT - GRAPH_INSET);
	const positions = new Map<string, GraphPosition>();

	for (const node of nodes) {
		const businessPosition = businessLayout.get(node.id);
		if (node.id === FINAL_SHORTLIST_ID && businessPosition) {
			positions.set(node.id, businessPosition);
			continue;
		}

		const anchorPosition = anchorNodePosition(node, bounds, rightX);
		if (anchorPosition) {
			positions.set(node.id, anchorPosition);
			continue;
		}

		if (businessPosition) {
			positions.set(node.id, businessPosition);
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
			y: clamp(stackedY, GRAPH_INSET, maxY)
		});
	}

	return separateOverlappingNodes(positions, nodes);
}

export function mergeManualNodePositions(
	input: ManualPositionMergeInput
): ManualPositionMergeResult {
	if (input.currentGraphIdentity !== input.nextGraphIdentity) {
		return {
			positions: new Map(input.current),
			manualPositions: new Map()
		};
	}

	const nextNodeIds = new Set(input.nextNodeIds);
	const manualPositions = new Map(
		[...input.manual.entries()].filter(([nodeId]) => nextNodeIds.has(nodeId))
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
	rightX: number
): GraphPosition | null {
	if (START_NODE_IDS.has(node.id)) {
		return {
			x: GRAPH_INSET,
			y: verticalCenter(bounds)
		};
	}

	if (node.id === FINAL_SHORTLIST_ID) {
		return {
			x: rightX,
			y: verticalCenter(bounds)
		};
	}

	return null;
}

function verticalCenter(bounds: GraphBounds): number {
	return Math.max(GRAPH_INSET, (bounds.height - NODE_HEIGHT) / 2);
}

function businessWorkflowLayout(
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds
): Map<string, GraphPosition> {
	const runtimeRounds = uniqueSortedNumbers(
		nodes
			.map((node) => runtimeRoundNodeInfo(node.id)?.roundNo)
			.filter((value): value is number => typeof value === 'number')
	);
	if (runtimeRounds.length > 0) {
		return runtimeRoundWorkflowLayout(nodes, bounds, runtimeRounds);
	}
	return legacyBusinessWorkflowLayout(nodes, bounds);
}

type RuntimeRoundNodeInfo = {
	roundNo: number;
	stage: 'query' | 'source' | 'merge' | 'score' | 'feedback';
	sourceKind: 'cts' | 'liepin' | null;
};

function runtimeRoundNodeInfo(nodeId: string): RuntimeRoundNodeInfo | null {
	const match = /^round-(\d+)-(query|merge|score|feedback)$/.exec(nodeId);
	if (match) {
		return {
			roundNo: Number(match[1]),
			stage: match[2] as RuntimeRoundNodeInfo['stage'],
			sourceKind: null
		};
	}
	const sourceMatch = /^round-(\d+)-source-(cts|liepin)$/.exec(nodeId);
	if (sourceMatch) {
		return {
			roundNo: Number(sourceMatch[1]),
			stage: 'source',
			sourceKind: sourceMatch[2] as 'cts' | 'liepin'
		};
	}
	return null;
}

function runtimeRoundWorkflowLayout(
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds,
	rounds: number[]
): Map<string, GraphPosition> {
	const positions = new Map<string, GraphPosition>();
	const sourceSpread = Math.round(NODE_HEIGHT * 0.62);
	const hasDualSourceRound = rounds.some(
		(roundNo) =>
			nodes.some((node) => node.id === `round-${String(roundNo)}-source-cts`) &&
			nodes.some((node) => node.id === `round-${String(roundNo)}-source-liepin`)
	);
	const rowGap = hasDualSourceRound
		? NODE_HEIGHT + BUSINESS_ROW_GAP + sourceSpread * 2 + 24
		: NODE_HEIGHT + BUSINESS_ROW_GAP + 28;
	const firstRoundY = GRAPH_INSET + NODE_HEIGHT + BUSINESS_ROW_GAP;
	const rowY = new Map(rounds.map((roundNo, index) => [roundNo, firstRoundY + index * rowGap]));
	const firstRound = rounds[0];
	const sharedY =
		firstRound === undefined
			? verticalCenter(bounds)
			: (rowY.get(firstRound) ?? verticalCenter(bounds));

	for (const node of nodes) {
		if (node.id === 'requirements') {
			positions.set(node.id, { x: columnX('requirements'), y: sharedY });
			continue;
		}
		const info = runtimeRoundNodeInfo(node.id);
		if (!info) {
			continue;
		}
		const baseY = rowY.get(info.roundNo) ?? sharedY;
		const sourceOffset =
			info.stage === 'source' && info.sourceKind === 'cts'
				? -sourceSpread
				: info.stage === 'source' && info.sourceKind === 'liepin'
					? sourceSpread
					: 0;
		positions.set(node.id, {
			x: columnX(info.stage),
			y: Math.max(GRAPH_INSET, baseY + sourceOffset)
		});
	}

	if (nodes.some((node) => node.id === FINAL_SHORTLIST_ID)) {
		const lastRound = rounds[rounds.length - 1];
		positions.set(FINAL_SHORTLIST_ID, {
			x: columnX('final'),
			y: lastRound === undefined ? sharedY : (rowY.get(lastRound) ?? sharedY)
		});
	}

	return positions;
}

function legacyBusinessWorkflowLayout(
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds
): Map<string, GraphPosition> {
	const ctsRoundNumbers = uniqueSortedNumbers(
		nodes
			.map((node) => /^cts-round-(\d+)-(query|result|score|reflect)$/.exec(node.id)?.[1])
			.filter(Boolean)
			.map((value) => Number(value))
	);
	const hasKnownWorkflowNodes = nodes.some(isBusinessWorkflowNode) || ctsRoundNumbers.length > 0;
	if (!hasKnownWorkflowNodes) {
		return new Map();
	}

	const ctsRows = new Map<number, number>();
	for (const [index, roundNo] of ctsRoundNumbers.entries()) {
		ctsRows.set(roundNo, GRAPH_INSET + index * (NODE_HEIGHT + BUSINESS_ROW_GAP));
	}
	const ctsBaseY = ctsRows.get(ctsRoundNumbers[0] ?? 1) ?? GRAPH_INSET;
	const afterCtsY = GRAPH_INSET + ctsRoundNumbers.length * (NODE_HEIGHT + BUSINESS_ROW_GAP);
	const hasLiepinNodes = nodes.some((node) => node.lane === 'liepin');
	const liepinY = hasLiepinNodes
		? Math.max(afterCtsY + BUSINESS_ROW_GAP, bounds.height * 0.62)
		: afterCtsY;
	const sharedY = verticalCenter(bounds);
	const positions = new Map<string, GraphPosition>();

	for (const node of nodes) {
		if (node.id === 'requirements') {
			positions.set(node.id, { x: BUSINESS_STAGE_X.requirements, y: sharedY });
			continue;
		}

		const column = stageColumn(node);
		if (column === null) {
			continue;
		}

		positions.set(node.id, {
			x: columnX(column),
			y: stageRowY(node, ctsRows, ctsBaseY, liepinY, sharedY)
		});
	}

	if (nodes.some((node) => node.id === FINAL_SHORTLIST_ID)) {
		const lastCtsRound = ctsRoundNumbers[ctsRoundNumbers.length - 1];
		const finalY =
			lastCtsRound !== undefined
				? (ctsRows.get(lastCtsRound) ?? ctsBaseY)
				: hasLiepinNodes
					? liepinY
					: sharedY;
		positions.set(FINAL_SHORTLIST_ID, { x: columnX('final'), y: finalY });
	}

	return positions;
}

function stageColumn(node: RecruiterGraphNode): keyof typeof BUSINESS_STAGE_X | null {
	if (node.id === 'cts-source-start' || node.id === 'liepin-source-start') {
		return 'source';
	}
	if (/^cts-round-\d+-query$/.test(node.id)) {
		return 'query';
	}
	if (/^cts-round-\d+-result$/.test(node.id) || node.id === 'liepin-card-search') {
		return 'source';
	}
	if (/^cts-round-\d+-score$/.test(node.id) || node.id === 'liepin-card-candidates') {
		return 'score';
	}
	if (/^cts-round-\d+-reflect$/.test(node.id) || node.id === 'liepin-detail-approval') {
		return 'feedback';
	}
	return null;
}

function isBusinessWorkflowNode(node: RecruiterGraphNode): boolean {
	return stageColumn(node) !== null;
}

function columnX(column: keyof typeof BUSINESS_STAGE_X): number {
	return BUSINESS_STAGE_X[column];
}

function stageRowY(
	node: RecruiterGraphNode,
	ctsRows: Map<number, number>,
	ctsBaseY: number,
	liepinY: number,
	sharedY: number
): number {
	if (START_NODE_IDS.has(node.id) || node.id === FINAL_SHORTLIST_ID) {
		return sharedY;
	}
	if (node.lane === 'liepin') {
		return liepinY;
	}
	const roundNo = ctsRoundNo(node.id);
	if (roundNo !== null) {
		return ctsRows.get(roundNo) ?? ctsBaseY;
	}
	if (node.lane === 'cts') {
		return ctsBaseY;
	}
	return sharedY;
}

function ctsRoundNo(nodeId: string): number | null {
	const match = /^cts-round-(\d+)-(query|result|score|reflect)$/.exec(nodeId);
	return match ? Number(match[1]) : null;
}

function uniqueSortedNumbers(values: number[]): number[] {
	return [...new Set(values.filter((value) => Number.isFinite(value)))].sort(
		(left, right) => left - right
	);
}

function separateOverlappingNodes(
	positions: Map<string, GraphPosition>,
	nodes: RecruiterGraphNode[]
): Map<string, GraphPosition> {
	const orderedNodes = [...nodes].sort((left, right) => {
		const leftAnchored = isAnchorNode(left);
		const rightAnchored = isAnchorNode(right);
		if (leftAnchored !== rightAnchored) {
			return leftAnchored ? -1 : 1;
		}
		const leftPosition = positions.get(left.id);
		const rightPosition = positions.get(right.id);
		return (
			(leftPosition?.x ?? 0) - (rightPosition?.x ?? 0) ||
			(leftPosition?.y ?? 0) - (rightPosition?.y ?? 0)
		);
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
				y: nextPosition.y + NODE_HEIGHT + COLLISION_GAP
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

function contentBounds(
	positions: Map<string, GraphPosition>,
	bounds: GraphBounds
): Pick<LaidOutStrategyGraph, 'contentWidth' | 'contentHeight'> {
	const maxPositionX = Math.max(
		0,
		...[...positions.values()].map((position) => position.x + NODE_WIDTH + GRAPH_INSET)
	);
	const maxPositionY = Math.max(
		0,
		...[...positions.values()].map((position) => position.y + NODE_HEIGHT + GRAPH_INSET)
	);
	return {
		contentWidth: Math.max(bounds.width, maxPositionX),
		contentHeight: Math.max(bounds.height, maxPositionY)
	};
}

function flowNodes(
	nodes: RecruiterGraphNode[],
	positions: Map<string, GraphPosition>
): StrategyFlowNode[] {
	return nodes.map((node) => ({
		id: node.id,
		type: 'strategy',
		position: positions.get(node.id) ?? { x: GRAPH_INSET, y: GRAPH_INSET },
		width: NODE_WIDTH,
		height: NODE_HEIGHT,
		style: `width: ${NODE_WIDTH}px; height: ${NODE_HEIGHT}px;`,
		data: { graphNode: node, selected: false },
		draggable: true,
		selected: false,
		selectable: true,
		sourcePosition: Position.Right,
		targetPosition: Position.Left
	}));
}

function flowEdges(edges: RecruiterGraphEdge[]): StrategyFlowEdge[] {
	return edges.map((edge) => {
		const flowEdge: StrategyFlowEdge = {
			id: edgeId(edge),
			source: edge.from,
			target: edge.to,
			type: 'smoothstep',
			data: { graphEdge: edge },
			class: `strategy-flow-edge ${edge.tone}`
		};
		if (edge.label) {
			flowEdge.label = edge.label;
		}
		return flowEdge;
	});
}

function percentPosition(node: RecruiterGraphNode, bounds: GraphBounds): GraphPosition {
	const xRatio = normalizePercent(node.x);
	const yRatio = normalizePercent(node.y);

	return {
		x: xRatio * Math.max(0, bounds.width - NODE_WIDTH),
		y: yRatio * Math.max(0, bounds.height - NODE_HEIGHT)
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
