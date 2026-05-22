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

export function createWorkbenchEventStream({
	queryClient,
	sessionId
}: CreateWorkbenchEventStreamOptions) {
	if (typeof EventSource === 'undefined') {
		return;
	}

	let invalidateTimer: ReturnType<typeof setTimeout> | null = null;
	let pendingSessionId = sessionId;
	const scheduleInvalidation = (targetSessionId: string | null) => {
		pendingSessionId = targetSessionId ?? pendingSessionId;
		if (invalidateTimer) {
			return;
		}
		invalidateTimer = setTimeout(() => {
			invalidateTimer = null;
			invalidateWorkbench(queryClient, pendingSessionId);
		}, INVALIDATION_DEBOUNCE_MS);
	};
	const url = sessionId
		? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/events/stream`
		: '/api/workbench/events/stream';
	const source = new EventSource(url);
	const handleEvent = (message: MessageEvent<string>) => {
		scheduleInvalidation(eventSessionId(parseEvent(message.data), sessionId));
	};

	source.addEventListener('workbench_event', handleEvent);
	source.addEventListener('message', handleEvent);
	source.onerror = () => {
		scheduleInvalidation(sessionId);
	};

	return () => {
		if (invalidateTimer) {
			clearTimeout(invalidateTimer);
			invalidateTimer = null;
		}
		source.removeEventListener('workbench_event', handleEvent);
		source.removeEventListener('message', handleEvent);
		source.close();
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

function invalidateWorkbench(queryClient: QueryClient, sessionId: string | null) {
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
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sessionEvents(sessionId, 0) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.graphCandidatesRoot(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.resumeSnapshotRoot(sessionId) });
	void queryClient.invalidateQueries({ queryKey: workbenchKeys.sourcePolicy(sessionId) });
}
