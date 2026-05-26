import { describe, expect, it } from 'vitest';
import { hasStartableSourceRun } from './sessionFlow';

describe('session flow state', () => {
	it('allows requirement preparation before a sheet exists', () => {
		expect(hasStartableSourceRun([{ status: 'queued' }])).toBe(true);
	});

	it('does not show the central start button after all selected sources are terminal', () => {
		expect(hasStartableSourceRun([{ status: 'completed' }, { status: 'failed' }])).toBe(false);
	});

	it('allows retrying a blocked source after the operator fixes the channel', () => {
		expect(hasStartableSourceRun([{ status: 'completed' }, { status: 'blocked' }])).toBe(true);
	});
});
