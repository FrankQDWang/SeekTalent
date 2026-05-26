import { render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
import type { RuntimeGraphStory } from '$lib/workbench/runtimeGraphView';
import {
	disposeStrategyGraphLayoutRunner,
	setStrategyGraphLayoutRunnerForTests
} from '$lib/workbench/strategyGraphLayout';
import StrategyGraph from './StrategyGraph.svelte';

class TestResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

beforeEach(() => {
	globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
	window.matchMedia = (query: string) =>
		({
			matches: false,
			media: query,
			onchange: null,
			addListener: () => {},
			removeListener: () => {},
			addEventListener: () => {},
			removeEventListener: () => {},
			dispatchEvent: () => false
		}) as MediaQueryList;
	setStrategyGraphLayoutRunnerForTests(async (graph) => ({ ...graph, children: [] }));
});

afterEach(() => {
	disposeStrategyGraphLayoutRunner();
});

describe('StrategyGraph', () => {
	it('renders six runtime round rows inside scrollable content', async () => {
		const story: RuntimeGraphStory = {
			criteria: null,
			graphNodes: [
				graphNode('job', '岗位需求'),
				graphNode('requirements', '需求拆解'),
				...Array.from({ length: 6 }, (_, index) => index + 1).flatMap((roundNo) => [
					graphNode(`round-${String(roundNo)}-query`, `第 ${String(roundNo)} 轮 · 查询包`),
					graphNode(
						`round-${String(roundNo)}-source-cts`,
						`第 ${String(roundNo)} 轮 · CTS 检索`,
						'cts'
					),
					graphNode(
						`round-${String(roundNo)}-source-liepin`,
						`第 ${String(roundNo)} 轮 · Liepin 检索`,
						'liepin'
					),
					graphNode(`round-${String(roundNo)}-merge`, `第 ${String(roundNo)} 轮 · 合并去重`),
					graphNode(`round-${String(roundNo)}-score`, `第 ${String(roundNo)} 轮 · Top Pool`)
				]),
				graphNode('final-shortlist', '最终短名单')
			],
			graphEdges: [],
			logEntries: [],
			completionText: null
		};

		const { container } = render(StrategyGraph, {
			props: { story, selectedNodeId: null, onSelectNode: () => {} }
		});
		const shell = container.querySelector('.strategy-flow-shell') as HTMLDivElement | null;

		expect(shell).not.toBeNull();
		await waitFor(() =>
			expect(screen.getByTestId('strategy-node-round-6-source-liepin')).toBeInTheDocument()
		);
		await waitFor(() => {
			const contentHeight = Number.parseFloat(
				shell?.style.getPropertyValue('--strategy-content-height') ?? ''
			);
			expect(contentHeight).toBeGreaterThan(1_000);
		});

		shell!.scrollTop = Number.parseFloat(
			shell!.style.getPropertyValue('--strategy-content-height')
		);
		shell!.dispatchEvent(new Event('scroll'));

		expect(screen.getByTestId('strategy-node-round-6-source-liepin')).toBeVisible();
	});
});

function graphNode(
	id: string,
	label: string,
	lane: 'shared' | 'cts' | 'liepin' = 'shared'
): RecruiterGraphNode {
	return {
		id,
		at: 0,
		kind: '检索',
		label,
		detail: label,
		x: 0,
		y: 0,
		tone: 'neutral',
		sourceKind: lane === 'shared' ? 'all' : lane,
		sourceLabel: lane === 'cts' ? 'CTS' : lane === 'liepin' ? 'Liepin' : 'All sources',
		lane,
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}
