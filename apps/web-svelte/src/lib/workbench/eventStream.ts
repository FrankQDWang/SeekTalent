import type { QueryClient } from '@tanstack/svelte-query';
import { workbenchKeys } from '$lib/query/keys';

type CreateWorkbenchEventStreamOptions = {
	queryClient: QueryClient;
	sessionId: string | null;
};

type WorkbenchEventPayload = {
	sessionId?: string | null;
	eventName?: string;
};

const INVALIDATION_DEBOUNCE_MS = 250;
const SESSION_SUMMARY_EVENTS = new Set([
	'requirement_review_updated',
	'requirement_review_approved',
	'runtime_sourcing_queued',
	'runtime_sourcing_started',
	'runtime_sourcing_completed',
	'runtime_sourcing_failed',
	'source_connection_status_changed',
	'source_run_started',
	'source_run_completed',
	'source_run_failed'
]);
const SOURCE_CONNECTION_EVENTS = new Set(['source_connection_status_changed']);

export function createWorkbenchEventStream({
	queryClient,
	sessionId
}: CreateWorkbenchEventStreamOptions) {
	if (typeof EventSource === 'undefined') {
		return;
	}

	let invalidateTimer: ReturnType<typeof setTimeout> | null = null;
	let pendingSessionId = sessionId;
	let pendingFullRefresh = false;
	let pendingEventNames = new Set<string>();
	let source: EventSource | null = null;
	const scheduleInvalidation = (
		targetSessionId: string | null,
		event: WorkbenchEventPayload | null = null,
		fullRefresh = false
	) => {
		pendingSessionId = targetSessionId ?? pendingSessionId;
		pendingFullRefresh = pendingFullRefresh || fullRefresh;
		if (event?.eventName) {
			pendingEventNames.add(event.eventName);
		}
		if (invalidateTimer) {
			return;
		}
		invalidateTimer = setTimeout(() => {
			invalidateTimer = null;
			const eventNames = pendingEventNames;
			pendingEventNames = new Set();
			const fullRefresh = pendingFullRefresh;
			pendingFullRefresh = false;
			invalidateWorkbench(queryClient, pendingSessionId, { eventNames, fullRefresh });
		}, INVALIDATION_DEBOUNCE_MS);
	};
	const url = sessionId
		? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/events/stream`
		: '/api/workbench/events/stream';
	const handleEvent = (message: MessageEvent<string>) => {
		const event = parseEvent(message.data);
		scheduleInvalidation(eventSessionId(event, sessionId), event);
	};
	const closeSource = () => {
		if (!source) {
			return;
		}
		source.removeEventListener('workbench_event', handleEvent);
		source.removeEventListener('message', handleEvent);
		source.close();
		source = null;
	};
	const openSource = () => {
		if (source || documentIsHidden()) {
			return;
		}
		source = new EventSource(url);
		source.addEventListener('workbench_event', handleEvent);
		source.addEventListener('message', handleEvent);
		source.onopen = () => {
			scheduleInvalidation(sessionId, null, true);
		};
		source.onerror = () => {
			if (documentIsHidden()) {
				closeSource();
			}
		};
	};
	const handleVisibilityChange = () => {
		if (documentIsHidden()) {
			closeSource();
			return;
		}
		openSource();
		scheduleInvalidation(sessionId, null, true);
	};

	if (typeof document !== 'undefined') {
		document.addEventListener('visibilitychange', handleVisibilityChange);
	}
	openSource();

	return () => {
		if (invalidateTimer) {
			clearTimeout(invalidateTimer);
			invalidateTimer = null;
		}
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
		}
		closeSource();
	};
}

function parseEvent(data: string): WorkbenchEventPayload {
	try {
		const parsed = JSON.parse(data) as WorkbenchEventPayload;
		return parsed && typeof parsed === 'object' ? parsed : {};
	} catch {
		return {};
	}
}

function eventSessionId(event: WorkbenchEventPayload, activeSessionId: string | null) {
	return event.sessionId ?? activeSessionId;
}

function documentIsHidden(): boolean {
	return typeof document !== 'undefined' && document.hidden;
}

type InvalidationOptions = {
	eventNames: Set<string>;
	fullRefresh: boolean;
};

function invalidateWorkbench(
	queryClient: QueryClient,
	sessionId: string | null,
	{ eventNames, fullRefresh }: InvalidationOptions
) {
	if (!fullRefresh && eventNames.size > 0) {
		invalidateEventUpdates(queryClient, sessionId, eventNames);
		return;
	}
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.globalEvents() });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.settings() });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnections });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnectionRoot() });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.detailOpenRequests() });

	if (!sessionId) {
		return;
	}

	void queryClient.invalidateQueries({ queryKey: workbenchKeys.session(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.candidates(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.finalTop10(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.runtimeGraph(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessionEvents(sessionId, 0) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.graphCandidatesRoot(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.resumeSnapshotRoot(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourcePolicy(sessionId) });
}

function invalidateEventUpdates(
	queryClient: QueryClient,
	sessionId: string | null,
	eventNames: Set<string>
) {
	if (!sessionId) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.globalEvents() });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions });
		if (hasAny(eventNames, SOURCE_CONNECTION_EVENTS)) {
			void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnections });
			void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnectionRoot() });
		}
		return;
	}

	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessionEvents(sessionId, 0) });
	if (needsSessionSummary(eventNames)) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.session(sessionId) });
	}
	if (needsRuntimeGraph(eventNames)) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.runtimeGraph(sessionId) });
	}
	if (needsCandidateSurfaces(eventNames)) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.candidates(sessionId) });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.finalTop10(sessionId) });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.graphCandidatesRoot(sessionId) });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.resumeSnapshotRoot(sessionId) });
	}
	if (needsDetailRequests(eventNames)) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.detailOpenRequests(sessionId) });
	}
	if (hasAny(eventNames, SOURCE_CONNECTION_EVENTS)) {
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnections });
		void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnectionRoot() });
	}
}

function needsSessionSummary(eventNames: Set<string>) {
	return hasAny(eventNames, SESSION_SUMMARY_EVENTS);
}

function needsRuntimeGraph(eventNames: Set<string>) {
	for (const eventName of eventNames) {
		if (
			eventName.startsWith('runtime_') ||
			eventName.startsWith('source_run_') ||
			eventName.startsWith('detail_') ||
			eventName.startsWith('liepin_detail_') ||
			eventName.startsWith('requirement_review_')
		) {
			return true;
		}
	}
	return false;
}

function needsCandidateSurfaces(eventNames: Set<string>) {
	for (const eventName of eventNames) {
		if (
			eventName.includes('candidate') ||
			eventName.includes('source_result') ||
			eventName.includes('merge') ||
			eventName.includes('scoring') ||
			eventName.includes('final') ||
			eventName.includes('detail') ||
			eventName === 'runtime_search_completed' ||
			eventName === 'runtime_sourcing_completed'
		) {
			return true;
		}
	}
	return false;
}

function needsDetailRequests(eventNames: Set<string>) {
	for (const eventName of eventNames) {
		if (eventName.includes('detail')) {
			return true;
		}
	}
	return false;
}

function hasAny(eventNames: Set<string>, targetNames: Set<string>) {
	for (const eventName of eventNames) {
		if (targetNames.has(eventName)) {
			return true;
		}
	}
	return false;
}
