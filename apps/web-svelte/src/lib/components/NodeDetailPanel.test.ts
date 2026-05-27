import { render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import NodeDetailPanelHarness from './NodeDetailPanel.test.svelte';
import type { components } from '$lib/api/schema';
import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
import { getGraphCandidateResumeSnapshot } from '$lib/api/workbench';

vi.mock('$lib/api/workbench', () => ({
	getGraphCandidateResumeSnapshot: vi.fn()
}));

type RuntimeNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
type CandidatePage = components['schemas']['WorkbenchGraphCandidateListResponse'];
type ResumeSnapshot = components['schemas']['WorkbenchGraphCandidateResumeSnapshotResponse'];

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

function graphCandidate(
	id: string,
	name: string,
	canExpandResume = true
): CandidatePage['items'][number] {
	return {
		graphCandidateId: id,
		sourceKind: 'cts',
		sourceRunId: 'source-run-1',
		nodeKind: 'scoring',
		roundNo: 1,
		laneType: null,
		queryRole: null,
		relationshipKind: 'scored',
		displayName: name,
		title: 'Backend Engineer',
		company: 'SearchCo',
		location: 'Shanghai',
		sourceBadges: ['CTS'],
		score: 91,
		fitBucket: 'fit',
		summary: 'Normalized resume summary should not render in candidate card.',
		matchedMustHaves: [],
		strengths: [],
		missingRisks: [],
		reviewItemId: `review-${id}`,
		evidenceLevel: 'detail',
		detailOpenRequestId: null,
		canExpandResume,
		canMarkPromising: true,
		canReject: true,
		canSaveNote: true,
		canRequestDetail: false,
		canOpenProvider: false
	};
}

function candidatePage(nodeId: string, canExpandResume = true): CandidatePage {
	return {
		...emptyPage(nodeId),
		items: [graphCandidate('graph-candidate-1', 'Candidate One', canExpandResume)],
		totalGraphCandidates: 1,
		totalEstimate: 1,
		recoveryState: 'ready',
		recoveryReason: null
	};
}

function resumeSnapshot(graphCandidateId = 'graph-candidate-1', rawName = '原始简历姓名'): ResumeSnapshot {
	return {
		graphCandidateId,
		status: 'ready',
		sourceCompleteness: 'cts_raw_payload',
		originalResume: {
			sourceKind: 'cts',
			sections: [
				{
					title: '基本信息',
					items: [
						{
							title: '基本信息',
							fields: [{ key: 'candidateName', label: '姓名', value: rawName }]
						}
					]
				}
			]
		},
		profile: {
			displayName: 'Normalized Name',
			headline: 'Normalized Title',
			company: 'Normalized Company',
			location: 'Normalized Location',
			summary: 'Normalized summary should never render in node details'
		},
		workExperience: [],
		education: [],
		projects: [],
		skills: ['Normalized Skill'],
		sourceEvidence: []
	};
}

describe('NodeDetailPanel runtime graph details', () => {
	beforeEach(() => {
		vi.mocked(getGraphCandidateResumeSnapshot).mockReset();
	});

	it('renders backend-authored natural text and fact sections', () => {
		render(NodeDetailPanelHarness, {
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
		render(NodeDetailPanelHarness, {
			props: {
				node: graphNode(runtimeNode({ nodeId: 'requirements', label: '需求拆解' })),
				graphCandidatePage: emptyPage('requirements')
			}
		});

		expect(screen.getByText('原始简历需要恢复')).toBeInTheDocument();
		expect(screen.getByText('node_has_no_candidate_scope')).toBeInTheDocument();
	});

	it('renders every graph candidate as an original resume card without normalized fields', async () => {
		vi.mocked(getGraphCandidateResumeSnapshot).mockImplementation((_sessionId, graphCandidateId) =>
			Promise.resolve(
				resumeSnapshot(
					graphCandidateId,
					graphCandidateId === 'graph-candidate-1' ? '第一份原始简历' : '第二份原始简历'
				)
			)
		);

		render(NodeDetailPanelHarness, {
			props: {
				node: graphNode(runtimeNode()),
				graphCandidatePage: {
					...candidatePage('final-shortlist'),
					nodeScope: { sessionId: 'session-1', source: 'all', roundId: null, nodeKind: 'final' },
					items: [
						graphCandidate('graph-candidate-1', 'Candidate One'),
						graphCandidate('graph-candidate-2', 'Candidate Two')
					],
					totalGraphCandidates: 2,
					totalEstimate: 2
				}
			}
		});

		await waitFor(() => {
			expect(screen.getByText('第一份原始简历')).toBeInTheDocument();
			expect(screen.getByText('第二份原始简历')).toBeInTheDocument();
		});
		expect(screen.getAllByText('展开完整简历')).toHaveLength(2);
		expect(
			screen.queryByText('Normalized resume summary should not render in candidate card.')
		).not.toBeInTheDocument();
		expect(screen.queryByText('Normalized Name')).not.toBeInTheDocument();
		expect(
			screen.queryByText('Normalized summary should never render in node details')
		).not.toBeInTheDocument();
		expect(screen.queryByText('Normalized Skill')).not.toBeInTheDocument();
	});

	it('does not request or show an infinite loader for candidates without original resumes', () => {
		render(NodeDetailPanelHarness, {
			props: {
				node: graphNode(runtimeNode()),
				graphCandidatePage: candidatePage('round-1-score', false)
			}
		});

		expect(screen.getByText('原始简历不可用')).toBeInTheDocument();
		expect(screen.queryByText('正在加载原始简历')).not.toBeInTheDocument();
		expect(getGraphCandidateResumeSnapshot).not.toHaveBeenCalled();
	});
});
