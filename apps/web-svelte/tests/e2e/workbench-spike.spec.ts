import { expect, type Page, test } from '@playwright/test';

const SESSION_ID = 'session-svelte-spike';
const GRAPH_CANDIDATE_ID = 'graph-final-1';
const RAW_LEAK_STRINGS = [
	'/private/artifacts/foo.json',
	'X-CSRF-Token',
	'cookie',
	'raw_provider_payload',
	'Authorization'
];

const user = {
	userId: 'user-spike',
	email: 'spike@example.com',
	displayName: 'Spike Reviewer',
	role: 'admin',
	workspaceId: 'workspace-spike'
};

const triage = {
	sessionId: SESSION_ID,
	status: 'approved',
	mustHaves: ['AI platform product leadership', 'multi-source recruiting workflows'],
	niceToHaves: ['猎头业务理解', 'workflow automation'],
	synonyms: ['talent intelligence', 'candidate discovery'],
	seniorityFilters: ['director+', 'principal'],
	exclusions: ['junior IC only'],
	generatedQueryHints: ['AI recruiting agent', 'talent graph workflow'],
	createdAt: '2026-05-10T00:00:00Z',
	updatedAt: '2026-05-10T00:00:00Z',
	approvedAt: '2026-05-10T00:01:00Z'
};

const sourceCards = [
	{
		sourceRunId: 'src-cts-spike',
		sourceKind: 'cts',
		label: 'CTS',
		status: 'completed',
		authState: 'not_required',
		cardsScannedCount: 42,
		uniqueCandidatesCount: 9,
		detailOpenUsedCount: 0,
		detailOpenBlockedCount: 0,
		warningCode: null,
		warningMessage: null
	},
	{
		sourceRunId: 'src-liepin-spike',
		sourceKind: 'liepin',
		label: 'Liepin',
		status: 'completed',
		authState: 'login_required',
		cardsScannedCount: 96,
		uniqueCandidatesCount: 12,
		detailOpenUsedCount: 3,
		detailOpenBlockedCount: 1,
		warningCode: null,
		warningMessage: null,
		connectionId: 'conn-liepin-spike',
		connectionStatus: 'connected',
		connectionWarningCode: null,
		connectionWarningMessage: null
	}
];

const session = {
	sessionId: SESSION_ID,
	workspaceId: 'workspace-spike',
	ownerUserId: 'user-spike',
	jobTitle: 'AI Recruiting Platform VP',
	jdText: [
		'Own multi-source candidate discovery across CTS and Liepin.',
		'Protect detail-open budget with sequential review.',
		'Use profile seeds to reverse-engineer shared attributes.'
	].join('\n'),
	notes: 'Internal executive search pilot / high-end roles / LAN workbench',
	status: 'draft',
	requirementTriage: triage,
	sourceRuns: sourceCards,
	sourceCards,
	runtimeSourceState: {
		sessionId: SESSION_ID,
		status: 'completed',
		coverageStatus: 'complete',
		finalizationRevision: 1,
		finalizationReasonCode: 'source_lanes_completed',
		identityMergeCount: 1,
		ambiguousDuplicateCount: 0,
		canonicalResumeSelectedCount: 1,
		sources: [
			{
				sourceKind: 'cts',
				status: 'completed',
				cardsSeenCount: 42,
				cardsFilteredCount: 4,
				candidatesCount: 9,
				detailRecommendationsCount: 0,
				detailState: null,
				lastEventType: 'source_lane_completed',
				lastEventSeq: 3,
				updatedAt: '2026-05-10T00:03:00Z'
			},
			{
				sourceKind: 'liepin',
				status: 'completed',
				cardsSeenCount: 96,
				cardsFilteredCount: 12,
				candidatesCount: 12,
				detailRecommendationsCount: 3,
				detailState: 'completed',
				lastEventType: 'source_lane_completed',
				lastEventSeq: 5,
				updatedAt: '2026-05-10T00:05:00Z'
			}
		]
	}
};

const events = [
	{
		globalSeq: 1,
		sessionSeq: 1,
		sessionId: SESSION_ID,
		sourceRunId: 'src-cts-spike',
		sourceKind: 'cts',
		eventName: 'requirements_approved',
		payload: { message: 'Requirement triage approved.' },
		createdAt: '2026-05-10T00:01:00Z'
	},
	{
		globalSeq: 2,
		sessionSeq: 2,
		sessionId: SESSION_ID,
		sourceRunId: 'src-cts-spike',
		sourceKind: 'cts',
		eventName: 'runtime_round_completed',
		payload: {
			type: 'round_completed',
			roundNo: 1,
			payload: {
				executed_queries: [{ query_terms: ['AI platform', 'multi-source recruiting'] }],
				raw_candidate_count: 18,
				unique_new_count: 6,
				newly_scored_count: 6,
				fit_count: 2,
				not_fit_count: 4,
				reflection_summary: '收窄到有多源招聘工作流经验的产品负责人。',
				reflection_rationale: '强候选人集中在 enterprise search 和 talent intelligence 产品线。',
				next_direction: '保留 AI platform，加入 recruiting workflow 和 executive search 关键词。'
			}
		},
		createdAt: '2026-05-10T00:02:00Z'
	},
	{
		globalSeq: 3,
		sessionSeq: 3,
		sessionId: SESSION_ID,
		sourceRunId: 'src-liepin-spike',
		sourceKind: 'liepin',
		eventName: 'liepin_card_search_completed',
		payload: { scanned: 96, candidates: 12 },
		createdAt: '2026-05-10T00:03:00Z'
	},
	{
		globalSeq: 4,
		sessionSeq: 4,
		sessionId: SESSION_ID,
		sourceRunId: 'src-liepin-spike',
		sourceKind: 'liepin',
		eventName: 'candidate_review_item_upserted',
		payload: { reviewItemId: 'review-liepin-spike' },
		createdAt: '2026-05-10T00:04:00Z'
	}
];

const reviewCandidate = {
	reviewItemId: 'review-liepin-spike',
	sessionId: SESSION_ID,
	graphCandidateId: GRAPH_CANDIDATE_ID,
	canExpandResume: true,
	status: 'promising',
	note: 'Strong operator profile, detail already approved.',
	displayName: 'Candidate A',
	title: 'VP Product, Talent Intelligence',
	company: 'Enterprise AI Platform',
	location: 'Shanghai',
	summary: 'Led recruiting workflow automation and enterprise search products.',
	aggregateScore: 92,
	fitBucket: 'fit',
	sourceBadges: ['CTS', 'Liepin'],
	evidenceLevel: 'detail',
	matchedMustHaves: ['AI platform product leadership'],
	matchedPreferences: ['猎头业务理解'],
	missingRisks: ['Compensation band needs confirmation'],
	strengths: ['Built search workflows'],
	weaknesses: ['Limited public benchmark evidence'],
	evidence: [
		{
			evidenceId: 'ev-liepin-spike',
			sourceRunId: 'src-liepin-spike',
			sourceKind: 'liepin',
			evidenceLevel: 'detail',
			score: 92,
			fitBucket: 'fit',
			matchedMustHaves: ['AI platform product leadership'],
			matchedPreferences: ['猎头业务理解'],
			missingRisks: ['Compensation band needs confirmation'],
			strengths: ['Built search workflows'],
			weaknesses: ['Limited public benchmark evidence'],
			createdAt: '2026-05-10T00:04:00Z'
		}
	],
	createdAt: '2026-05-10T00:04:00Z',
	updatedAt: '2026-05-10T00:05:00Z'
};

const graphCandidate = {
	graphCandidateId: GRAPH_CANDIDATE_ID,
	sourceKind: 'liepin',
	sourceRunId: 'src-liepin-spike',
	nodeKind: 'final',
	roundNo: 1,
	laneType: 'shared',
	queryRole: 'final',
	relationshipKind: 'final',
	displayName: 'Candidate A',
	title: 'VP Product, Talent Intelligence',
	company: 'Enterprise AI Platform',
	location: 'Shanghai',
	sourceBadges: ['CTS', 'Liepin'],
	score: 92,
	fitBucket: 'fit',
	summary: 'Safe candidate summary for the graph panel.',
	matchedMustHaves: ['AI platform product leadership'],
	strengths: ['Built search workflows'],
	missingRisks: ['Compensation band needs confirmation'],
	reviewItemId: 'review-liepin-spike',
	evidenceLevel: 'detail',
	detailOpenRequestId: 'detail-open-1',
	canExpandResume: true
};

const resumeSnapshot = {
	graphCandidateId: GRAPH_CANDIDATE_ID,
	status: 'ready',
	reason: null,
	sourceCompleteness: 'normalized_fallback',
	originalResume: null,
	profile: {
		displayName: 'Candidate A',
		headline: 'VP Product, Talent Intelligence',
		company: 'Enterprise AI Platform',
		location: 'Shanghai',
		summary: 'Sanitized resume summary: built enterprise recruiting workflow automation.'
	},
	workExperience: [
		{
			company: 'Enterprise AI Platform',
			title: 'VP Product',
			duration: '2022-2026',
			summary: 'Led multi-source recruiting workflow products.'
		}
	],
	education: [{ school: 'Fudan University', degree: 'MBA', major: 'Management' }],
	projects: [{ name: 'Talent Graph', summary: 'Built recruiter-facing matching workflow.' }],
	skills: ['Recruiting workflow', 'Enterprise search'],
	sourceEvidence: [{ label: 'safe evidence', text: 'Normalized detail evidence only.' }]
};

test.describe('Svelte Workbench parity graph regression', () => {
	test('renders graph path, loads graph candidates and lazy resume snapshot', async ({
		page
	}, testInfo) => {
		const callCounts = await mockWorkbenchApi(page);
		await page.setViewportSize({ width: 1440, height: 920 });
		await page.goto('/sessions');

		await expect(page.getByRole('link', { name: /AI Recruiting Platform VP/ })).toBeVisible();
		await page.getByRole('link', { name: /AI Recruiting Platform VP/ }).click();
		await expect(page.getByTestId('strategy-flow')).toBeVisible();

		const finalNode = page.getByTestId('strategy-node-final-shortlist');
		await expect(finalNode).toBeVisible();
		await finalNode.click();
		await expect(page.getByTestId('node-detail-panel')).toContainText('最终短名单');
		await expect.poll(() => callCounts.graphCandidates).toBe(1);

		const candidateCard = page.getByTestId(`graph-candidate-${GRAPH_CANDIDATE_ID}`);
		await expect(candidateCard).toBeVisible();
		await candidateCard.click();
		await expect.poll(() => callCounts.resumeSnapshot).toBe(1);
		await expect(page.getByText('Sanitized resume summary')).toBeVisible();

		await finalNode.focus();
		await page.keyboard.press('Enter');
		await expect(page.getByTestId('node-detail-panel')).toContainText('最终短名单');

		const draggableRequirementsNode = page.locator('.svelte-flow__node[data-id="requirements"]');
		await expect(draggableRequirementsNode).toBeVisible();
		const beforeDrag = await boundingBox(draggableRequirementsNode);
		const dragStartX = beforeDrag.x + beforeDrag.width / 2;
		const dragStartY = beforeDrag.y + beforeDrag.height / 2;
		await page.mouse.move(dragStartX, dragStartY);
		await page.mouse.down();
		await page.mouse.move(dragStartX + 140, dragStartY + 48, { steps: 12 });
		await page.mouse.up();
		const afterDrag = await boundingBox(draggableRequirementsNode);
		expect(
			Math.abs(afterDrag.x - beforeDrag.x) + Math.abs(afterDrag.y - beforeDrag.y)
		).toBeGreaterThan(12);

		const viewport = page.locator('.svelte-flow__viewport').first();
		const beforeZoom = await transformStyle(viewport);
		await page.mouse.wheel(0, -280);
		await expect.poll(() => transformStyle(viewport)).not.toBe(beforeZoom);
		const beforePan = await transformStyle(viewport);
		const graph = await boundingBox(page.getByTestId('strategy-flow'));
		const panStartX = graph.x + 72;
		const panStartY = graph.y + graph.height - 56;
		await page.mouse.move(panStartX, panStartY);
		await page.mouse.down();
		await page.mouse.move(panStartX + 120, panStartY + 20, { steps: 8 });
		await page.mouse.up();
		await expect.poll(() => transformStyle(viewport)).not.toBe(beforePan);

		await expect.poll(() => callCounts.sessionEvents).toBeGreaterThanOrEqual(1);
		await page.screenshot({
			path: testInfo.outputPath('desktop-graph-detail.png'),
			fullPage: false
		});
		await page.setViewportSize({ width: 1024, height: 820 });
		await page.screenshot({
			path: testInfo.outputPath('tablet-1024-graph-detail.png'),
			fullPage: false
		});

		for (const raw of RAW_LEAK_STRINGS) {
			await expect(page.getByText(raw, { exact: false })).toHaveCount(0);
		}
	});

	test('does not render raw backend error details', async ({ page }) => {
		await page.route('**/api/**', async (route) => {
			const requestUrl = new URL(route.request().url());
			if (requestUrl.pathname === '/api/auth/me') {
				return route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({ user })
				});
			}
			if (requestUrl.pathname === '/api/workbench/sessions') {
				return route.fulfill({
					status: 500,
					contentType: 'application/json',
					body: JSON.stringify({
						detail: RAW_LEAK_STRINGS.join(' ')
					})
				});
			}
			return route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
		});

		await page.goto('/sessions');
		await expect(page.getByRole('alert')).toContainText('Could not load sessions');
		for (const raw of RAW_LEAK_STRINGS) {
			await expect(page.getByText(raw, { exact: false })).toHaveCount(0);
		}
	});
});

async function mockWorkbenchApi(page: Page) {
	const callCounts = {
		sessionEvents: 0,
		graphCandidates: 0,
		resumeSnapshot: 0
	};

	await page.route('**/api/**', async (route) => {
		const requestUrl = new URL(route.request().url());
		const json = (payload: unknown, status = 200) =>
			route.fulfill({
				status,
				contentType: 'application/json',
				headers: { 'X-CSRF-Token': 'spike-csrf-token' },
				body: JSON.stringify(payload)
			});

		if (requestUrl.pathname === '/api/auth/me') {
			return json({ user });
		}
		if (requestUrl.pathname === '/api/workbench/dev-mode/status') {
			return json({
				mode: 'settings',
				overallStatus: 'configured',
				components: [],
				credentials: {},
				sources: {},
				dataRoots: { dataRoots: {} }
			});
		}
		if (requestUrl.pathname === '/api/workbench/sessions') {
			return json({ sessions: [session] });
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}`) {
			return json(session);
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/final-top10`) {
			return json({
				items: [],
				coverageStatus: 'complete',
				finalizationRevision: 1
			});
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/candidates`) {
			return json({ items: [reviewCandidate] });
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/events`) {
			callCounts.sessionEvents += 1;
			return json({ events });
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/graph-candidates`) {
			callCounts.graphCandidates += 1;
			return json({
				nodeId: requestUrl.searchParams.get('node_id') ?? 'unknown',
				nodeScope: {
					sessionId: SESSION_ID,
					source: 'all',
					roundId: null,
					nodeKind: 'final'
				},
				items: [graphCandidate],
				nextCursor: null,
				totalSourceResults: 1,
				totalGraphCandidates: 1,
				totalEstimate: 1,
				coverage: {
					nodeId: requestUrl.searchParams.get('node_id') ?? 'unknown',
					totalSourceResults: 1,
					totalGraphCandidates: 1,
					matchedReviewItems: 1,
					missingSafeIdentity: 0,
					missingSnapshot: 0,
					forbiddenSnapshot: 0
				},
				truncated: false,
				generatedAt: '2026-05-10T00:05:00Z',
				recoveryState: 'ready',
				recoveryReason: null
			});
		}
		if (
			requestUrl.pathname ===
			`/api/workbench/sessions/${SESSION_ID}/graph-candidates/${GRAPH_CANDIDATE_ID}/resume-snapshot`
		) {
			callCounts.resumeSnapshot += 1;
			return json(resumeSnapshot);
		}

		return json({ detail: `Unhandled mock route ${requestUrl.pathname}` }, 404);
	});

	return callCounts;
}

async function boundingBox(locator: ReturnType<Page['locator']>) {
	const box = await locator.boundingBox();
	expect(box).not.toBeNull();
	return box!;
}

async function transformStyle(locator: ReturnType<Page['locator']>) {
	return locator.evaluate((element) => getComputedStyle(element).transform);
}
