export type RequirementPreparationSession = {
	requirement_review: {
		status: string;
		requirement_sheet?: unknown | null;
	};
};

export type RequirementPreparationEvent = {
	eventName: string;
	globalSeq: number;
	sourceKind?: string | null;
	sourceRunId?: string | null;
};

const STARTED_EVENTS = new Set(['runtime_run_started', 'runtime_requirements_started']);
const TERMINAL_EVENTS = new Set([
	'runtime_requirements_completed',
	'runtime_requirements_failed',
	'requirement_review_updated'
]);

export function latestWorkbenchEventSeq(events: RequirementPreparationEvent[]): number {
	return events.reduce((latest, event) => Math.max(latest, event.globalSeq), 0);
}

export function isRequirementPreparationRunning(
	session: RequirementPreparationSession,
	events: RequirementPreparationEvent[],
	optimisticStartedAfterSeq: number | null = null
): boolean {
	if (hasRequirementSheet(session)) {
		return false;
	}
	const status = String(session.requirement_review.status);
	if (status === 'pending' || status === 'running') {
		return true;
	}
	const startedAt = maxRequirementEventSeq(events, STARTED_EVENTS);
	const finishedAt = maxRequirementEventSeq(events, TERMINAL_EVENTS);
	if (startedAt > finishedAt) {
		return true;
	}
	return optimisticStartedAfterSeq !== null && finishedAt <= optimisticStartedAfterSeq;
}

export function isRequirementPreparationSettled(
	session: RequirementPreparationSession,
	events: RequirementPreparationEvent[],
	optimisticStartedAfterSeq: number | null
): boolean {
	if (hasRequirementSheet(session)) {
		return true;
	}
	if (optimisticStartedAfterSeq === null) {
		return false;
	}
	return maxRequirementEventSeq(events, TERMINAL_EVENTS) > optimisticStartedAfterSeq;
}

function hasRequirementSheet(session: RequirementPreparationSession): boolean {
	return Boolean(session.requirement_review.requirement_sheet);
}

function maxRequirementEventSeq(
	events: RequirementPreparationEvent[],
	eventNames: Set<string>
): number {
	return events.reduce((latest, event) => {
		if (!isSessionLevelRequirementEvent(event) || !eventNames.has(event.eventName)) {
			return latest;
		}
		return Math.max(latest, event.globalSeq);
	}, 0);
}

function isSessionLevelRequirementEvent(event: RequirementPreparationEvent): boolean {
	return event.sourceKind == null && event.sourceRunId == null;
}
