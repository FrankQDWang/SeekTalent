import type { QueryClient } from '@tanstack/svelte-query';
import { workbenchKeys } from '$lib/query/keys';

type CreateWorkbenchEventStreamOptions = {
	queryClient: QueryClient;
	sessionId: string | null;
};

type WorkbenchEventPayload = {
	sessionId?: string | null;
	globalSeq?: number;
	eventName?: string;
	[key: string]: unknown;
};

type WorkbenchEventList = {
	events: WorkbenchEventPayload[];
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
	const handleEvent = (message: MessageEvent<string>) => {
		const event = parseEvent(message.data);
		if (!isUsableStreamEvent(event)) {
			return;
		}
		const appended = appendStreamEvent(queryClient, sessionId, event);
		if (appended) {
			scheduleInvalidation(eventSessionId(event, sessionId), event);
		}
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
		source = new EventSource(eventStreamUrl(queryClient, sessionId));
		source.addEventListener('workbench_event', handleEvent);
		source.addEventListener('message', handleEvent);
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

function eventStreamUrl(queryClient: QueryClient, sessionId: string | null) {
	const baseUrl = sessionId
		? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/events/stream`
		: '/api/workbench/events/stream';
	const afterSeq = latestCachedGlobalSeq(queryClient, sessionId);
	if (afterSeq <= 0) {
		return baseUrl;
	}
	return `${baseUrl}?after_seq=${afterSeq}`;
}

function latestCachedGlobalSeq(queryClient: QueryClient, sessionId: string | null) {
	const cached = getEventCache(queryClient, eventCacheKey(sessionId));
	if (!cached) {
		return 0;
	}
	return cached.events.reduce((latest, event) => Math.max(latest, eventGlobalSeq(event) ?? 0), 0);
}

function appendStreamEvent(
	queryClient: QueryClient,
	sessionId: string | null,
	event: WorkbenchEventPayload
) {
	const globalSeq = eventGlobalSeq(event);
	if (globalSeq === null) {
		return false;
	}
	const key = eventCacheKey(sessionId);
	const cached = getEventCache(queryClient, key);
	if (cached?.events.some((candidate) => eventGlobalSeq(candidate) === globalSeq)) {
		return false;
	}
	setEventCache(queryClient, key, (current) => appendEvent(current, event));
	return true;
}

function appendEvent(
	current: WorkbenchEventList | undefined,
	event: WorkbenchEventPayload
): WorkbenchEventList {
	const events = [...(current?.events ?? []), event].sort(
		(left, right) => (eventGlobalSeq(left) ?? 0) - (eventGlobalSeq(right) ?? 0)
	);
	return { ...(current ?? { events: [] }), events };
}

function eventCacheKey(sessionId: string | null) {
	return sessionId ? workbenchKeys.sessionEvents(sessionId, 0) : workbenchKeys.globalEvents(0);
}

function getEventCache(queryClient: QueryClient, key: ReturnType<typeof eventCacheKey>) {
	const reader = (queryClient as QueryClient & { getQueryData?: (queryKey: typeof key) => unknown })
		.getQueryData;
	const cached = reader?.call(queryClient, key);
	if (
		!cached ||
		typeof cached !== 'object' ||
		!Array.isArray((cached as WorkbenchEventList).events)
	) {
		return undefined;
	}
	return cached as WorkbenchEventList;
}

function setEventCache(
	queryClient: QueryClient,
	key: ReturnType<typeof eventCacheKey>,
	updater: (current: WorkbenchEventList | undefined) => WorkbenchEventList
) {
	const writer = (
		queryClient as QueryClient & {
			setQueryData?: (
				queryKey: typeof key,
				updater: (current: WorkbenchEventList | undefined) => WorkbenchEventList
			) => unknown;
		}
	).setQueryData;
	writer?.call(queryClient, key, updater);
}

function isUsableStreamEvent(event: WorkbenchEventPayload) {
	return eventGlobalSeq(event) !== null && Boolean(event.eventName);
}

function eventGlobalSeq(event: WorkbenchEventPayload) {
	const value = Number(event.globalSeq);
	return Number.isInteger(value) && value >= 0 ? value : null;
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
