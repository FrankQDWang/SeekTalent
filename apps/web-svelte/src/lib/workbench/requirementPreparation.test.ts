import { describe, expect, it } from 'vitest';

import {
	isRequirementPreparationRunning,
	isRequirementPreparationSettled,
	latestWorkbenchEventSeq,
	type RequirementPreparationEvent,
	type RequirementPreparationSession
} from './requirementPreparation';

function session({
	status = 'draft',
	hasSheet = false
}: {
	status?: string;
	hasSheet?: boolean;
} = {}): RequirementPreparationSession {
	return {
		requirement_review: {
			status,
			requirement_sheet: hasSheet ? { job_title: 'Data Engineer' } : null
		}
	};
}

function event(
	globalSeq: number,
	eventName: string,
	overrides: Partial<RequirementPreparationEvent> = {}
): RequirementPreparationEvent {
	return {
		globalSeq,
		eventName,
		sourceKind: null,
		sourceRunId: null,
		...overrides
	};
}

describe('requirement preparation state', () => {
	it('stays running after prepare returns before persisted events refresh', () => {
		const events: RequirementPreparationEvent[] = [];

		expect(
			isRequirementPreparationRunning(session(), events, latestWorkbenchEventSeq(events))
		).toBe(true);
	});

	it('uses persisted requirement events when the page reloads during extraction', () => {
		const events = [event(4, 'runtime_requirements_started')];

		expect(isRequirementPreparationRunning(session(), events)).toBe(true);
	});

	it('does not treat source lane runtime events as requirement preparation', () => {
		const events = [
			event(10, 'runtime_requirements_started', {
				sourceKind: 'liepin',
				sourceRunId: 'source-run-1'
			})
		];

		expect(isRequirementPreparationRunning(session(), events)).toBe(false);
	});

	it('settles optimistic state only after a newer terminal event or sheet appears', () => {
		const baseSession = session();

		expect(
			isRequirementPreparationSettled(baseSession, [event(10, 'runtime_requirements_failed')], 10)
		).toBe(false);
		expect(
			isRequirementPreparationSettled(baseSession, [event(11, 'runtime_requirements_failed')], 10)
		).toBe(true);
		expect(isRequirementPreparationSettled(session({ hasSheet: true }), [], 10)).toBe(true);
	});

	it('stops running after completion or review update', () => {
		const events = [
			event(1, 'runtime_requirements_started'),
			event(2, 'requirement_review_updated')
		];

		expect(isRequirementPreparationRunning(session(), events)).toBe(false);
	});
});
