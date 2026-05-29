import { expect, type Page, test } from '@playwright/test';

const SESSION_ID = 'session-dev-mode';
const RAW_LEAK_STRINGS = [
	'secret-token',
	'cookie',
	'Authorization',
	'raw_provider_payload',
	'identity-1',
	'review-cts',
	'review-liepin',
	'ev-cts',
	'ev-liepin'
];

const user = {
	userId: 'user-dev',
	email: 'dev@example.com',
	displayName: 'Dev Recruiter',
	role: 'admin',
	workspaceId: 'workspace-dev'
};

type RequirementSheetFixture = {
	job_title: string;
	title_anchor_terms: string[];
	title_anchor_rationale: string;
	role_summary: string;
	must_have_capabilities: string[];
	preferred_capabilities: string[];
	exclusion_signals: string[];
	hard_constraints: Record<string, unknown>;
	preferences: { preferred_query_terms: string[] };
	initial_query_term_pool: Array<{
		term: string;
		source: 'job_title' | 'jd' | 'notes' | 'reflection' | 'candidate_feedback';
		category: 'role_anchor' | 'domain' | 'tooling' | 'expansion' | 'company';
		priority: number;
		evidence: string;
		first_added_round: number;
		active: boolean;
		retrieval_role: string;
		queryability: string;
		family: string;
	}>;
	scoring_rationale: string;
};

type RequirementReviewFixture = {
	session_id: string;
	status: string;
	requirement_sheet: RequirementSheetFixture | null;
	created_at: string;
	updated_at: string;
	approved_at: string | null;
};

type SourceFixture = {
	sourceRunId: string;
	sourceKind: 'cts' | 'liepin';
	label: string;
	status: string;
	authState: string;
	cardsScannedCount: number;
	uniqueCandidatesCount: number;
	detailOpenUsedCount: number;
	detailOpenBlockedCount: number;
	warningCode: string | null;
	warningMessage: string | null;
	connectionId?: string;
	connectionStatus?: string;
	connectionWarningCode?: string | null;
	connectionWarningMessage?: string | null;
};

const devModeStatus = {
	mode: 'settings',
	overallStatus: 'configured',
	components: [
		{
			name: 'text_llm',
			label: 'Text LLM',
			status: 'configured',
			reasonCode: null,
			authNote: 'BYOK ready'
		},
		{
			name: 'liepin_opencli',
			label: '猎聘浏览器通道',
			status: 'needs_setup',
			reasonCode: 'liepin_opencli_extension_disconnected',
			authNote: '请确认本机浏览器助手已连接'
		}
	],
	credentials: {},
	sources: {},
	dataRoots: { dataRoots: {} }
};

const preparedRequirementSheet: RequirementSheetFixture = {
	job_title: 'Dev Mode Svelte UI Engineer',
	title_anchor_terms: ['Svelte UI Engineer'],
	title_anchor_rationale: 'The job title anchors active sourcing.',
	role_summary: 'Build local BYOK Svelte UI for CTS and Liepin sourcing.',
	must_have_capabilities: ['Svelte Workbench', '多源候选人检索'],
	preferred_capabilities: ['Liepin card 判断'],
	exclusion_signals: [],
	hard_constraints: {},
	preferences: { preferred_query_terms: ['Svelte Workbench recruiting agent'] },
	initial_query_term_pool: [
		{
			term: 'Svelte Workbench recruiting agent',
			source: 'notes',
			category: 'domain',
			priority: 1,
			evidence: 'First milestone local demo.',
			first_added_round: 0,
			active: true,
			retrieval_role: 'domain_context',
			queryability: 'admitted',
			family: 'domain.svelteworkbenchrecruitingagent'
		}
	],
	scoring_rationale: 'Prioritize Svelte Workbench and multi-source sourcing evidence.'
};

const draftRequirementReview: RequirementReviewFixture = {
	session_id: SESSION_ID,
	status: 'draft',
	requirement_sheet: null,
	created_at: '2026-05-18T00:00:00Z',
	updated_at: '2026-05-18T00:00:00Z',
	approved_at: null
};

const preparedRequirementReview = {
	...draftRequirementReview,
	requirement_sheet: preparedRequirementSheet,
	updated_at: '2026-05-18T00:01:00Z'
};

const approvedRequirementReview = {
	...preparedRequirementReview,
	status: 'approved',
	approved_at: '2026-05-18T00:01:00Z'
};

const queuedSources: SourceFixture[] = [
	{
		sourceRunId: 'src-cts-dev',
		sourceKind: 'cts',
		label: 'CTS',
		status: 'queued',
		authState: 'not_required',
		cardsScannedCount: 0,
		uniqueCandidatesCount: 0,
		detailOpenUsedCount: 0,
		detailOpenBlockedCount: 0,
		warningCode: null,
		warningMessage: null
	},
	{
		sourceRunId: 'src-liepin-dev',
		sourceKind: 'liepin',
		label: '猎聘',
		status: 'queued',
		authState: 'login_required',
		cardsScannedCount: 0,
		uniqueCandidatesCount: 0,
		detailOpenUsedCount: 0,
		detailOpenBlockedCount: 0,
		warningCode: null,
		warningMessage: null,
		connectionId: 'conn-liepin-dev',
		connectionStatus: 'connected',
		connectionWarningCode: null,
		connectionWarningMessage: null
	}
];

const completedSources: SourceFixture[] = [
	{
		...queuedSources[0]!,
		status: 'completed',
		cardsScannedCount: 10,
		uniqueCandidatesCount: 8
	},
	{
		...queuedSources[1]!,
		status: 'blocked',
		cardsScannedCount: 18,
		uniqueCandidatesCount: 4,
		detailOpenBlockedCount: 2,
		warningCode: 'blocked_backend_unavailable',
		warningMessage: null
	}
];

const runtimeSourceState = {
	sessionId: SESSION_ID,
	status: 'degraded',
	coverageStatus: 'degraded',
	finalizationRevision: 1,
	finalizationReasonCode: 'source_lane_degraded',
	identityMergeCount: 1,
	ambiguousDuplicateCount: 0,
	canonicalResumeSelectedCount: 1,
	sources: [
		{
			sourceKind: 'cts',
			status: 'completed',
			cardsSeenCount: 10,
			cardsFilteredCount: 1,
			candidatesCount: 8,
			detailRecommendationsCount: 0,
			detailState: null,
			reasonCode: null,
			lastEventType: 'source_lane_completed',
			lastEventSeq: 2,
			updatedAt: '2026-05-18T00:02:00Z'
		},
		{
			sourceKind: 'liepin',
			status: 'blocked',
			cardsSeenCount: 18,
			cardsFilteredCount: 5,
			candidatesCount: 4,
			detailRecommendationsCount: 2,
			detailState: 'recommended',
			reasonCode: 'blocked_backend_unavailable',
			lastEventType: 'source_lane_blocked',
			lastEventSeq: 3,
			updatedAt: '2026-05-18T00:03:00Z'
		}
	]
};

const finalTop10 = {
	items: [
		{
			reviewItemId: 'review-canonical',
			runtimeIdentityId: 'identity-1',
			canonicalReviewItemId: 'review-canonical',
			mergedReviewItemIds: ['review-cts', 'review-liepin'],
			rank: 1,
			displayName: 'Candidate A',
			title: 'Senior Frontend Platform Engineer',
			company: 'SearchCo',
			location: 'Shanghai',
			summary: 'CTS and Liepin card both matched the same identity.',
			aggregateScore: 92,
			fitBucket: 'fit',
			whySelected: 'Runtime selected this candidate for agent workflow depth.',
			riskFlags: ['management scope unclear'],
			matchedMustHaves: ['Python backend', 'distributed systems'],
			matchedPreferences: ['agent tooling'],
			strengths: ['Strong backend systems'],
			weaknesses: ['Needs leadership calibration'],
			sourceRound: 2,
			sourceBadges: ['CTS final', 'Liepin card', 'Multiple sources'],
			evidenceLevel: 'final',
			sourceEvidence: [
				{
					evidenceId: 'ev-cts',
					sourceRunId: 'src-cts-dev',
					sourceKind: 'cts',
					evidenceLevel: 'final',
					score: 92,
					fitBucket: 'fit'
				},
				{
					evidenceId: 'ev-liepin',
					sourceRunId: 'src-liepin-dev',
					sourceKind: 'liepin',
					evidenceLevel: 'card',
					score: 88,
					fitBucket: 'fit'
				}
			]
		}
	],
	coverageStatus: 'degraded',
	finalizationRevision: 1
};

test.describe('Dev-mode BYOK dual-source Workbench', () => {
	test('creates a dual-source session and shows degraded Liepin coverage without leaking raw data', async ({
		page
	}) => {
		await mockDevModeWorkbenchApi(page);
		await page.setViewportSize({ width: 1440, height: 920 });
		await page.goto('/sessions');

		await expect(page.getByRole('heading', { name: '本地运行准备' })).toHaveCount(0);
		await expect(
			page.getByText(
				new RegExp(
					`${['Liepin', 'Pi', 'Agent'].join(' ')}|${'Doko' + 'Bot'}|${'doko' + 'bot'}|${'pi' + '_agent'}`,
					'i'
				)
			)
		).toHaveCount(0);
		await page.getByLabel('岗位名称').fill('Dev Mode Svelte UI Engineer');
		await page.getByLabel('JD').fill('Build a local BYOK Svelte UI for CTS and Liepin sourcing.');
		await page.getByLabel('补充说明').fill('First milestone local demo.');
		await page.getByRole('button', { name: '创建会话' }).click();

		await expect(page.getByRole('heading', { name: 'Dev Mode Svelte UI Engineer' })).toBeVisible();
		await page.getByRole('button', { name: '提取需求' }).click();
		await expect(page.getByText('Svelte Workbench', { exact: false }).first()).toBeVisible();
		await page.getByRole('button', { name: '确认需求' }).click();
		await page.getByRole('button', { name: '启动检索' }).click();

		await expect(page.getByText('CTS 最终', { exact: true })).toBeVisible();
		await expect(page.getByText('猎聘卡片', { exact: true })).toBeVisible();
		await expect(page.getByTestId('candidate-card-identity-1').getByText('多源')).toBeVisible();
		await expect(page.getByText('Candidate A')).toBeVisible();
		await expect(page.getByTestId('source-card-liepin').getByText('通道不可用')).toBeVisible();
		await expect(page.getByText('Liepin 浏览器执行暂不可用。')).toBeVisible();
		await expect(page.getByTestId('source-card-liepin').getByText('详情')).toBeVisible();
		await expect(page.getByText('覆盖不完整', { exact: false })).toBeVisible();

		for (const raw of RAW_LEAK_STRINGS) {
			await expect(page.getByText(raw, { exact: false })).toHaveCount(0);
		}
		await assertNoHorizontalOverflow(page);

		await page.setViewportSize({ width: 390, height: 860 });
		await expect(page.getByLabel('最终短名单')).toBeVisible();
		await assertNoHorizontalOverflow(page);
	});
});

async function mockDevModeWorkbenchApi(page: Page) {
	let sessionCreated = false;
	let requirementReview = draftRequirementReview;
	let sources = queuedSources;
	let sourceState: typeof runtimeSourceState = {
		...runtimeSourceState,
		status: 'pending',
		coverageStatus: 'pending',
		sources: runtimeSourceState.sources.map((source) => ({ ...source, status: 'queued' }))
	};

	await page.route('**/api/**', async (route) => {
		const requestUrl = new URL(route.request().url());
		const json = (payload: unknown, status = 200) =>
			route.fulfill({
				status,
				contentType: 'application/json',
				headers: { 'X-CSRF-Token': 'dev-mode-csrf' },
				body: JSON.stringify(payload)
			});

		if (requestUrl.pathname === '/api/auth/me') {
			return json({ user });
		}
		if (requestUrl.pathname === '/api/workbench/dev-mode/status') {
			return json(devModeStatus);
		}
		if (requestUrl.pathname === '/api/workbench/sessions') {
			if (route.request().method() === 'POST') {
				sessionCreated = true;
				return json(buildSession({ requirementReview, sources, sourceState }), 201);
			}
			return json({
				sessions: sessionCreated ? [buildSession({ requirementReview, sources, sourceState })] : []
			});
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}`) {
			return json(buildSession({ requirementReview, sources, sourceState }));
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/requirements/prepare`) {
			requirementReview = preparedRequirementReview;
			return json(requirementReview);
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/requirements/approve`) {
			requirementReview = approvedRequirementReview;
			return json(requirementReview);
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/start`) {
			sources = completedSources;
			sourceState = runtimeSourceState;
			return json(
				{
					sessionId: SESSION_ID,
					runtimeJob: {
						jobId: 'rtjob-dual-source',
						status: 'queued',
						sourceKinds: sources.map((source) => source.sourceKind as 'cts' | 'liepin'),
						attemptCount: 0,
						errorMessage: null,
						createdAt: '2026-05-26T00:00:00Z',
						updatedAt: '2026-05-26T00:00:00Z'
					},
					blockedSources: []
				},
				202
			);
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/candidates`) {
			return json({ items: [] });
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/final-top10`) {
			return json(
				sourceState.coverageStatus === 'pending'
					? { items: [], coverageStatus: 'pending', finalizationRevision: null }
					: finalTop10
			);
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/events`) {
			return json({ events: [] });
		}
		if (requestUrl.pathname === '/api/workbench/detail-open-requests') {
			return json({ requests: [] });
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/runtime-graph`) {
			return json(runtimeGraph(sourceState));
		}
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/graph-candidates`) {
			return json({
				nodeId: requestUrl.searchParams.get('node_id') ?? 'unknown',
				nodeScope: { sessionId: SESSION_ID, source: 'all', roundId: null, nodeKind: 'final' },
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
				generatedAt: '2026-05-18T00:05:00Z',
				recoveryState: 'ready',
				recoveryReason: null
			});
		}
		return json({ detail: `Unhandled mock route ${requestUrl.pathname}` }, 404);
	});
}

function buildSession({
	requirementReview,
	sources,
	sourceState
}: {
	requirementReview: RequirementReviewFixture;
	sources: typeof queuedSources;
	sourceState: typeof runtimeSourceState;
}) {
	return {
		sessionId: SESSION_ID,
		workspaceId: 'workspace-dev',
		ownerUserId: 'user-dev',
		jobTitle: 'Dev Mode Svelte UI Engineer',
		jdText: 'Build a local BYOK Svelte UI for CTS and Liepin sourcing.',
		notes: 'First milestone local demo.',
		status: 'draft',
		requirement_review: requirementReview,
		sourceRuns: sources,
		sourceCards: sources,
		runtimeSourceState: sourceState
	};
}

function runtimeGraph(sourceState: typeof runtimeSourceState) {
	const liepinStatus = sourceState.coverageStatus === 'pending' ? 'queued' : 'blocked';
	const finalStatus = sourceState.coverageStatus === 'pending' ? 'queued' : 'degraded';
	return {
		sessionId: SESSION_ID,
		generatedAt: '2026-05-18T00:05:00Z',
		completionText: sourceState.coverageStatus === 'pending' ? null : 'CTS 已完成，猎聘通道降级。',
		nodes: [
			runtimeGraphNode(
				`${SESSION_ID}:job`,
				'job',
				'Dev Mode Svelte UI Engineer',
				'completed',
				'all'
			),
			runtimeGraphNode(`${SESSION_ID}:cts`, 'source_result', 'CTS 候选人', 'completed', 'cts'),
			runtimeGraphNode(
				`${SESSION_ID}:liepin`,
				'source_result',
				'猎聘候选人',
				liepinStatus,
				'liepin'
			),
			runtimeGraphNode(`${SESSION_ID}:final`, 'final', '最终短名单', finalStatus, 'all')
		],
		edges: [
			{
				edgeId: `${SESSION_ID}:job-cts`,
				fromNodeId: `${SESSION_ID}:job`,
				toNodeId: `${SESSION_ID}:cts`,
				label: '检索'
			},
			{
				edgeId: `${SESSION_ID}:job-liepin`,
				fromNodeId: `${SESSION_ID}:job`,
				toNodeId: `${SESSION_ID}:liepin`,
				label: '检索'
			},
			{
				edgeId: `${SESSION_ID}:cts-final`,
				fromNodeId: `${SESSION_ID}:cts`,
				toNodeId: `${SESSION_ID}:final`,
				label: '合并'
			},
			{
				edgeId: `${SESSION_ID}:liepin-final`,
				fromNodeId: `${SESSION_ID}:liepin`,
				toNodeId: `${SESSION_ID}:final`,
				label: '合并'
			}
		]
	};
}

function runtimeGraphNode(
	nodeId: string,
	kind: string,
	label: string,
	status: string,
	sourceKind: 'cts' | 'liepin' | 'all'
) {
	return {
		nodeId,
		kind,
		label,
		summaryText: label,
		status,
		stage: kind === 'final' ? 'finalization' : kind === 'job' ? 'intake' : 'retrieval',
		sourceKind,
		lane: sourceKind === 'all' ? 'shared' : sourceKind,
		roundNo: kind === 'job' ? 0 : kind === 'final' ? 2 : 1,
		candidateScope: {
			scopeKind: kind === 'final' ? 'final' : kind === 'job' ? 'none' : 'round_recall',
			sourceKind,
			roundNo: kind === 'job' ? null : kind === 'final' ? 2 : 1,
			reason: null
		},
		eventIds: [],
		detailSections: []
	};
}

async function assertNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.body.scrollWidth - window.innerWidth);
	expect(overflow).toBeLessThanOrEqual(1);
}
