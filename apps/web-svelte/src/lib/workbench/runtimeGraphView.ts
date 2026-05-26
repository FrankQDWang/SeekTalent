import type { components } from '$lib/api/schema';
import type {
	RecruiterGraphEdge,
	RecruiterGraphNode,
	RecruiterLane,
	RecruiterLogEntry,
	RecruiterTone,
	SourceKind
} from './recruiterAnimation';

type RuntimeGraph = components['schemas']['WorkbenchRuntimeGraphResponse'];
type RuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
type RuntimeGraphEdge = components['schemas']['WorkbenchRuntimeGraphEdgeResponse'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];

export type RuntimeGraphStory = {
	criteria: null;
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	logEntries: RecruiterLogEntry[];
	completionText: string | null;
};

const kindLabels: Record<string, RecruiterGraphNode['kind']> = {
	job: '岗位',
	requirements: '拆解',
	query: '检索',
	round_query: '检索',
	source_result: '检索',
	merge: '命中',
	scoring: '评分',
	feedback: '反思',
	detail_approval: '详情审批',
	final: '排序'
};

export function runtimeGraphToStory(
	graph: RuntimeGraph,
	events: WorkbenchEvent[] = []
): RuntimeGraphStory {
	return {
		criteria: null,
		graphNodes: graph.nodes.map(runtimeNodeToRecruiterNode),
		graphEdges: graph.edges.map(runtimeEdgeToRecruiterEdge),
		logEntries: workbenchNotesToLogEntries(events),
		completionText: graph.completionText
	};
}

export function workbenchNotesToLogEntries(events: WorkbenchEvent[]): RecruiterLogEntry[] {
	return events
		.filter((event) => event.eventName === 'workbench_note_created')
		.map((event) => {
			const payload = event.payload as Record<string, unknown>;
			const sequence = Number(payload.eventSeq ?? payload.event_seq ?? event.globalSeq);
			const sourceKind = event.sourceKind ?? 'all';
			return {
				id: `workbench-note-${String(sequence)}`,
				at: Number.isFinite(sequence) ? sequence : event.globalSeq,
				tag: 'SYS',
				text: String(payload.text ?? '').trim(),
				sourceKind,
				sourceLabel:
					sourceKind === 'cts' ? 'CTS' : sourceKind === 'liepin' ? '猎聘' : '全部来源',
				lane: sourceKind === 'cts' || sourceKind === 'liepin' ? sourceKind : 'shared',
				relatedNodeId: undefined
			};
		})
		.filter((entry) => entry.text.length > 0)
		.sort((left, right) => left.at - right.at || left.id.localeCompare(right.id));
}

function runtimeNodeToRecruiterNode(node: RuntimeGraphNode): RecruiterGraphNode {
	const sourceKind = node.sourceKind === 'cts' || node.sourceKind === 'liepin' ? node.sourceKind : 'all';
	return {
		id: node.nodeId,
		at: node.roundNo ?? 0,
		kind: kindLabels[node.kind] ?? kindLabels[node.stage] ?? '检索',
		label: node.label,
		detail: node.summaryText,
		x: 0,
		y: 50,
		tone: toneForStatus(node.status),
		sourceKind,
		sourceLabel: sourceLabel(sourceKind),
		lane: laneForNode(node),
		detailPayload: { kind: 'runtimeGraphNode', node },
		eventIds: node.eventIds,
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function runtimeEdgeToRecruiterEdge(edge: RuntimeGraphEdge): RecruiterGraphEdge {
	return {
		from: edge.fromNodeId,
		to: edge.toNodeId,
		label: edge.label ?? undefined,
		tone: 'blue'
	};
}

function toneForStatus(status: RuntimeGraphNode['status']): RecruiterTone {
	if (status === 'completed') return 'green';
	if (status === 'running') return 'blue';
	if (status === 'partial' || status === 'blocked' || status === 'degraded') return 'amber';
	if (status === 'failed' || status === 'cancelled') return 'rose';
	return 'neutral';
}

function laneForNode(node: RuntimeGraphNode): RecruiterLane {
	if (node.lane === 'cts' || node.lane === 'liepin') return node.lane;
	return 'shared';
}

function sourceLabel(sourceKind: SourceKind | 'all') {
	if (sourceKind === 'cts') return 'CTS';
	if (sourceKind === 'liepin') return '猎聘';
	return '全部来源';
}
