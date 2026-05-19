import { createApiClient, requireData } from './client';
import type { components } from './schema';

type BootstrapAdminInput = components['schemas']['WorkbenchBootstrapRequest'];
type LoginInput = components['schemas']['WorkbenchLoginRequest'];
type WorkbenchSessionCreateInput = components['schemas']['WorkbenchSessionCreateRequest'];
type WorkbenchRequirementTriageUpdateInput =
	components['schemas']['WorkbenchRequirementTriageUpdateRequest'];
type LiepinPolicyUpdateInput = components['schemas']['WorkbenchSourceRunPolicyUpdateRequest'];
type WorkbenchCandidateReviewItemUpdateInput =
	components['schemas']['WorkbenchCandidateReviewItemUpdateRequest'];
type WorkbenchDetailOpenRequestCreateInput =
	components['schemas']['WorkbenchDetailOpenRequestCreateRequest'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];
type GraphCandidateQuery = {
	node_id: string;
	limit: number;
	cursor?: string | null;
};

const EVENT_PAGE_LIMIT = 200;
const EVENT_MAX_PAGES = 25;

export const api = createApiClient().client;

export async function bootstrapAdmin(input: BootstrapAdminInput) {
	return requireData(await api.POST('/api/auth/bootstrap', { body: input }));
}

export async function getMe() {
	return requireData(await api.GET('/api/auth/me'));
}

export async function login(input: LoginInput) {
	const result = await api.POST('/api/auth/login', { body: input });
	if (!result.response.ok) {
		requireData(result);
	}
}

export async function logout() {
	const result = await api.POST('/api/auth/logout');
	if (!result.response.ok) {
		requireData(result);
	}
}

export async function listSessions() {
	return requireData(await api.GET('/api/workbench/sessions'));
}

export async function createSession(input: WorkbenchSessionCreateInput) {
	return requireData(await api.POST('/api/workbench/sessions', { body: input }));
}

export async function getSession(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function getDevModeStatus() {
	return requireData(await api.GET('/api/workbench/dev-mode/status'));
}

export async function prepareRequirementTriage(sessionId: string) {
	return requireData(
		await api.POST('/api/workbench/sessions/{session_id}/triage/prepare', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function updateRequirementTriage(
	sessionId: string,
	input: WorkbenchRequirementTriageUpdateInput
) {
	return requireData(
		await api.PUT('/api/workbench/sessions/{session_id}/triage', {
			params: { path: { session_id: sessionId } },
			body: input
		})
	);
}

export async function approveRequirementTriage(sessionId: string) {
	return requireData(
		await api.POST('/api/workbench/sessions/{session_id}/triage/approve', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function startSessionSourceRuns(sessionId: string) {
	return requireData(
		await api.POST('/api/workbench/sessions/{session_id}/start', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function listCandidateReviewItems(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}/candidates', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function listFinalTopCandidates(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}/final-top10', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function updateCandidateReviewItem(
	sessionId: string,
	reviewItemId: string,
	input: WorkbenchCandidateReviewItemUpdateInput
) {
	return requireData(
		await api.PUT('/api/workbench/sessions/{session_id}/candidates/{review_item_id}', {
			params: { path: { session_id: sessionId, review_item_id: reviewItemId } },
			body: input
		})
	);
}

export async function openCandidateProviderAction(sessionId: string, reviewItemId: string) {
	return requireData(
		await api.POST(
			'/api/workbench/sessions/{session_id}/candidates/{review_item_id}/provider-actions/open',
			{
				params: { path: { session_id: sessionId, review_item_id: reviewItemId } }
			}
		)
	);
}

export async function createDetailOpenRequest(
	sessionId: string,
	reviewItemId: string,
	input: WorkbenchDetailOpenRequestCreateInput
) {
	return requireData(
		await api.POST(
			'/api/workbench/sessions/{session_id}/candidates/{review_item_id}/detail-open-requests',
			{
				params: { path: { session_id: sessionId, review_item_id: reviewItemId } },
				body: input
			}
		)
	);
}

export async function listDetailOpenRequests(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/detail-open-requests', {
			params: { query: { session_id: sessionId } }
		})
	);
}

export async function approveDetailOpenRequest(requestId: string) {
	return requireData(
		await api.POST('/api/workbench/detail-open-requests/{request_id}/approve', {
			params: { path: { request_id: requestId } }
		})
	);
}

export async function rejectDetailOpenRequest(requestId: string, reason: string) {
	return requireData(
		await api.POST('/api/workbench/detail-open-requests/{request_id}/reject', {
			params: { path: { request_id: requestId } },
			body: { reason }
		})
	);
}

export async function listSourceConnections() {
	return requireData(await api.GET('/api/workbench/source-connections'));
}

export async function getSourceConnection(connectionId: string) {
	return requireData(
		await api.GET('/api/workbench/source-connections/{connection_id}', {
			params: { path: { connection_id: connectionId } }
		})
	);
}

export async function createLiepinConnection() {
	return requireData(await api.POST('/api/workbench/source-connections/liepin'));
}

export async function getLiepinSourceRunPolicy(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}/source-runs/liepin/policy', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function updateLiepinSourceRunPolicy(
	sessionId: string,
	input: LiepinPolicyUpdateInput
) {
	return requireData(
		await api.PUT('/api/workbench/sessions/{session_id}/source-runs/liepin/policy', {
			params: { path: { session_id: sessionId } },
			body: input
		})
	);
}

export async function listGraphCandidates(
	sessionId: string,
	nodeId: string,
	cursor?: string,
	limit = 50
) {
	const query: GraphCandidateQuery = { node_id: nodeId, limit };
	if (cursor !== undefined) {
		query.cursor = cursor;
	}

	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}/graph-candidates', {
			params: {
				path: { session_id: sessionId },
				query
			}
		})
	);
}

export async function getGraphCandidateResumeSnapshot(sessionId: string, graphCandidateId: string) {
	return requireData(
		await api.GET(
			'/api/workbench/sessions/{session_id}/graph-candidates/{graph_candidate_id}/resume-snapshot',
			{
				params: {
					path: {
						session_id: sessionId,
						graph_candidate_id: graphCandidateId
					}
				}
			}
		)
	);
}

export async function listSessionEvents(sessionId: string, afterSeq = 0) {
	const events: WorkbenchEvent[] = [];
	let cursor = afterSeq;

	for (let pageIndex = 0; pageIndex < EVENT_MAX_PAGES; pageIndex += 1) {
		const page = requireData(
			await api.GET('/api/workbench/sessions/{session_id}/events', {
				params: {
					path: { session_id: sessionId },
					query: { after_seq: cursor, limit: EVENT_PAGE_LIMIT }
				}
			})
		);
		events.push(...page.events);
		if (page.events.length < EVENT_PAGE_LIMIT) {
			break;
		}
		const lastEvent = page.events.at(-1);
		if (!lastEvent) {
			break;
		}
		cursor = lastEvent.globalSeq;
	}

	return { events };
}
