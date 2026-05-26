import { expect, type Page, type Route } from '@playwright/test';

export const SESSION_IDS = {
	completed: 'session-parity-completed',
	blocked: 'session-parity-liepin-blocked',
	partial: 'session-parity-liepin-partial'
} as const;

export type ParitySourceState = 'completed' | 'blocked' | 'partial';

type MockOptions = {
	authenticated?: boolean;
	sourceState?: ParitySourceState;
};

export type ParityMockCalls = {
	all: string[];
	protectedBeforeAuth: string[];
	forbidden: string[];
	eventStreams: string[];
	unhandled: string[];
};

const USER = {
	userId: 'user-parity',
	email: 'parity@example.com',
	displayName: 'Parity Recruiter',
	role: 'admin',
	workspaceId: 'workspace-parity'
};

const GRAPH_CANDIDATE_ID = 'graph-parity-final-1';
const REVIEW_ITEM_ID = 'review-parity-final-1';
const DETAIL_REQUEST_ID = 'detail-request-parity-1';
const CONNECTION_ID = 'conn-liepin-parity';

const protectedApiPrefixes = [
	'/api/workbench/sessions',
	'/api/workbench/events',
	'/api/workbench/source-connections',
	'/api/workbench/detail-open-requests',
	'/api/workbench/settings'
];

const forbiddenManagedBrowserPatterns = [
	/^\/api\/workbench\/source-connections\/[^/]+\/login$/,
	/^\/api\/workbench\/source-connections\/[^/]+\/login\/frame$/,
	/^\/api\/workbench\/source-connections\/[^/]+\/login\/snapshot$/,
	/^\/api\/workbench\/source-connections\/[^/]+\/login\/input$/,
	/^\/api\/workbench\/source-connections\/[^/]+\/login\/complete$/
];

export async function mockParityApi(page: Page, options: MockOptions = {}) {
	const authenticated = options.authenticated ?? true;
	const activeSourceState = options.sourceState ?? 'completed';
	const calls: ParityMockCalls = {
		all: [],
		protectedBeforeAuth: [],
		forbidden: [],
		eventStreams: [],
		unhandled: []
	};

	await page.route('**/api/**', async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		const path = url.pathname;
		const method = request.method();
		const callKey = `${method} ${path}`;
		calls.all.push(callKey);

		if (forbiddenManagedBrowserPatterns.some((pattern) => pattern.test(path))) {
			calls.forbidden.push(callKey);
			return json(route, { detail: `Forbidden legacy managed-browser route ${path}` }, 500);
		}

		if (!authenticated && isProtectedApi(path)) {
			calls.protectedBeforeAuth.push(callKey);
			return json(route, { detail: 'Unauthorized' }, 401);
		}

		if (path === '/api/auth/me' && method === 'GET') {
			return authenticated
				? json(route, { user: USER })
				: json(route, { detail: 'Unauthorized' }, 401);
		}
		if (path === '/api/auth/login' && method === 'POST') {
			return json(route, { user: USER });
		}
		if (path === '/api/auth/logout' && method === 'POST') {
			return json(route, {});
		}
		if (path === '/api/auth/bootstrap' && method === 'POST') {
			return json(route, { user: USER });
		}

		if (path === '/api/workbench/events/stream' && method === 'GET') {
			calls.eventStreams.push(path);
			return eventStream(route);
		}
		const sessionStreamMatch = path.match(/^\/api\/workbench\/sessions\/([^/]+)\/events\/stream$/);
		if (sessionStreamMatch?.[1] && method === 'GET') {
			calls.eventStreams.push(path);
			return eventStream(route);
		}

		if (path === '/api/workbench/sessions') {
			if (method === 'GET') {
				return json(route, { sessions: buildSessions(activeSourceState) });
			}
			if (method === 'POST') {
				return json(route, buildSession(SESSION_IDS.completed, activeSourceState), 201);
			}
		}

		const sessionId = matchPath(path, /^\/api\/workbench\/sessions\/([^/]+)$/);
		if (sessionId && method === 'GET') {
			return json(route, buildSession(sessionId, stateForSession(sessionId, activeSourceState)));
		}

		const candidatesSessionId = matchPath(
			path,
			/^\/api\/workbench\/sessions\/([^/]+)\/candidates$/
		);
		if (candidatesSessionId && method === 'GET') {
			return json(route, { items: [reviewCandidate(candidatesSessionId)] });
		}

		const finalTopSessionId = matchPath(path, /^\/api\/workbench\/sessions\/([^/]+)\/final-top10$/);
		if (finalTopSessionId && method === 'GET') {
			return json(route, finalTop10(finalTopSessionId));
		}

		const sessionEventsId = matchPath(path, /^\/api\/workbench\/sessions\/([^/]+)\/events$/);
		if (sessionEventsId && method === 'GET') {
			return json(route, { events: events(sessionEventsId) });
		}

		const graphCandidatesSessionId = matchPath(
			path,
			/^\/api\/workbench\/sessions\/([^/]+)\/graph-candidates$/
		);
		if (graphCandidatesSessionId && method === 'GET') {
			return json(
				route,
				graphCandidates(graphCandidatesSessionId, url.searchParams.get('node_id'))
			);
		}

		const graphSnapshotMatch = path.match(
			/^\/api\/workbench\/sessions\/([^/]+)\/graph-candidates\/([^/]+)\/resume-snapshot$/
		);
		if (graphSnapshotMatch?.[1] && graphSnapshotMatch[2] && method === 'GET') {
			return json(route, resumeSnapshot(graphSnapshotMatch[2]));
		}

		const sourcePolicySessionId = matchPath(
			path,
			/^\/api\/workbench\/sessions\/([^/]+)\/source-runs\/liepin\/policy$/
		);
		if (sourcePolicySessionId && (method === 'GET' || method === 'PUT')) {
			return json(route, sourcePolicy(sourcePolicySessionId));
		}

		if (path === '/api/workbench/detail-open-requests' && method === 'GET') {
			return json(route, { requests: [detailOpenRequest(SESSION_IDS.completed)] });
		}
		const detailAction = path.match(
			/^\/api\/workbench\/detail-open-requests\/([^/]+)\/(approve|reject)$/
		);
		if (detailAction?.[1] && (method === 'POST' || method === 'PUT')) {
			return json(route, { ...detailOpenRequest(SESSION_IDS.completed), status: detailAction[2] });
		}

		if (path === '/api/workbench/source-connections' && method === 'GET') {
			return json(route, { connections: [sourceConnection()] });
		}
		if (path === '/api/workbench/source-connections/liepin' && method === 'POST') {
			return json(route, sourceConnection(), 201);
		}
		const connectionId = matchPath(path, /^\/api\/workbench\/source-connections\/([^/]+)$/);
		if (connectionId && (method === 'GET' || method === 'PATCH' || method === 'DELETE')) {
			return json(route, sourceConnection(connectionId));
		}

		const startSessionId = matchPath(path, /^\/api\/workbench\/sessions\/([^/]+)\/start$/);
		if (startSessionId && method === 'POST') {
			const sourceKinds = buildSourceCards(stateForSession(startSessionId, activeSourceState)).map(
				(source) => source.sourceKind as 'cts' | 'liepin'
			);
			return json(route, {
				sessionId: startSessionId,
				runtimeJob: {
					jobId: `rtjob-${startSessionId}`,
					status: 'queued',
					sourceKinds,
					attemptCount: 0,
					errorMessage: null,
					createdAt: '2026-05-26T00:00:00Z',
					updatedAt: '2026-05-26T00:00:00Z'
				},
				blockedSources: []
			});
		}

		const requirementPrepareId = matchPath(
			path,
			/^\/api\/workbench\/sessions\/([^/]+)\/requirements\/prepare$/
		);
		if (requirementPrepareId && method === 'POST') {
			return json(route, requirementReview(requirementPrepareId, 'draft'));
		}
		const requirementId = matchPath(path, /^\/api\/workbench\/sessions\/([^/]+)\/requirements$/);
		if (requirementId && method === 'PUT') {
			return json(route, requirementReview(requirementId, 'draft'));
		}
		const requirementApproveId = matchPath(
			path,
			/^\/api\/workbench\/sessions\/([^/]+)\/requirements\/approve$/
		);
		if (requirementApproveId && method === 'POST') {
			return json(route, requirementReview(requirementApproveId, 'approved'));
		}

		const candidateUpdate = path.match(
			/^\/api\/workbench\/sessions\/([^/]+)\/candidates\/([^/]+)$/
		);
		if (candidateUpdate?.[1] && candidateUpdate[2] && (method === 'PATCH' || method === 'PUT')) {
			return json(route, reviewCandidate(candidateUpdate[1], candidateUpdate[2]));
		}
		const providerOpen = path.match(
			/^\/api\/workbench\/sessions\/([^/]+)\/candidates\/([^/]+)\/provider-actions\/open$/
		);
		if (providerOpen?.[1] && providerOpen[2] && method === 'POST') {
			return json(route, {
				actionKind: 'managed_browser',
				sourceKind: 'liepin',
				connectionId: CONNECTION_ID,
				reviewItemId: providerOpen[2],
				budgetImpact: 'none',
				message: 'Safe provider action is available after backend approval.'
			});
		}
		const candidateDetailRequest = path.match(
			/^\/api\/workbench\/sessions\/([^/]+)\/candidates\/([^/]+)\/detail-open-requests$/
		);
		if (candidateDetailRequest?.[1] && candidateDetailRequest[2] && method === 'POST') {
			return json(route, detailOpenRequest(candidateDetailRequest[1]));
		}

		calls.unhandled.push(callKey);
		return json(route, { detail: `Unhandled parity mock route ${method} ${path}` }, 404);
	});

	return calls;
}

export async function expectNoForbiddenRoutes(calls: ParityMockCalls) {
	expect(calls.forbidden, 'legacy managed-browser endpoint calls').toEqual([]);
}

function isProtectedApi(path: string) {
	return protectedApiPrefixes.some((prefix) => path.startsWith(prefix));
}

function matchPath(path: string, pattern: RegExp) {
	return path.match(pattern)?.[1] ?? null;
}

function stateForSession(sessionId: string, fallback: ParitySourceState): ParitySourceState {
	if (sessionId === SESSION_IDS.blocked) {
		return 'blocked';
	}
	if (sessionId === SESSION_IDS.partial) {
		return 'partial';
	}
	if (sessionId === SESSION_IDS.completed) {
		return 'completed';
	}
	return fallback;
}

function buildSessions(activeSourceState: ParitySourceState) {
	return [
		buildSession(SESSION_IDS.completed, stateForSession(SESSION_IDS.completed, activeSourceState)),
		buildSession(SESSION_IDS.blocked, stateForSession(SESSION_IDS.blocked, activeSourceState)),
		buildSession(SESSION_IDS.partial, stateForSession(SESSION_IDS.partial, activeSourceState))
	];
}

function buildSession(sessionId: string, sourceState: ParitySourceState) {
	const status =
		sourceState === 'blocked' ? 'degraded' : sourceState === 'partial' ? 'running' : 'draft';
	return {
		sessionId,
		workspaceId: USER.workspaceId,
		ownerUserId: USER.userId,
		jobTitle: titleForState(sourceState),
		jdText:
			'Find a senior product leader who has built AI recruiting workflow products across CTS and Liepin.',
		notes: `Parity fixture: ${sourceState}`,
		status,
		requirement_review: requirementReview(sessionId, 'approved'),
		sourceRuns: buildSourceCards(sourceState),
		sourceCards: buildSourceCards(sourceState),
		runtimeSourceState: runtimeSourceState(sessionId, sourceState)
	};
}

function titleForState(sourceState: ParitySourceState) {
	if (sourceState === 'blocked') {
		return 'AI Recruiting Platform VP - Liepin Login Required';
	}
	if (sourceState === 'partial') {
		return 'AI Recruiting Platform VP - Liepin Partial';
	}
	return 'AI Recruiting Platform VP';
}

function requirementReview(sessionId: string, status: 'draft' | 'approved') {
	const sourceState = stateForSession(sessionId, 'completed');
	const jobTitle = titleForState(sourceState);
	return {
		session_id: sessionId,
		status,
		requirement_sheet: {
			job_title: jobTitle,
			title_anchor_terms: ['AI Recruiting Platform VP'],
			title_anchor_rationale: 'The job title anchors active sourcing.',
			role_summary:
				'Find a senior product leader for AI recruiting workflows across CTS and Liepin.',
			must_have_capabilities: [
				'AI platform product leadership',
				'multi-source recruiting workflows'
			],
			preferred_capabilities: ['猎头业务理解', 'workflow automation'],
			exclusion_signals: ['junior IC only'],
			hard_constraints: {},
			preferences: { preferred_query_terms: ['AI recruiting agent', 'talent graph workflow'] },
			initial_query_term_pool: [
				{
					term: 'AI recruiting agent',
					source: 'jd',
					category: 'domain',
					priority: 1,
					evidence: 'AI recruiting workflow products',
					first_added_round: 0,
					active: true,
					retrieval_role: 'domain_context',
					queryability: 'admitted',
					family: 'domain.airecruitingagent'
				}
			],
			scoring_rationale: 'Prioritize AI recruiting workflow leadership evidence.'
		},
		created_at: '2026-05-18T00:00:00Z',
		updated_at: '2026-05-18T00:01:00Z',
		approved_at: status === 'approved' ? '2026-05-18T00:01:00Z' : null
	};
}

function buildSourceCards(sourceState: ParitySourceState) {
	const liepinStatus = sourceState === 'completed' ? 'completed' : sourceState;
	const liepinAuthState = sourceState === 'blocked' ? 'login_required' : 'login_required';
	const liepinWarning =
		sourceState === 'blocked'
			? '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。'
			: sourceState === 'partial'
				? '猎聘已返回有效卡片，详情额度仍待审批。'
				: null;

	return [
		{
			sourceRunId: 'src-cts-parity',
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
			sourceRunId: 'src-liepin-parity',
			sourceKind: 'liepin',
			label: '猎聘',
			status: liepinStatus,
			authState: liepinAuthState,
			cardsScannedCount: sourceState === 'blocked' ? 0 : 33,
			uniqueCandidatesCount: sourceState === 'blocked' ? 0 : 7,
			detailOpenUsedCount: sourceState === 'completed' ? 2 : 0,
			detailOpenBlockedCount: sourceState === 'blocked' ? 2 : 0,
			warningCode: sourceState === 'blocked' ? 'login_required' : null,
			warningMessage: liepinWarning,
			connectionId: CONNECTION_ID,
			connectionStatus: sourceState === 'blocked' ? 'needs_login' : 'connected',
			connectionWarningCode: sourceState === 'blocked' ? 'login_required' : null,
			connectionWarningMessage: liepinWarning
		}
	];
}

function runtimeSourceState(sessionId: string, sourceState: ParitySourceState) {
	return {
		sessionId,
		status:
			sourceState === 'blocked' ? 'degraded' : sourceState === 'partial' ? 'partial' : 'completed',
		coverageStatus:
			sourceState === 'blocked' ? 'degraded' : sourceState === 'partial' ? 'partial' : 'complete',
		finalizationRevision: 1,
		finalizationReasonCode:
			sourceState === 'completed' ? 'source_lanes_completed' : 'source_lane_degraded',
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
				updatedAt: '2026-05-18T00:03:00Z'
			},
			{
				sourceKind: 'liepin',
				status: sourceState === 'completed' ? 'completed' : sourceState,
				cardsSeenCount: sourceState === 'blocked' ? 0 : 33,
				cardsFilteredCount: sourceState === 'blocked' ? 0 : 4,
				candidatesCount: sourceState === 'blocked' ? 0 : 7,
				detailRecommendationsCount: sourceState === 'blocked' ? 0 : 2,
				detailState: sourceState === 'completed' ? 'completed' : 'recommended',
				reasonCode: sourceState === 'blocked' ? 'login_required' : null,
				lastEventType:
					sourceState === 'blocked' ? 'source_lane_blocked' : 'liepin_card_search_completed',
				lastEventSeq: 4,
				updatedAt: '2026-05-18T00:04:00Z'
			}
		]
	};
}

function finalTop10(sessionId: string) {
	return {
		items: [reviewCandidate(sessionId)],
		coverageStatus: 'complete',
		finalizationRevision: 1
	};
}

function reviewCandidate(sessionId: string, reviewItemId = REVIEW_ITEM_ID) {
	return {
		reviewItemId,
		sessionId,
		graphCandidateId: GRAPH_CANDIDATE_ID,
		canExpandResume: true,
		status: 'promising',
		note: 'Strong operator profile, detail already approved.',
		runtimeIdentityId: 'identity-parity-1',
		canonicalReviewItemId: reviewItemId,
		mergedReviewItemIds: [reviewItemId],
		rank: 1,
		displayName: 'Candidate A',
		title: 'VP Product, Talent Intelligence',
		company: 'Enterprise AI Platform',
		location: 'Shanghai',
		summary: 'Led recruiting workflow automation and enterprise search products.',
		aggregateScore: 92,
		fitBucket: 'fit',
		whySelected: 'Runtime selected this candidate for agent workflow depth.',
		riskFlags: ['management scope unclear'],
		sourceRound: 2,
		sourceBadges: ['CTS', 'Liepin'],
		evidenceLevel: 'detail',
		matchedMustHaves: ['Python backend', 'distributed systems'],
		matchedPreferences: ['agent tooling'],
		missingRisks: ['Compensation band needs confirmation'],
		strengths: ['Strong backend systems'],
		weaknesses: ['Needs leadership calibration'],
		evidence: [
			{
				evidenceId: 'ev-liepin-parity',
				sourceRunId: 'src-liepin-parity',
				sourceKind: 'liepin',
				evidenceLevel: 'detail',
				score: 92,
				fitBucket: 'fit'
			}
		],
		sourceEvidence: [
			{
				evidenceId: 'ev-liepin-parity',
				sourceRunId: 'src-liepin-parity',
				sourceKind: 'liepin',
				evidenceLevel: 'detail',
				score: 92,
				fitBucket: 'fit'
			}
		],
		createdAt: '2026-05-18T00:04:00Z',
		updatedAt: '2026-05-18T00:05:00Z'
	};
}

function events(sessionId: string) {
	return [
		{
			globalSeq: 1,
			sessionSeq: 1,
			sessionId,
			sourceRunId: 'src-cts-parity',
			sourceKind: 'cts',
			eventName: 'requirements_approved',
			payload: { message: 'Requirement review approved.' },
			createdAt: '2026-05-18T00:01:00Z'
		}
	];
}

function graphCandidates(sessionId: string, nodeId: string | null) {
	return {
		nodeId: nodeId ?? 'final-shortlist',
		nodeScope: { sessionId, source: 'all', roundId: null, nodeKind: 'final' },
		items: [
			{
				graphCandidateId: GRAPH_CANDIDATE_ID,
				sourceKind: 'liepin',
				sourceRunId: 'src-liepin-parity',
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
				reviewItemId: REVIEW_ITEM_ID,
				evidenceLevel: 'detail',
				detailOpenRequestId: DETAIL_REQUEST_ID,
				canExpandResume: true
			}
		],
		nextCursor: null,
		totalSourceResults: 1,
		totalGraphCandidates: 1,
		totalEstimate: 1,
		coverage: {
			nodeId: nodeId ?? 'final-shortlist',
			totalSourceResults: 1,
			totalGraphCandidates: 1,
			matchedReviewItems: 1,
			missingSafeIdentity: 0,
			missingSnapshot: 0,
			forbiddenSnapshot: 0
		},
		truncated: false,
		generatedAt: '2026-05-18T00:05:00Z',
		recoveryState: 'ready',
		recoveryReason: null
	};
}

function resumeSnapshot(graphCandidateId: string) {
	return {
		graphCandidateId,
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
}

function sourcePolicy(sessionId: string) {
	return {
		sessionId,
		sourceKind: 'liepin',
		detailOpenBudget: 3,
		detailOpenUsedCount: 1,
		detailOpenBlockedCount: 0,
		requiresApproval: true
	};
}

function detailOpenRequest(sessionId: string) {
	return {
		requestId: DETAIL_REQUEST_ID,
		sessionId,
		reviewItemId: REVIEW_ITEM_ID,
		status: 'pending',
		detailOpenMode: 'human_confirm',
		decisionNote: 'Candidate A is within final shortlist range.',
		candidate: {
			reviewItemId: REVIEW_ITEM_ID,
			displayName: 'Candidate A',
			title: 'VP Product',
			company: 'Enterprise AI Platform',
			location: 'Shanghai',
			summary: 'Safe detail request snapshot.',
			aggregateScore: 92,
			evidenceLevel: 'card',
			sourceBadges: ['Liepin card'],
			matchedMustHaves: ['AI recruiting workflow'],
			matchedPreferences: ['Enterprise search'],
			missingRisks: []
		},
		blockedReason: null,
		ledger: null,
		providerAction: null,
		createdAt: '2026-05-18T00:06:00Z',
		updatedAt: '2026-05-18T00:06:00Z'
	};
}

function sourceConnection(connectionId = CONNECTION_ID) {
	return {
		connectionId,
		sourceKind: 'liepin',
		label: 'Liepin parity connection',
		status: 'connected',
		authState: 'login_required',
		createdAt: '2026-05-18T00:00:00Z',
		updatedAt: '2026-05-18T00:00:00Z',
		lastCheckedAt: '2026-05-18T00:00:00Z',
		warningCode: null,
		warningMessage: null
	};
}

function json(route: Route, payload: unknown, status = 200) {
	return route.fulfill({
		status,
		contentType: 'application/json',
		headers: { 'X-CSRF-Token': 'parity-csrf-token' },
		body: JSON.stringify(payload)
	});
}

function eventStream(route: Route) {
	return route.fulfill({
		status: 200,
		contentType: 'text/event-stream',
		body: 'event: ping\ndata: {}\n\n'
	});
}
