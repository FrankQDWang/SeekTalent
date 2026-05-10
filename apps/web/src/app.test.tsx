import '@testing-library/jest-dom/vitest';

import { QueryClient } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createWorkbenchApi } from './api';
import { createWorkbenchRouter } from './app';
import type {
  WorkbenchEvent,
  WorkbenchRequirementTriage,
  WorkbenchSession,
  WorkbenchSettingsResponse,
  WorkbenchSourceConnection,
  WorkbenchUser,
} from './types';

const user: WorkbenchUser = {
  userId: 'user-1',
  email: 'admin@example.com',
  displayName: 'Admin User',
  role: 'admin',
  workspaceId: 'default',
};

function triage(overrides: Partial<WorkbenchRequirementTriage> = {}): WorkbenchRequirementTriage {
  return {
    sessionId: overrides.sessionId ?? 'session-1',
    status: overrides.status ?? 'draft',
    mustHaves: overrides.mustHaves ?? ['Python APIs'],
    niceToHaves: overrides.niceToHaves ?? ['Retrieval systems'],
    synonyms: overrides.synonyms ?? ['platform engineer'],
    seniorityFilters: overrides.seniorityFilters ?? ['senior'],
    exclusions: overrides.exclusions ?? ['intern'],
    generatedQueryHints: overrides.generatedQueryHints ?? ['python backend'],
    createdAt: overrides.createdAt ?? '2026-05-09T00:00:00Z',
    updatedAt: overrides.updatedAt ?? '2026-05-09T00:00:00Z',
    approvedAt: overrides.approvedAt ?? null,
  };
}

function session(overrides: Partial<WorkbenchSession> = {}): WorkbenchSession {
  const sessionId = overrides.sessionId ?? 'session-1';
  return {
    sessionId,
    workspaceId: 'default',
    ownerUserId: 'user-1',
    jobTitle: overrides.jobTitle ?? 'Python Platform Engineer',
    jdText: overrides.jdText ?? 'Build Python APIs.',
    notes: overrides.notes ?? 'Prefer retrieval systems.',
    status: 'draft',
    requirementTriage: overrides.requirementTriage ?? triage({ sessionId }),
    sourceRuns: overrides.sourceRuns ?? [
      {
        sourceRunId: 'src-cts',
        sourceKind: 'cts',
        status: 'queued',
        authState: 'not_required',
        cardsScannedCount: 0,
        uniqueCandidatesCount: 0,
        warningCode: null,
        warningMessage: null,
      },
      {
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        status: 'blocked',
        authState: 'login_required',
        cardsScannedCount: 0,
        uniqueCandidatesCount: 0,
        warningCode: 'login_required',
        warningMessage: 'Liepin login is not connected yet.',
      },
    ],
    sourceCards: overrides.sourceCards ?? [
      {
        sourceRunId: 'src-cts',
        sourceKind: 'cts',
        label: 'CTS',
        status: 'queued',
        authState: 'not_required',
        cardsScannedCount: 0,
        uniqueCandidatesCount: 0,
        warningCode: null,
        warningMessage: null,
      },
      {
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        label: 'Liepin',
        status: 'blocked',
        authState: 'login_required',
        cardsScannedCount: 0,
        uniqueCandidatesCount: 0,
        warningCode: 'login_required',
        warningMessage: 'Liepin login is not connected yet.',
      },
    ],
  };
}

const settingsResponse: WorkbenchSettingsResponse = {
  workspaceId: 'default',
  sources: [
    { sourceKind: 'cts', label: 'CTS', enabled: true, authRequired: false },
    { sourceKind: 'liepin', label: 'Liepin', enabled: true, authRequired: true },
  ],
};

function event(overrides: Partial<WorkbenchEvent> = {}): WorkbenchEvent {
  return {
    globalSeq: overrides.globalSeq ?? 1,
    sessionSeq: overrides.sessionSeq ?? 1,
    sessionId: overrides.sessionId ?? 'session-1',
    sourceRunId: overrides.sourceRunId ?? 'src-cts',
    sourceKind: overrides.sourceKind ?? 'cts',
    eventName: overrides.eventName ?? 'source_run_started',
    payload: overrides.payload ?? { status: 'running' },
    createdAt: overrides.createdAt ?? '2026-05-09T00:00:00Z',
  };
}

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string | URL;
  readonly listeners = new Map<string, EventListenerOrEventListenerObject[]>();
  readyState = 0;
  onerror: ((this: EventSource, ev: Event) => unknown) | null = null;
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null;
  onopen: ((this: EventSource, ev: Event) => unknown) | null = null;
  close = vi.fn(() => {
    this.readyState = 2;
  });

  constructor(url: string | URL) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((item) => item !== listener),
    );
  }

  dispatchEvent(): boolean {
    return true;
  }

  emit(type: string, payload: WorkbenchEvent) {
    const message = new MessageEvent(type, { data: JSON.stringify(payload) });
    for (const listener of this.listeners.get(type) ?? []) {
      if (typeof listener === 'function') {
        listener(message);
      } else {
        listener.handleEvent(message);
      }
    }
  }
}

function mockEventSource() {
  MockEventSource.instances = [];
  vi.stubGlobal('EventSource', MockEventSource);
}

type RouteHandler = (url: string, init: RequestInit) => Response | Promise<Response>;

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  });
}

function emptyResponse(init: ResponseInit = {}) {
  return new Response(null, { status: init.status ?? 204, headers: init.headers });
}

function eventsResponse(events: WorkbenchEvent[] = []) {
  return jsonResponse({ events });
}

function candidateReviewItem(overrides: Record<string, unknown> = {}) {
  return {
    reviewItemId: 'review-1',
    sessionId: 'session-1',
    status: 'new',
    note: '',
    displayName: 'Lin Qian',
    title: 'Senior Backend Engineer',
    company: 'SearchCo',
    location: 'Shanghai',
    summary: 'Strong FastAPI and retrieval systems background.',
    aggregateScore: 91,
    fitBucket: 'fit',
    sourceBadges: ['CTS'],
    evidenceLevel: 'final',
    matchedMustHaves: ['FastAPI', 'retrieval systems'],
    matchedPreferences: ['agent tooling'],
    missingRisks: ['benchmark depth unclear'],
    strengths: ['Built SSE APIs'],
    weaknesses: ['Limited benchmark ownership'],
    evidence: [
      {
        evidenceId: 'evidence-1',
        sourceRunId: 'src-cts',
        sourceKind: 'cts',
        evidenceLevel: 'final',
        score: 91,
        fitBucket: 'fit',
        matchedMustHaves: ['FastAPI'],
        matchedPreferences: ['agent tooling'],
        missingRisks: ['benchmark depth unclear'],
        strengths: ['Built SSE APIs'],
        weaknesses: ['Limited benchmark ownership'],
        createdAt: '2026-05-09T00:04:00Z',
      },
    ],
    createdAt: '2026-05-09T00:04:00Z',
    updatedAt: '2026-05-09T00:04:00Z',
    ...overrides,
  };
}

function candidateQueueResponse(items = [candidateReviewItem()]) {
  return jsonResponse({ items });
}

function liepinConnection(overrides: Partial<WorkbenchSourceConnection> = {}): WorkbenchSourceConnection {
  return {
    connectionId: overrides.connectionId ?? 'conn-liepin-1',
    sourceKind: overrides.sourceKind ?? 'liepin',
    label: overrides.label ?? 'Liepin',
    status: overrides.status ?? 'login_required',
    warningCode: overrides.warningCode ?? 'login_required',
    warningMessage: overrides.warningMessage ?? 'Liepin login has not been connected yet.',
    createdAt: overrides.createdAt ?? '2026-05-09T00:00:00Z',
    updatedAt: overrides.updatedAt ?? '2026-05-09T00:00:00Z',
    connectedAt: overrides.connectedAt ?? null,
  };
}

function renderWorkbench(path: string, handler: RouteHandler) {
  Object.defineProperty(window, 'scrollTo', { value: vi.fn(), writable: true });
  const api = createWorkbenchApi();
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createWorkbenchRouter({ api, queryClient, history });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    try {
      return Promise.resolve(handler(url, init ?? {}));
    } catch (error) {
      if (/^\/api\/workbench\/sessions\/[^/]+\/candidates(?:\/[^/]+)?$/.test(url)) {
        return Promise.resolve(jsonResponse({ items: [] }));
      }
      throw error;
    }
  });
  vi.stubGlobal('fetch', fetchMock);

  const view = render(<RouterProvider router={router} />);
  return { ...view, api, fetchMock, router };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('workbench routes', () => {
  it('redirects unauthenticated protected routes to login', async () => {
    renderWorkbench('/sessions', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ detail: 'Not authenticated.' }, { status: 401 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
  });

  it('redirects unauthenticated session detail routes to login', async () => {
    const { fetchMock } = renderWorkbench('/sessions/session-private', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ detail: 'Not authenticated.' }, { status: 401 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/workbench/sessions/session-private',
      expect.anything(),
    );
  });

  it('setup and login forms call auth APIs', async () => {
    const setupRequests: Array<{ url: string; body: unknown }> = [];
    const setup = renderWorkbench('/setup', (url, init) => {
      setupRequests.push({ url, body: JSON.parse(String(init.body)) });
      return jsonResponse({ user, workspace: { id: 'default', name: 'Default Workspace' } }, { status: 201 });
    });

    await userEvent.type(await screen.findByLabelText('Email'), 'admin@example.com');
    await userEvent.type(screen.getByLabelText('Display name'), 'Admin User');
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse');
    await userEvent.click(screen.getByRole('button', { name: 'Create admin' }));

    await waitFor(() => expect(setupRequests).toHaveLength(1));
    expect(setupRequests[0]).toEqual({
      url: '/api/auth/bootstrap',
      body: { email: 'admin@example.com', password: 'correct horse', displayName: 'Admin User' },
    });

    setup.unmount();

    const loginRequests: Array<{ url: string; body?: unknown }> = [];
    renderWorkbench('/login', (url, init) => {
      loginRequests.push({
        url,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url === '/api/auth/login') {
        return emptyResponse({ headers: { 'X-CSRF-Token': 'login-token' } });
      }
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'me-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await userEvent.type(await screen.findByLabelText('Email'), 'admin@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() =>
      expect(loginRequests).toContainEqual({
        url: '/api/auth/login',
        body: { email: 'admin@example.com', password: 'correct horse' },
      }),
    );
  });

  it('stores refreshed csrf tokens from response headers and sends them on authenticated mutations', async () => {
    let createHeaders: Headers | undefined;
    const api = createWorkbenchApi();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/login') {
          return Promise.resolve(emptyResponse({ headers: { 'X-CSRF-Token': 'login-token' } }));
        }
        if (url === '/api/auth/me') {
          return Promise.resolve(jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'refreshed-token' } }));
        }
        if (url === '/api/workbench/sessions') {
          createHeaders = new Headers(init?.headers);
          return Promise.resolve(jsonResponse(session({ sessionId: 'session-new' }), { status: 201 }));
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );

    await api.login({ email: 'admin@example.com', password: 'correct horse' });
    await api.me();
    await api.createSession({
      jobTitle: 'Python Platform Engineer',
      jdText: 'Build Python APIs.',
      notes: '',
    });

    expect(createHeaders?.get('X-CSRF-Token')).toBe('refreshed-token');
  });

  it('captures csrf from bootstrap response headers and replaces it with later refreshed tokens', async () => {
    const mutationHeaders: string[] = [];
    const api = createWorkbenchApi();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/bootstrap') {
          return Promise.resolve(
            jsonResponse(
              { user, workspace: { id: 'default', name: 'Default Workspace' } },
              { status: 201, headers: { 'X-CSRF-Token': 'bootstrap-token' } },
            ),
          );
        }
        if (url === '/api/auth/me') {
          return Promise.resolve(jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'me-token' } }));
        }
        if (url === '/api/workbench/sessions') {
          mutationHeaders.push(new Headers(init?.headers).get('X-CSRF-Token') ?? '');
          return Promise.resolve(jsonResponse(session({ sessionId: `session-${mutationHeaders.length}` }), { status: 201 }));
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );

    await api.bootstrap({
      email: 'admin@example.com',
      password: 'correct horse',
      displayName: 'Admin User',
    });
    await api.createSession({ jobTitle: 'After Bootstrap', jdText: 'JD', notes: '' });
    await api.me();
    await api.createSession({ jobTitle: 'After Me', jdText: 'JD', notes: '' });

    expect(mutationHeaders).toEqual(['bootstrap-token', 'me-token']);
  });

  it('refreshes csrf and retries one stale mutating request across api instances', async () => {
    const apiA = createWorkbenchApi();
    const apiB = createWorkbenchApi();
    const createHeaders: string[] = [];
    let meRequests = 0;

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/login') {
          return Promise.resolve(emptyResponse({ headers: { 'X-CSRF-Token': 'token-a' } }));
        }
        if (url === '/api/auth/me') {
          meRequests += 1;
          return Promise.resolve(
            jsonResponse(
              { user },
              { headers: { 'X-CSRF-Token': meRequests === 1 ? 'token-b' : 'token-a-refreshed' } },
            ),
          );
        }
        if (url === '/api/workbench/sessions') {
          const csrf = new Headers(init?.headers).get('X-CSRF-Token') ?? '';
          createHeaders.push(csrf);
          if (createHeaders.length === 1) {
            return Promise.resolve(jsonResponse({ detail: 'Invalid CSRF token.' }, { status: 403 }));
          }
          return Promise.resolve(jsonResponse(session({ sessionId: 'session-new' }), { status: 201 }));
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );

    await apiA.login({ email: 'admin@example.com', password: 'correct horse' });
    await apiB.me();
    const created = await apiA.createSession({ jobTitle: 'Retried', jdText: 'JD', notes: '' });

    expect(created.sessionId).toBe('session-new');
    expect(meRequests).toBe(2);
    expect(createHeaders).toEqual(['token-a', 'token-a-refreshed']);
  });

  it('loads durable event pages beyond the first backend page', async () => {
    const api = createWorkbenchApi();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/workbench/events?after_seq=0&limit=200') {
        return Promise.resolve(eventsResponse(Array.from({ length: 200 }, (_, index) => event({ globalSeq: index + 1 }))));
      }
      if (url === '/api/workbench/events?after_seq=200&limit=200') {
        return Promise.resolve(eventsResponse([event({ globalSeq: 201, eventName: 'runtime_round_completed' })]));
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const response = await api.listEvents(0);

    expect(response.events).toHaveLength(201);
    expect(response.events[200].eventName).toBe('runtime_round_completed');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('renders and filters the session rail', async () => {
    renderWorkbench('/sessions', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({
          sessions: [
            session({ sessionId: 'session-python', jobTitle: 'Python Platform Engineer' }),
            session({ sessionId: 'session-data', jobTitle: 'Data Search Lead' }),
          ],
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const rail = await screen.findByTestId('session-rail');
    expect(await within(rail).findByText('Python Platform Engineer')).toBeInTheDocument();
    expect(within(rail).getByText('Data Search Lead')).toBeInTheDocument();

    await userEvent.type(within(rail).getByPlaceholderText('Search sessions'), 'data');

    expect(within(rail).queryByText('Python Platform Engineer')).not.toBeInTheDocument();
    expect(within(rail).getByText('Data Search Lead')).toBeInTheDocument();
  });

  it('renders a session rail error instead of an empty list when session loading fails', async () => {
    renderWorkbench('/sessions', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ detail: 'Session list unavailable.' }, { status: 500 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const rail = await screen.findByTestId('session-rail');
    expect(await within(rail).findByText('Could not load sessions')).toBeInTheDocument();
    expect(within(rail).queryByText('No sessions')).not.toBeInTheDocument();
  });

  it('marks the rail collapse button state for assistive technology', async () => {
    renderWorkbench('/sessions', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const button = await screen.findByRole('button', { name: 'Collapse session rail' });
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(button).toHaveAttribute('aria-controls', 'session-rail-content');

    await userEvent.click(button);

    expect(screen.getByRole('button', { name: 'Expand session rail' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens one app-level event stream in authenticated layout and closes it on unmount', async () => {
    mockEventSource();
    const view = renderWorkbench('/sessions', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Create session' })).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe('/api/workbench/events/stream');
    expect([...MockEventSource.instances[0].listeners.keys()]).toEqual(['workbench_event']);

    view.unmount();

    expect(MockEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('does not create a partial fresh event cache before the timeline loads', async () => {
    mockEventSource();
    const requests: string[] = [];
    const currentSession = session();
    const historicalEvent = event({ globalSeq: 1, eventName: 'runtime_requirements_completed' });

    renderWorkbench('/sessions', (url) => {
      requests.push(url);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([historicalEvent]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Create session' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: /Python Platform Engineer/ })).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emit(
      'workbench_event',
      event({ globalSeq: 2, eventName: 'runtime_round_completed', payload: { roundNo: 1 } }),
    );

    expect(requests.some((url) => url.startsWith('/api/workbench/events?after_seq=0'))).toBe(false);

    await userEvent.click(await screen.findByRole('link', { name: /Python Platform Engineer/ }));

    await waitFor(() =>
      expect(requests.some((url) => url.startsWith('/api/workbench/events?after_seq=0'))).toBe(true),
    );
    expect(await screen.findByText('runtime_requirements_completed')).toBeInTheDocument();
  });

  it('logs out through the topbar and closes the event stream', async () => {
    mockEventSource();
    const requests: string[] = [];
    renderWorkbench('/sessions', (url, init) => {
      requests.push(`${init.method ?? 'GET'} ${url}`);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/auth/logout' && init.method === 'POST') {
        return emptyResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Create session' })).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: 'Log out' }));

    await waitFor(() => expect(requests).toContain('POST /api/auth/logout'));
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument();
    expect(MockEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('refreshes only targeted workbench queries when a source-run event arrives', async () => {
    mockEventSource();
    const requests: string[] = [];
    const currentSession = session({ requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }) });

    renderWorkbench('/sessions/session-1', (url) => {
      requests.push(url);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByTestId('active-session-title')).toHaveTextContent('Python Platform Engineer');
    expect(await screen.findByText('No timeline events yet')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    const before = requests.length;
    const beforeAuthMe = requests.filter((url) => url === '/api/auth/me').length;

    MockEventSource.instances[0].emit(
      'workbench_event',
      event({ eventName: 'runtime_round_completed', globalSeq: 2, payload: { roundNo: 1 } }),
    );

    await waitFor(() => {
      const afterRequests = requests.slice(before);
      expect(afterRequests).toContain('/api/workbench/sessions');
      expect(afterRequests).toContain('/api/workbench/sessions/session-1');
      expect(afterRequests.some((url) => url.startsWith('/api/workbench/events?after_seq=0'))).toBe(false);
    });

    expect(await screen.findByText('runtime_round_completed')).toBeInTheDocument();
    const afterRequests = requests.slice(before);
    expect(afterRequests).not.toContain('/api/auth/me');
    expect(afterRequests).not.toContain('/api/workbench/settings');
    expect(requests.filter((url) => url === '/api/auth/me')).toHaveLength(beforeAuthMe);
  });

  it('keeps dirty triage text while source-run events refetch the session', async () => {
    mockEventSource();
    const requests: string[] = [];
    const currentSession = session();

    renderWorkbench('/sessions/session-1', (url) => {
      requests.push(url);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const mustHaves = await screen.findByLabelText('Must-haves');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Unsaved visible criteria');
    const before = requests.length;

    MockEventSource.instances[0].emit(
      'workbench_event',
      event({ eventName: 'source_run_started', globalSeq: 2, payload: { status: 'running' } }),
    );

    await waitFor(() => expect(requests.slice(before)).toContain('/api/workbench/sessions/session-1'));
    expect(screen.getByLabelText('Must-haves')).toHaveValue('Unsaved visible criteria');
  });

  it('resets dirty triage draft when switching to another session', async () => {
    const sessionOne = session({
      sessionId: 'session-1',
      jobTitle: 'Python Platform Engineer',
      requirementTriage: triage({ sessionId: 'session-1', mustHaves: ['Python APIs'] }),
    });
    const sessionTwo = session({
      sessionId: 'session-2',
      jobTitle: 'Data Search Lead',
      requirementTriage: triage({ sessionId: 'session-2', mustHaves: ['Search ranking'] }),
    });
    const triageRequests: Array<{ url: string; body: unknown }> = [];

    renderWorkbench('/sessions/session-1', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [sessionOne, sessionTwo] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(sessionOne);
      }
      if (url === '/api/workbench/sessions/session-2') {
        return jsonResponse(sessionTwo);
      }
      if (url === '/api/workbench/sessions/session-2/triage' && init.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        triageRequests.push({ url, body });
        return jsonResponse({ ...sessionTwo.requirementTriage, ...body });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const mustHaves = await screen.findByLabelText('Must-haves');
    expect(mustHaves).toHaveValue('Python APIs');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Unsaved session one draft');

    await userEvent.click(screen.getByRole('link', { name: /Data Search Lead/ }));

    expect(await screen.findByTestId('active-session-title')).toHaveTextContent('Data Search Lead');
    expect(await screen.findByLabelText('Must-haves')).toHaveValue('Search ranking');

    await userEvent.click(screen.getByRole('button', { name: 'Save triage' }));

    await waitFor(() => expect(triageRequests).toHaveLength(1));
    expect(triageRequests[0]).toEqual({
      url: '/api/workbench/sessions/session-2/triage',
      body: {
        mustHaves: ['Search ranking'],
        niceToHaves: ['Retrieval systems'],
        synonyms: ['platform engineer'],
        seniorityFilters: ['senior'],
        exclusions: ['intern'],
        generatedQueryHints: ['python backend'],
      },
    });
  });

  it('creates a session with csrf and updates the rail plus selected route', async () => {
    const created = session({ sessionId: 'session-new', jobTitle: 'AI Recruiter Engineer' });
    const postRequests: Array<{ headers: Headers; body: unknown }> = [];

    renderWorkbench('/sessions', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions' && init.method === 'POST') {
        postRequests.push({
          headers: new Headers(init.headers),
          body: JSON.parse(String(init.body)),
        });
        return jsonResponse(created, { status: 201 });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/sessions/session-new') {
        return jsonResponse(created);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await userEvent.type(await screen.findByLabelText('Job title'), 'AI Recruiter Engineer');
    await userEvent.type(screen.getByLabelText('JD'), 'Coordinate multi-source sourcing.');
    await userEvent.type(screen.getByLabelText('Notes'), 'Keep Liepin blocked until login.');
    await userEvent.click(screen.getByRole('button', { name: 'Create session' }));

    await waitFor(() => expect(postRequests).toHaveLength(1));
    expect(postRequests[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(postRequests[0].body).toEqual({
      jobTitle: 'AI Recruiter Engineer',
      jdText: 'Coordinate multi-source sourcing.',
      notes: 'Keep Liepin blocked until login.',
    });

    expect(await screen.findAllByText('AI Recruiter Engineer')).toHaveLength(2);
    expect(await screen.findByTestId('active-session-title')).toHaveTextContent('AI Recruiter Engineer');
  });

  it('renders source cards from materialized API state', async () => {
    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [session()] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(session());
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([event({ eventName: 'session_created', sourceRunId: null, sourceKind: null })]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const cts = await screen.findByTestId('source-card-cts');
    const liepin = await screen.findByTestId('source-card-liepin');

    expect(cts).toHaveTextContent('CTS');
    expect(cts).toHaveTextContent('待命');
    expect(cts).toHaveTextContent('本地库');
    expect(liepin).toHaveTextContent('Liepin');
    expect(liepin).toHaveTextContent('需登录');
    expect(liepin).toHaveTextContent('连接猎聘后可加入本次检索。');
  });

  it('renders real candidate review queue with source badges and evidence level', async () => {
    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [session()] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(session());
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return candidateQueueResponse();
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const card = await screen.findByTestId('candidate-card-review-1');

    expect(card).toHaveTextContent('Lin Qian');
    expect(card).toHaveTextContent('Senior Backend Engineer');
    expect(card).toHaveTextContent('CTS');
    expect(card).toHaveTextContent('final');
    expect(card).toHaveTextContent('FastAPI / retrieval systems');
    expect(card).toHaveTextContent('benchmark depth unclear');
  });

  it('updates candidate review action and note through the API', async () => {
    const updates: Array<{ url: string; body: unknown; headers: Headers }> = [];
    renderWorkbench('/sessions/session-1', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [session()] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(session());
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return candidateQueueResponse();
      }
      if (url === '/api/workbench/sessions/session-1/candidates/review-1' && init.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        updates.push({ url, body, headers: new Headers(init.headers) });
        return jsonResponse(candidateReviewItem({ status: body.status, note: body.note }));
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const card = await screen.findByTestId('candidate-card-review-1');
    await userEvent.type(within(card).getByLabelText('Note'), 'Call this person first.');
    await userEvent.click(within(card).getByRole('button', { name: 'Mark promising' }));

    await waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0].url).toBe('/api/workbench/sessions/session-1/candidates/review-1');
    expect(updates[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(updates[0].body).toEqual({ note: 'Call this person first.', status: 'promising' });
  });

  it('keeps a dirty candidate note while candidate events refetch the queue', async () => {
    mockEventSource();
    const requests: string[] = [];
    let queueItems = [candidateReviewItem()];

    renderWorkbench('/sessions/session-1', (url) => {
      requests.push(url);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [session()] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(session());
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return candidateQueueResponse(queueItems);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const card = await screen.findByTestId('candidate-card-review-1');
    const note = within(card).getByLabelText('Note');
    await userEvent.type(note, 'Unsaved local note');
    queueItems = [candidateReviewItem({ note: 'Remote saved note' })];
    const before = requests.length;

    MockEventSource.instances[0].emit(
      'workbench_event',
      event({
        eventName: 'candidate_review_item_updated',
        globalSeq: 3,
        payload: { reviewItemId: 'review-1', reviewStatus: 'promising' },
      }),
    );

    await waitFor(() => expect(requests.slice(before)).toContain('/api/workbench/sessions/session-1/candidates'));
    expect(note).toHaveValue('Unsaved local note');
  });

  it('renders requirement triage as escaped editable text', async () => {
    const unsafeSession = session({
      sessionId: 'session-unsafe-triage',
      requirementTriage: triage({
        sessionId: 'session-unsafe-triage',
        mustHaves: ['Own <script>alert("must")</script> safely'],
        niceToHaves: ['Nice <script>alert("nice")</script>'],
        synonyms: ['Syn <script>alert("syn")</script>'],
        seniorityFilters: ['Lead <script>alert("seniority")</script>'],
        exclusions: ['Exclude <script>alert("exclude")</script>'],
        generatedQueryHints: ['Hint <script>alert("hint")</script>'],
      }),
    });

    const { container } = renderWorkbench('/sessions/session-unsafe-triage', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [unsafeSession] });
      }
      if (url === '/api/workbench/sessions/session-unsafe-triage') {
        return jsonResponse(unsafeSession);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByLabelText('Must-haves')).toHaveValue('Own <script>alert("must")</script> safely');
    expect(screen.getByLabelText('Nice-to-haves')).toHaveValue('Nice <script>alert("nice")</script>');
    expect(screen.getByLabelText('Synonyms')).toHaveValue('Syn <script>alert("syn")</script>');
    expect(screen.getByLabelText('Seniority filters')).toHaveValue('Lead <script>alert("seniority")</script>');
    expect(screen.getByLabelText('Exclusions')).toHaveValue('Exclude <script>alert("exclude")</script>');
    expect(screen.getByLabelText('Query hints')).toHaveValue('Hint <script>alert("hint")</script>');
    expect(container.querySelector('script')).toBeNull();
  });

  it('saves and approves requirement triage with csrf', async () => {
    let currentSession = session();
    const triageRequests: Array<{ url: string; method: string; headers: Headers; body?: unknown }> = [];

    renderWorkbench('/sessions/session-1', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url === '/api/workbench/sessions/session-1/triage' && init.method === 'PUT') {
        triageRequests.push({
          url,
          method: init.method,
          headers: new Headers(init.headers),
          body: JSON.parse(String(init.body)),
        });
        currentSession = {
          ...currentSession,
          requirementTriage: {
            ...currentSession.requirementTriage,
            ...(triageRequests[0].body as object),
            updatedAt: '2026-05-09T00:01:00Z',
          },
        };
        return jsonResponse(currentSession.requirementTriage);
      }
      if (url === '/api/workbench/sessions/session-1/triage/approve' && init.method === 'POST') {
        triageRequests.push({ url, method: init.method, headers: new Headers(init.headers) });
        currentSession = {
          ...currentSession,
          requirementTriage: {
            ...currentSession.requirementTriage,
            status: 'approved',
            approvedAt: '2026-05-09T00:02:00Z',
          },
        };
        return jsonResponse(currentSession.requirementTriage);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const mustHaves = await screen.findByLabelText('Must-haves');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Python APIs\nFastAPI');
    await userEvent.clear(screen.getByLabelText('Query hints'));
    await userEvent.type(screen.getByLabelText('Query hints'), 'site:github.com python platform');
    await userEvent.click(screen.getByRole('button', { name: 'Save triage' }));

    await waitFor(() => expect(triageRequests).toHaveLength(1));
    expect(triageRequests[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(triageRequests[0].body).toEqual({
      mustHaves: ['Python APIs', 'FastAPI'],
      niceToHaves: ['Retrieval systems'],
      synonyms: ['platform engineer'],
      seniorityFilters: ['senior'],
      exclusions: ['intern'],
      generatedQueryHints: ['site:github.com python platform'],
    });

    await userEvent.click(screen.getByRole('button', { name: 'Approve triage' }));

    await waitFor(() => expect(triageRequests).toHaveLength(2));
    expect(triageRequests[1]).toMatchObject({
      url: '/api/workbench/sessions/session-1/triage/approve',
      method: 'POST',
    });
    expect(triageRequests[1].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(await screen.findByText('approved')).toBeInTheDocument();
  });

  it('saves visible dirty triage before direct approval', async () => {
    let currentSession = session();
    const triageRequests: Array<{ url: string; method: string; headers: Headers; body?: unknown }> = [];

    renderWorkbench('/sessions/session-1', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url === '/api/workbench/sessions/session-1/triage' && init.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        triageRequests.push({ url, method: init.method, headers: new Headers(init.headers), body });
        currentSession = {
          ...currentSession,
          requirementTriage: {
            ...currentSession.requirementTriage,
            ...body,
            updatedAt: '2026-05-09T00:01:00Z',
          },
        };
        return jsonResponse(currentSession.requirementTriage);
      }
      if (url === '/api/workbench/sessions/session-1/triage/approve' && init.method === 'POST') {
        triageRequests.push({ url, method: init.method, headers: new Headers(init.headers) });
        currentSession = {
          ...currentSession,
          requirementTriage: {
            ...currentSession.requirementTriage,
            status: 'approved',
            approvedAt: '2026-05-09T00:02:00Z',
          },
        };
        return jsonResponse(currentSession.requirementTriage);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const mustHaves = await screen.findByLabelText('Must-haves');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Visible unsaved must-have');
    await userEvent.click(screen.getByRole('button', { name: 'Approve triage' }));

    await waitFor(() => expect(triageRequests).toHaveLength(2));
    expect(triageRequests[0]).toMatchObject({
      url: '/api/workbench/sessions/session-1/triage',
      method: 'PUT',
    });
    expect(triageRequests[0].body).toMatchObject({
      mustHaves: ['Visible unsaved must-have'],
    });
    expect(triageRequests[1]).toMatchObject({
      url: '/api/workbench/sessions/session-1/triage/approve',
      method: 'POST',
    });
  });

  it('keeps CTS start disabled until triage approval then starts CTS by source kind with csrf', async () => {
    let currentSession = session();
    const startRequests: Array<{ headers: Headers; body: unknown }> = [];

    renderWorkbench('/sessions/session-1', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url === '/api/workbench/sessions/session-1/triage/approve' && init.method === 'POST') {
        currentSession = {
          ...currentSession,
          requirementTriage: {
            ...currentSession.requirementTriage,
            status: 'approved',
            approvedAt: '2026-05-09T00:02:00Z',
          },
        };
        return jsonResponse(currentSession.requirementTriage);
      }
      if (url === '/api/workbench/sessions/session-1/source-runs' && init.method === 'POST') {
        startRequests.push({
          headers: new Headers(init.headers),
          body: JSON.parse(String(init.body)),
        });
        currentSession = {
          ...currentSession,
          sourceRuns: currentSession.sourceRuns.map((run) =>
            run.sourceKind === 'cts' ? { ...run, status: 'queued' } : run,
          ),
          sourceCards: currentSession.sourceCards.map((card) =>
            card.sourceKind === 'cts' ? { ...card, status: 'queued' } : card,
          ),
        };
        return jsonResponse({
          sessionId: 'session-1',
          sourceRunId: 'src-cts',
          sourceKind: 'cts',
          status: 'queued',
          job: {
            jobId: 'job-1',
            sourceRunId: 'src-cts',
            status: 'queued',
            attemptCount: 0,
            errorMessage: null,
            createdAt: '2026-05-09T00:03:00Z',
            updatedAt: '2026-05-09T00:03:00Z',
          },
        });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const startCts = await screen.findByRole('button', { name: '启动 CTS' });
    expect(startCts).toBeDisabled();
    expect(screen.getByRole('button', { name: '连接猎聘' })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: 'Approve triage' }));

    await waitFor(() => expect(screen.getByRole('button', { name: '启动 CTS' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: '启动 CTS' }));

    await waitFor(() => expect(startRequests).toHaveLength(1));
    expect(startRequests[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(startRequests[0].body).toEqual({ sourceKind: 'cts' });
  });

  it('renders a session detail error instead of not found when the detail API fails', async () => {
    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [session()] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse({ detail: 'Detail unavailable.' }, { status: 500 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Could not load session')).toBeInTheDocument();
    expect(screen.queryByText('Session not found')).not.toBeInTheDocument();
  });

  it('renders settings source entries from the API', async () => {
    renderWorkbench('/settings/sources', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/settings') {
        return jsonResponse(settingsResponse);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Sources' })).toBeInTheDocument();
    expect(await screen.findByTestId('settings-source-cts')).toHaveTextContent('CTS');
    expect(screen.getByTestId('settings-source-cts')).toHaveTextContent('Enabled');
    expect(screen.getByTestId('settings-source-liepin')).toHaveTextContent('Liepin');
    expect(screen.getByTestId('settings-source-liepin')).toHaveTextContent('Login required');
  });

  it('renders a settings error instead of an empty source list when settings loading fails', async () => {
    renderWorkbench('/settings/sources', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/settings') {
        return jsonResponse({ detail: 'Settings unavailable.' }, { status: 500 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Could not load settings')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-source-cts')).not.toBeInTheDocument();
  });

  it('creates a Liepin connection from settings without starting a search run', async () => {
    const requests: string[] = [];
    const connection = liepinConnection();

    renderWorkbench('/settings/sources/liepin', (url, init) => {
      requests.push(`${init.method ?? 'GET'} ${url}`);
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/source-connections') {
        return jsonResponse({ connections: [] });
      }
      if (url === '/api/workbench/source-connections/liepin') {
        expect(init.method).toBe('POST');
        expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-token');
        return jsonResponse(connection, { status: 201 });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Liepin connection' })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Create Liepin connection' }));

    expect(await screen.findByTestId('source-connection-conn-liepin-1')).toHaveTextContent('login_required');
    expect(requests).toContain('POST /api/workbench/source-connections/liepin');
    expect(requests.some((request) => request.includes('/source-runs'))).toBe(false);
  });

  it('starts the isolated Liepin login handoff without exposing browser internals', async () => {
    const connection = liepinConnection();
    const handoff = {
      connectionId: connection.connectionId,
      sourceKind: 'liepin',
      status: 'login_in_progress',
      handoffMode: 'server_managed_browser',
      handoffState: 'relay_pending_worker',
      safeFrameUrl: null,
      warningCode: 'relay_pending_worker',
      warningMessage: 'Managed browser interaction bridge is pending.',
    };

    renderWorkbench('/connections/liepin/conn-liepin-1/login', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/source-connections/conn-liepin-1') {
        return jsonResponse(connection);
      }
      if (url === '/api/workbench/source-connections/conn-liepin-1/login') {
        expect(init.method).toBe('POST');
        expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-token');
        return jsonResponse(handoff);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByRole('heading', { name: 'Liepin managed-browser login' })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Start isolated login' }));

    expect(await screen.findByText('relay_pending_worker')).toBeInTheDocument();
    expect(screen.getByText('Safe frame pending')).toBeInTheDocument();
    const pageText = document.body.textContent?.toLowerCase() ?? '';
    for (const secretWord of ['cookie', 'storage state', 'cdp url', 'websocket url', 'worker url']) {
      expect(pageText).not.toContain(secretWord);
    }
  });

  it('renders the safe Liepin login frame when the relay is available', async () => {
    const connection = liepinConnection();
    const handoff = {
      connectionId: connection.connectionId,
      sourceKind: 'liepin',
      status: 'login_in_progress',
      handoffMode: 'server_managed_browser',
      handoffState: 'safe_frame_available',
      safeFrameUrl: '/api/workbench/source-connections/conn-liepin-1/login/frame',
      warningCode: null,
      warningMessage: null,
    };

    renderWorkbench('/connections/liepin/conn-liepin-1/login', (url, init) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [] });
      }
      if (url === '/api/workbench/source-connections/conn-liepin-1') {
        return jsonResponse(connection);
      }
      if (url === '/api/workbench/source-connections/conn-liepin-1/login') {
        expect(init.method).toBe('POST');
        return jsonResponse(handoff);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Start isolated login' }));

    expect(await screen.findByText('safe_frame_available')).toBeInTheDocument();
    const frame = screen.getByTitle('Liepin safe login frame');
    expect(frame).toHaveAttribute('src', handoff.safeFrameUrl);
  });

  it('renders jd notes and source warning text as escaped text', async () => {
    const unsafeSession = session({
      jdText: 'Use <script>alert("jd")</script> safely.',
      notes: 'Notes <script>alert("notes")</script>',
      sourceCards: [
        {
          sourceRunId: 'src-cts',
          sourceKind: 'cts',
          label: 'CTS',
          status: 'queued',
          authState: 'not_required',
          cardsScannedCount: 0,
          uniqueCandidatesCount: 0,
          warningCode: null,
          warningMessage: '<script>alert("source")</script>',
        },
      ],
      sourceRuns: [],
    });

    const { container } = renderWorkbench('/sessions/session-unsafe', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [unsafeSession] });
      }
      if (url === '/api/workbench/sessions/session-unsafe') {
        return jsonResponse(unsafeSession);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Use <script>alert("jd")</script> safely.')).toBeInTheDocument();
    expect(screen.getByText('Notes <script>alert("notes")</script>')).toBeInTheDocument();
    expect(screen.getByText('<script>alert("source")</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
  });
});
