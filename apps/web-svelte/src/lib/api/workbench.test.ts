import { afterEach, describe, expect, it, vi } from 'vitest';

function jsonResponse(body: unknown) {
	return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
}

function event(globalSeq: number) {
	return {
		globalSeq,
		sessionSeq: globalSeq,
		sessionId: 'session-1',
		sourceRunId: null,
		sourceKind: null,
		eventName: 'candidate_seen',
		schemaVersion: '1',
		idempotencyKey: null,
		payload: {},
		occurredAt: '2026-05-17T00:00:00Z',
		createdAt: '2026-05-17T00:00:00Z'
	};
}

function graphCandidateListResponse(nodeId: string) {
	return {
		nodeId,
		nodeScope: {
			sessionId: 'session 1',
			source: 'all',
			roundId: null,
			nodeKind: 'recall'
		},
		items: [],
		nextCursor: null,
		totalSourceResults: 0,
		totalGraphCandidates: 0,
		totalEstimate: null,
		coverage: {
			sourceResultIdsSeen: [],
			missingSafeIdentityCount: 0,
			missingSnapshotCount: 0,
			forbiddenSnapshotCount: 0,
			droppedRows: 0
		},
		truncated: false,
		generatedAt: '2026-05-17T00:00:00Z',
		recoveryState: 'ready',
		recoveryReason: null
	};
}

afterEach(() => {
	vi.resetModules();
	vi.unstubAllGlobals();
});

describe('workbench API functions', () => {
	it('paginates session events until the endpoint returns a short page', async () => {
		const requests: string[] = [];
		const fetchMock = vi.fn(async (request: Request) => {
			const url = new URL(request.url);
			requests.push(`${url.pathname}${url.search}`);
			const afterSeq = Number(url.searchParams.get('after_seq') ?? '0');
			const count = afterSeq === 0 ? 200 : 1;
			const events = Array.from({ length: count }, (_, index) => event(afterSeq + index + 1));
			return jsonResponse({ events });
		});
		vi.stubGlobal('fetch', fetchMock);
		const { listSessionEvents } = await import('./workbench');

		const result = await listSessionEvents('session-1');

		expect(result.events).toHaveLength(201);
		expect(requests).toEqual([
			'/api/workbench/sessions/session-1/events?after_seq=0&limit=200',
			'/api/workbench/sessions/session-1/events?after_seq=200&limit=200'
		]);
	});

	it('uses generated path and query parameters for graph candidates', async () => {
		const requests: string[] = [];
		const fetchMock = vi.fn(async (request: Request) => {
			const url = new URL(request.url);
			requests.push(`${url.pathname}${url.search}`);
			return jsonResponse(graphCandidateListResponse('node/1'));
		});
		vi.stubGlobal('fetch', fetchMock);
		const { listGraphCandidates } = await import('./workbench');

		const result = await listGraphCandidates('session 1', 'node/1', 'cursor-1', 25);

		expect(result.nodeId).toBe('node/1');
		expect(requests).toEqual([
			'/api/workbench/sessions/session%201/graph-candidates?node_id=node%2F1&limit=25&cursor=cursor-1'
		]);
	});

	it('rejects blank graph candidate query identifiers before issuing network requests', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);
		const { listGraphCandidates, getGraphCandidateResumeSnapshot } = await import('./workbench');

		await expect(listGraphCandidates('session-1', '')).rejects.toThrow('node id');
		await expect(getGraphCandidateResumeSnapshot('session-1', '')).rejects.toThrow(
			'graph candidate id'
		);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('calls the dev-mode and dual-source workbench endpoints', async () => {
		const requests: string[] = [];
		const fetchMock = vi.fn(async (request: Request) => {
			const url = new URL(request.url);
			requests.push(`${request.method} ${url.pathname}`);
			if (url.pathname === '/api/workbench/dev-mode/status') {
				return jsonResponse({
					mode: 'settings',
					overallStatus: 'configured',
					components: [],
					credentials: {},
					sources: {},
					dataRoots: { dataRoots: {} }
				});
			}
			if (url.pathname.endsWith('/final-top10')) {
				return jsonResponse({ items: [], coverageStatus: 'empty', finalizationRevision: 1 });
			}
			if (url.pathname.endsWith('/start')) {
				return jsonResponse({
					sessionId: 'session-1',
					runtimeJob: {
						jobId: 'rtjob-session-1',
						status: 'queued',
						sourceKinds: ['cts'],
						attemptCount: 0,
						errorMessage: null,
						createdAt: '2026-05-26T00:00:00Z',
						updatedAt: '2026-05-26T00:00:00Z'
					},
					blockedSources: []
				});
			}
			if (url.pathname.endsWith('/requirements/approve')) {
				return jsonResponse({
					session_id: 'session-1',
					status: 'approved',
					requirement_sheet: null,
					updated_at: '2026-05-17T00:00:00Z'
				});
			}
			return jsonResponse({});
		});
		vi.stubGlobal('fetch', fetchMock);
		const {
			approveRequirementReview,
			getDevModeStatus,
			listFinalTopCandidates,
			startSessionSourceRuns
		} = await import('./workbench');

		await getDevModeStatus();
		await listFinalTopCandidates('session-1');
		await approveRequirementReview('session-1');
		await startSessionSourceRuns('session-1');

		expect(requests).toEqual([
			'GET /api/workbench/dev-mode/status',
			'GET /api/workbench/sessions/session-1/final-top10',
			'POST /api/workbench/sessions/session-1/requirements/approve',
			'POST /api/workbench/sessions/session-1/start'
		]);
	});
});
