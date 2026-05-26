import { describe, expect, it } from 'vitest';
import { runtimeGraphToStory, workbenchNotesToLogEntries } from './runtimeGraphView';
import type { components } from '$lib/api/schema';

type RuntimeGraph = components['schemas']['WorkbenchRuntimeGraphResponse'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];

describe('runtimeGraphToStory', () => {
	it('maps backend authored graph without changing node ids or business kinds', () => {
		const graph: RuntimeGraph = {
			sessionId: 'session-1',
			generatedAt: '2026-05-26T00:00:00Z',
			completionText: null,
			nodes: [
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
					eventIds: ['runtime-test:1:scoring:all'],
					detailSections: [],
					candidateScope: { scopeKind: 'round_score', sourceKind: 'all', roundNo: 1, reason: null }
				}
			],
			edges: []
		};

		const story = runtimeGraphToStory(graph);
		const node = story.graphNodes[0];

		expect(story.graphNodes).toHaveLength(1);
		expect(node).toBeDefined();
		expect(node!.id).toBe('round-1-score');
		expect(node!.kind).toBe('评分');
		expect(node!.detailPayload?.kind).toBe('runtimeGraphNode');
		expect(node!.detailPayload?.node.nodeId).toBe('round-1-score');
	});

	it('keeps Workbench running notes from public events without rebuilding graph semantics', () => {
		const events: WorkbenchEvent[] = [
			{
				globalSeq: 42,
				eventName: 'workbench_note_created',
				sourceKind: null,
				sourceRunId: null,
				schemaVersion: 'workbench_note_v1',
				occurredAt: '2026-05-26T00:00:00Z',
				createdAt: '2026-05-26T00:00:00Z',
				payload: {
					text: 'CTS 和猎聘已完成本轮检索，正在合并候选人。',
					eventSeq: 42,
					noteKind: 'progress',
					statusHint: 'new_progress'
				}
			} as WorkbenchEvent
		];

		expect(workbenchNotesToLogEntries(events)).toMatchObject([
			{
				id: 'workbench-note-42',
				text: 'CTS 和猎聘已完成本轮检索，正在合并候选人。',
				tag: 'SYS',
				sourceKind: 'all'
			}
		]);
	});
});
