import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import NodeDetailPanel from './NodeDetailPanel.svelte';
import type { components } from '$lib/api/schema';
import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';

type RuntimeNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
type CandidatePage = components['schemas']['WorkbenchGraphCandidateListResponse'];

function runtimeNode(overrides: Partial<RuntimeNode> = {}): RuntimeNode {
	return Object.assign(
		{
			nodeId: 'round-1-score',
			kind: 'scoring',
			label: '第 1 轮 · Top Pool',
			summaryText: '第 1 轮评分完成，10 位候选人进入 Top Pool。',
			status: 'completed',
			stage: 'scoring',
			sourceKind: 'all',
			lane: 'shared',
			roundNo: 1,
			eventIds: [],
			detailSections: [
				{
					heading: '本轮评分',
					kind: 'facts',
					text: null,
					facts: [
						{ label: '进入评分', value: '10 人' },
						{ label: 'Top Pool', value: '10 人' }
					],
					values: []
				}
			],
			candidateScope: { scopeKind: 'round_score', sourceKind: 'all', roundNo: 1, reason: null }
		},
		overrides
	);
}

function graphNode(node: RuntimeNode): RecruiterGraphNode {
	return {
		id: node.nodeId,
		at: 1,
		kind: '评分',
		label: node.label,
		detail: node.summaryText,
		x: 0,
		y: 50,
		tone: 'green',
		sourceKind: 'all',
		sourceLabel: '全部来源',
		lane: 'shared',
		detailPayload: { kind: 'runtimeGraphNode', node },
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function emptyPage(nodeId: string, recoveryReason = 'node_has_no_candidate_scope'): CandidatePage {
	return {
		nodeId,
		nodeScope: { sessionId: 'session-1', source: 'all', roundId: null, nodeKind: 'recall' },
		items: [],
		nextCursor: null,
		totalSourceResults: 0,
		totalGraphCandidates: 0,
		totalEstimate: 0,
		coverage: {
			sourceResultIdsSeen: [],
			missingSafeIdentityCount: 0,
			missingSnapshotCount: 0,
			forbiddenSnapshotCount: 0,
			droppedRows: 0
		},
		truncated: false,
		generatedAt: '2026-05-26T00:00:00Z',
		recoveryState: 'recoverable_empty',
		recoveryReason
	};
}

describe('NodeDetailPanel runtime graph details', () => {
	it('renders backend-authored natural text and fact sections', () => {
		render(NodeDetailPanel, {
			props: {
				node: graphNode(runtimeNode()),
				graphCandidatePage: emptyPage('round-1-score')
			}
		});

		expect(screen.getByText('第 1 轮 · Top Pool')).toBeInTheDocument();
		expect(screen.getByText('第 1 轮评分完成，10 位候选人进入 Top Pool。')).toBeInTheDocument();
		expect(screen.getByText('本轮评分')).toBeInTheDocument();
		expect(screen.getByText('进入评分')).toBeInTheDocument();
		expect(screen.getAllByText('10 人').length).toBeGreaterThan(0);
	});

	it('renders recoverable empty candidate state without surfacing a 404', () => {
		render(NodeDetailPanel, {
			props: {
				node: graphNode(runtimeNode({ nodeId: 'requirements', label: '需求拆解' })),
				graphCandidatePage: emptyPage('requirements')
			}
		});

		expect(screen.getByText('候选人索引需要恢复')).toBeInTheDocument();
		expect(screen.getByText('node_has_no_candidate_scope')).toBeInTheDocument();
	});
});
