import { afterEach, describe, expect, it, vi } from 'vitest';

import { createWorkbenchEventStream } from './eventStream';

class FakeEventSource {
	static instances: FakeEventSource[] = [];

	readonly url: string;
	onerror: (() => void) | null = null;
	private listeners = new Map<string, ((message: MessageEvent<string>) => void)[]>();

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	addEventListener(type: string, listener: (message: MessageEvent<string>) => void) {
		const listeners = this.listeners.get(type) ?? [];
		listeners.push(listener);
		this.listeners.set(type, listeners);
	}

	removeEventListener(type: string, listener: (message: MessageEvent<string>) => void) {
		this.listeners.set(
			type,
			(this.listeners.get(type) ?? []).filter((candidate) => candidate !== listener)
		);
	}

	close() {}

	emit(type: string, data: string) {
		for (const listener of this.listeners.get(type) ?? []) {
			listener(new MessageEvent(type, { data }));
		}
	}
}

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
	FakeEventSource.instances = [];
});

describe('createWorkbenchEventStream', () => {
	it('coalesces replayed events into one query invalidation pass', async () => {
		vi.useFakeTimers();
		vi.stubGlobal('EventSource', FakeEventSource);
		const invalidateQueries = vi.fn();

		createWorkbenchEventStream({
			queryClient: { invalidateQueries } as never,
			sessionId: 'session-1'
		});
		const source = FakeEventSource.instances[0];
		expect(source).toBeDefined();
		if (!source) {
			throw new Error('expected EventSource instance');
		}
		expect(source.url).toBe('/api/workbench/sessions/session-1/events/stream');

		for (let index = 0; index < 50; index += 1) {
			source.emit(
				'workbench_event',
				JSON.stringify({
					globalSeq: index + 1,
					sessionId: 'session-1',
					eventName: 'candidate_seen'
				})
			);
		}

		expect(invalidateQueries).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(250);

		const invalidatedKeys = invalidateQueries.mock.calls.map(([argument]) => argument.queryKey);
		expect(invalidatedKeys.filter((key) => key.join(':') === 'workbench:sessions')).toHaveLength(1);
		expect(
			invalidatedKeys.filter((key) => key.join(':') === 'workbench:sessions:session-1')
		).toHaveLength(1);
	});
});
