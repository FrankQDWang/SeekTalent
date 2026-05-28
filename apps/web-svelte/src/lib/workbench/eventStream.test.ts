import { afterEach, describe, expect, it, vi } from 'vitest';

import { workbenchKeys } from '$lib/query/keys';
import { createWorkbenchEventStream } from './eventStream';

class FakeEventSource {
	static instances: FakeEventSource[] = [];

	readonly url: string;
	closed = false;
	onopen: (() => void) | null = null;
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

	close() {
		this.closed = true;
	}

	emit(type: string, data: string) {
		for (const listener of this.listeners.get(type) ?? []) {
			listener(new MessageEvent(type, { data }));
		}
	}
}

afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	FakeEventSource.instances = [];
});

describe('createWorkbenchEventStream', () => {
	it('coalesces replayed candidate events without refreshing unrelated session surfaces', async () => {
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
		expect(
			invalidatedKeys.filter((key) => key.join(':') === 'workbench:sessions:session-1:events:0')
		).toHaveLength(1);
		expect(
			invalidatedKeys.filter((key) => key.join(':') === 'workbench:sessions:session-1:candidates')
		).toHaveLength(1);
		expect(
			invalidatedKeys.filter((key) => key.join(':') === 'workbench:sessions:session-1:final-top10')
		).toHaveLength(1);
		expect(invalidatedKeys.some((key) => key.join(':') === 'workbench:sessions')).toBe(false);
		expect(
			invalidatedKeys.filter(
				(key) => key.join(':') === 'workbench:sessions:session-1:runtime-graph'
			)
		).toHaveLength(0);
	});

	it('refreshes only the note stream for workbench note events', async () => {
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

		source.emit(
			'workbench_event',
			JSON.stringify({
				globalSeq: 1,
				sessionId: 'session-1',
				eventName: 'workbench_note_created'
			})
		);
		await vi.advanceTimersByTimeAsync(250);

		const invalidatedKeys = invalidateQueries.mock.calls.map(([argument]) => argument.queryKey);
		expect(invalidatedKeys).toEqual([workbenchKeys.sessionEvents('session-1', 0)]);
	});

	it('refreshes runtime graph but not candidate lists for controller progress events', async () => {
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

		source.emit(
			'workbench_event',
			JSON.stringify({
				globalSeq: 1,
				sessionId: 'session-1',
				eventName: 'runtime_controller_started'
			})
		);
		await vi.advanceTimersByTimeAsync(250);

		const invalidatedKeys = invalidateQueries.mock.calls.map(([argument]) => argument.queryKey);
		expect(invalidatedKeys).toContainEqual(workbenchKeys.sessionEvents('session-1', 0));
		expect(invalidatedKeys).toContainEqual(workbenchKeys.runtimeGraph('session-1'));
		expect(invalidatedKeys).not.toContainEqual(workbenchKeys.candidates('session-1'));
		expect(invalidatedKeys).not.toContainEqual(workbenchKeys.finalTop10('session-1'));
	});

	it('refreshes session summary when requirement review changes', async () => {
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

		source.emit(
			'workbench_event',
			JSON.stringify({
				globalSeq: 1,
				sessionId: 'session-1',
				eventName: 'requirement_review_updated'
			})
		);
		await vi.advanceTimersByTimeAsync(250);

		const invalidatedKeys = invalidateQueries.mock.calls.map(([argument]) => argument.queryKey);
		expect(invalidatedKeys).toContainEqual(workbenchKeys.sessions);
		expect(invalidatedKeys).toContainEqual(workbenchKeys.session('session-1'));
		expect(invalidatedKeys).toContainEqual(workbenchKeys.sessionEvents('session-1', 0));
		expect(invalidatedKeys).toContainEqual(workbenchKeys.runtimeGraph('session-1'));
	});

	it('pauses the event stream while the tab is hidden and refreshes on foreground', async () => {
		if (typeof document === 'undefined') {
			expect(typeof document).toBe('undefined');
			return;
		}
		vi.useFakeTimers();
		vi.stubGlobal('EventSource', FakeEventSource);
		const invalidateQueries = vi.fn();
		const hiddenSpy = vi.spyOn(document, 'hidden', 'get');
		hiddenSpy.mockReturnValue(true);

		createWorkbenchEventStream({
			queryClient: { invalidateQueries } as never,
			sessionId: 'session-1'
		});

		expect(FakeEventSource.instances).toHaveLength(0);

		hiddenSpy.mockReturnValue(false);
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(250);

		expect(FakeEventSource.instances).toHaveLength(1);
		expect(invalidateQueries).toHaveBeenCalledWith({
			queryKey: workbenchKeys.session('session-1')
		});

		hiddenSpy.mockReturnValue(true);
		document.dispatchEvent(new Event('visibilitychange'));

		expect(FakeEventSource.instances[0]?.closed).toBe(true);
	});

	it('does not turn EventSource reconnect errors into query invalidation loops', async () => {
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

		source.onerror?.();
		source.onerror?.();
		source.onerror?.();
		await vi.advanceTimersByTimeAsync(250);

		expect(invalidateQueries).not.toHaveBeenCalled();
	});
});
