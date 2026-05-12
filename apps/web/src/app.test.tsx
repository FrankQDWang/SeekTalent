import '@testing-library/jest-dom/vitest';

import { QueryClient } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createWorkbenchApi } from './api';
import { createWorkbenchRouter } from './app';
import { disposeStrategyGraphLayoutRunner, setStrategyGraphLayoutRunnerForTests } from './strategyGraphLayout';
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
        detailOpenUsedCount: 0,
        detailOpenBlockedCount: 0,
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
        detailOpenUsedCount: 0,
        detailOpenBlockedCount: 0,
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
        detailOpenUsedCount: 0,
        detailOpenBlockedCount: 0,
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
        detailOpenUsedCount: 0,
        detailOpenBlockedCount: 0,
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

beforeEach(() => {
  setStrategyGraphLayoutRunnerForTests(async (graph) => ({
    ...graph,
    children: graph.children?.map((child, index) => ({
      ...child,
      x: 40 + index * 180,
      y: 80 + (index % 3) * 96,
    })),
  }));
});

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

function detailOpenRequest(overrides: Record<string, unknown> = {}) {
  return {
    requestId: 'dor-1',
    sessionId: 'session-1',
    reviewItemId: 'review-liepin',
    status: 'pending',
    detailOpenMode: 'human_confirm',
    decisionNote: 'Agent recommends opening detail after card triage; must-have: FastAPI; card signal score: 68.',
    candidate: {
      reviewItemId: 'review-liepin',
      displayName: 'Lin Qian',
      title: 'Senior Backend Engineer',
      company: 'Redacted Cloud',
      location: 'Shanghai',
      summary: 'FastAPI / retrieval systems',
      aggregateScore: 68,
      evidenceLevel: 'card',
      sourceBadges: ['Liepin'],
      matchedMustHaves: ['FastAPI'],
      matchedPreferences: ['retrieval'],
      missingRisks: ['Detail page not opened yet.'],
    },
    blockedReason: null,
    ledger: null,
    providerAction: null,
    createdAt: '2026-05-09T00:05:00Z',
    updatedAt: '2026-05-09T00:05:00Z',
    ...overrides,
  };
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
      if (url.startsWith('/api/workbench/detail-open-requests')) {
        return Promise.resolve(jsonResponse({ requests: [] }));
      }
      if (/^\/api\/workbench\/sessions\/[^/]+\/graph-candidates(?:\?.*)?$/.test(url)) {
        const parsed = new URL(url, 'http://localhost');
        return Promise.resolve(jsonResponse({
          nodeId: parsed.searchParams.get('node_id') ?? '',
          items: [],
          nextCursor: null,
          totalEstimate: 0,
          truncated: false,
          generatedAt: '2026-05-09T00:00:00Z',
          recoveryState: 'ready',
          recoveryReason: null,
        }));
      }
      if (/^\/api\/workbench\/sessions\/[^/]+\/source-runs\/liepin\/policy$/.test(url)) {
        return Promise.resolve(jsonResponse({
          sessionId: 'session-1',
          sourceKind: 'liepin',
          detailOpenMode: 'human_confirm',
          updatedAt: '2026-05-09T00:00:00Z',
        }));
      }
      throw error;
    }
  });
  vi.stubGlobal('fetch', fetchMock);

  const view = render(<RouterProvider router={router} />);
  return { ...view, api, fetchMock, router };
}

function renderWorkbenchWithRound(roundPayload: Record<string, unknown> = {}) {
  const currentSession = session({
    requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    sourceCards: [{ ...session().sourceCards[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
    sourceRuns: [{ ...session().sourceRuns[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
  });
  return renderWorkbench('/sessions/session-1', (url) => {
    if (url === '/api/auth/me') return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
    if (url === '/api/workbench/sessions') return jsonResponse({ sessions: [currentSession] });
    if (url === '/api/workbench/sessions/session-1') return jsonResponse(currentSession);
    if (url === '/api/workbench/sessions/session-1/candidates') return candidateQueueResponse([]);
    if (url.startsWith('/api/workbench/detail-open-requests')) return jsonResponse({ requests: [] });
    if (url.startsWith('/api/workbench/events?after_seq=0')) {
      return eventsResponse([
        event({
          globalSeq: 1,
          eventName: 'runtime_round_completed',
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          payload: {
            roundNo: 1,
            payload: {
              executed_queries: [{ query_terms: ['Flink CDC'] }],
              raw_candidate_count: 14,
              unique_new_count: 9,
              newly_scored_count: 9,
              fit_count: 1,
              not_fit_count: 8,
              ...roundPayload,
            },
          },
        }),
        event({
          globalSeq: 2,
          eventName: 'workbench_note_created',
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          payload: { text: '需要放宽 Kafka 关键词。', eventSeq: 2 },
        }),
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });
}

async function openGraphNode(name: RegExp | string) {
  const candidates = await screen.findAllByRole('button', { name });
  const node = candidates.find((candidate) => candidate.classList.contains('strategy-flow-node')) ?? candidates[0];
  fireEvent.click(node);
  expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
  return node;
}

function openFinalShortlistNode() {
  return openGraphNode(/最终短名单/);
}

function openDetailApprovalNode() {
  return openGraphNode(/详情审批/);
}

function renderWorkbenchWithMultiSourceGraph() {
  const currentSession = session({
    requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    sourceCards: [
      { ...session().sourceCards[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 },
      { ...session().sourceCards[1], status: 'completed', connectionStatus: 'connected', cardsScannedCount: 12, uniqueCandidatesCount: 4 },
    ],
  });
  renderWorkbench('/sessions/session-1', (url) => {
    if (url === '/api/auth/me') return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
    if (url === '/api/workbench/sessions') return jsonResponse({ sessions: [currentSession] });
    if (url === '/api/workbench/sessions/session-1') return jsonResponse(currentSession);
    if (url === '/api/workbench/sessions/session-1/candidates') return candidateQueueResponse([]);
    if (url.startsWith('/api/workbench/detail-open-requests')) return jsonResponse({ requests: [] });
    if (url.startsWith('/api/workbench/events?after_seq=0')) {
      return eventsResponse([
        event({ globalSeq: 1, eventName: 'source_run_started', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
        event({ globalSeq: 2, eventName: 'liepin_card_search_completed', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
        event({ globalSeq: 3, eventName: 'runtime_round_completed', sourceKind: 'cts', sourceRunId: 'src-cts' }),
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });
}

afterEach(() => {
  cleanup();
  disposeStrategyGraphLayoutRunner();
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
    expect(await screen.findByText('岗位需求 / Python Platform Engineer')).toBeInTheDocument();
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
    expect(await screen.findByText('需求拆解')).toBeInTheDocument();
    expect(screen.queryByText('No timeline events yet')).not.toBeInTheDocument();
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

    expect(await screen.findByText('第 1 轮关键词')).toBeInTheDocument();
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

    await userEvent.click(await screen.findByRole('button', { name: '修改' }));
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

  it('shows runtime extraction without treating it as saved triage', async () => {
    const currentSession = session({
      requirementTriage: triage({
        status: 'approved',
        approvedAt: '2026-05-09T00:02:00Z',
        mustHaves: [],
        niceToHaves: [],
        synonyms: [],
        seniorityFilters: [],
        exclusions: [],
        generatedQueryHints: [],
      }),
    });
    const runtimeRequirements = event({
      globalSeq: 3,
      eventName: 'runtime_requirements_completed',
      payload: {
        type: 'requirements_completed',
        message: '岗位需求解析完成：Python Platform Engineer',
        roundNo: null,
        payload: {
          role_title: 'Python Platform Engineer',
          must_have_capabilities: ['Flink CDC', 'streaming data pipeline construction'],
          preferred_capabilities: ['production data platform experience'],
          search_terms: ['Streaming Data', 'Flink CDC'],
        },
      },
    });

    renderWorkbench('/sessions/session-1', (url) => {
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
        return eventsResponse([runtimeRequirements]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Agent 提取')).toBeInTheDocument();
    expect(screen.getByText('Flink CDC / streaming data pipeline construction')).toBeInTheDocument();
    expect(screen.getAllByText('production data platform experience').length).toBeGreaterThan(0);
    expect(screen.getByText('Streaming Data / Flink CDC')).toBeInTheDocument();
    expect(screen.queryByLabelText('Must-haves')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Nice-to-haves')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Query hints')).not.toBeInTheDocument();

    expect(screen.queryByRole('button', { name: '填入表单' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '修改' }));
    expect(screen.getByLabelText('Must-haves')).toHaveValue('Flink CDC\nstreaming data pipeline construction');
    expect(screen.getByLabelText('Nice-to-haves')).toHaveValue('production data platform experience');
    expect(screen.getByLabelText('Query hints')).toHaveValue('Streaming Data\nFlink CDC');
  });

  it('starts with agent extraction instead of an empty triage form', async () => {
    const emptyTriage = triage({
      status: 'draft',
      mustHaves: [],
      niceToHaves: [],
      synonyms: [],
      seniorityFilters: [],
      exclusions: [],
      generatedQueryHints: [],
    });
    const extractedTriage = triage({
      status: 'draft',
      mustHaves: ['Python APIs', 'ranking systems'],
      niceToHaves: ['retrieval experience'],
      synonyms: [],
      seniorityFilters: [],
      exclusions: ['intern only'],
      generatedQueryHints: ['Python backend', 'ranking systems'],
    });
    let currentSession = session({ requirementTriage: emptyTriage });
    const prepareRequests: Headers[] = [];
    const startRequests: Headers[] = [];

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
      if (url === '/api/workbench/sessions/session-1/triage/prepare' && init.method === 'POST') {
        prepareRequests.push(new Headers(init.headers));
        currentSession = { ...currentSession, requirementTriage: extractedTriage };
        return jsonResponse(extractedTriage);
      }
      if (url === '/api/workbench/sessions/session-1/start' && init.method === 'POST') {
        startRequests.push(new Headers(init.headers));
        return jsonResponse({ sessionId: 'session-1', sourceRuns: [], blockedSources: [] }, { status: 202 });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Agent 将先拆解 JD，生成可确认的检索标准。')).toBeInTheDocument();
    expect(screen.queryByLabelText('Must-haves')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '启动 Agent' }));

    await waitFor(() => expect(prepareRequests).toHaveLength(1));
    expect(prepareRequests[0]?.get('X-CSRF-Token')).toBe('csrf-token');
    expect(startRequests).toHaveLength(0);
    expect(await screen.findByText('Agent 提取')).toBeInTheDocument();
    expect(screen.getByText('Python APIs / ranking systems')).toBeInTheDocument();
    expect(screen.getAllByText('retrieval experience').length).toBeGreaterThan(0);
    expect(screen.getByText('intern only')).toBeInTheDocument();
    expect(screen.getByText('Python backend / ranking systems')).toBeInTheDocument();
    expect(screen.queryByLabelText('Must-haves')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认并开始检索' })).toBeInTheDocument();
  });

  it('does not show reference-only criteria or playback controls in a real session', async () => {
    const currentSession = session({
      requirementTriage: triage({
        status: 'approved',
        approvedAt: '2026-05-09T00:02:00Z',
        mustHaves: [],
        niceToHaves: [],
        synonyms: [],
        seniorityFilters: [],
        exclusions: [],
        generatedQueryHints: [],
      }),
      sourceRuns: [
        {
          sourceRunId: 'src-cts',
          sourceKind: 'cts',
          status: 'completed',
          authState: 'not_required',
          cardsScannedCount: 9,
          uniqueCandidatesCount: 9,
          detailOpenUsedCount: 0,
          detailOpenBlockedCount: 0,
          warningCode: null,
          warningMessage: null,
        },
      ],
      sourceCards: [
        {
          sourceRunId: 'src-cts',
          sourceKind: 'cts',
          label: 'CTS',
          status: 'completed',
          authState: 'not_required',
          cardsScannedCount: 9,
          uniqueCandidatesCount: 9,
          detailOpenUsedCount: 0,
          detailOpenBlockedCount: 0,
          warningCode: null,
          warningMessage: null,
        },
      ],
    });
    const runtimeRequirements = event({
      globalSeq: 3,
      eventName: 'runtime_requirements_completed',
      payload: {
        type: 'requirements_completed',
        message: '岗位需求解析完成：Python Platform Engineer',
        payload: {
          role_title: 'Python Platform Engineer',
          must_have_capabilities: ['Flink CDC', 'streaming data pipeline construction'],
          preferred_capabilities: [],
        },
      },
    });

    renderWorkbench('/sessions/session-1', (url) => {
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
        return eventsResponse([runtimeRequirements]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('Flink CDC')).toBeInTheDocument();
    expect(screen.getAllByText(/扫描/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('9')).toHaveLength(2);
    expect(screen.queryByText('快消 / 互联网大厂双背景')).not.toBeInTheDocument();
    expect(screen.queryByText('MBA 或海外背景优先')).not.toBeInTheDocument();
    expect(screen.queryByText('有 IPO 前经验 加分')).not.toBeInTheDocument();
    expect(screen.queryByText('12,911')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Start playback')).not.toBeInTheDocument();
    expect(screen.queryByText(/34\.0s/)).not.toBeInTheDocument();
    expect(screen.queryByText('Developer log')).not.toBeInTheDocument();
    expect(screen.queryByText('候选人短名单')).not.toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['运行笔记', '节点详情']);
    expect(screen.queryByRole('tab', { name: '候选人队列' })).not.toBeInTheDocument();
  });

  it('renders workbench-created running notes instead of raw runtime event names', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    });
    const storyEvents = [
      event({
        globalSeq: 1,
        eventName: 'runtime_requirements_completed',
        payload: {
          type: 'requirements_completed',
          message: '岗位需求解析完成：Python Platform Engineer',
          payload: {
            must_have_capabilities: ['Flink CDC', 'streaming data pipeline construction'],
            preferred_capabilities: ['production data platform experience'],
          },
        },
      }),
      event({
        globalSeq: 2,
        eventName: 'runtime_round_completed',
        payload: {
          type: 'round_completed',
          message: '第 1 轮完成。',
          roundNo: 1,
          payload: {
            executed_queries: [
              {
                query_role: 'exploit',
                query_terms: ['Streaming Data', 'Flink CDC'],
              },
            ],
            raw_candidate_count: 14,
            unique_new_count: 9,
            newly_scored_count: 9,
            fit_count: 1,
            not_fit_count: 8,
            reflection_rationale: 'Flink CDC must stay as the core technical signal.',
          },
        },
      }),
      event({
        globalSeq: 3,
        eventName: 'runtime_run_completed',
        payload: {
          type: 'run_completed',
          message: 'Run completed after 1 retrieval round.',
          payload: { rounds_executed: 1 },
        },
      }),
      event({
        globalSeq: 4,
        eventName: 'workbench_note_created',
        sourceKind: 'cts',
        sourceRunId: 'src-cts',
        payload: { text: '第 1 轮检索完成，正在等待下一步。', eventSeq: 4, noteKind: 'waiting', statusHint: 'waiting' },
      }),
    ];

    renderWorkbench('/sessions/session-1', (url) => {
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
        return eventsResponse(storyEvents);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('岗位需求 / Python Platform Engineer')).toBeInTheDocument();
    expect(screen.getByText('需求拆解')).toBeInTheDocument();
    expect(await screen.findByText('第 1 轮关键词')).toBeInTheDocument();
    expect(screen.getAllByText('Streaming Data + Flink CDC')[0]).toBeInTheDocument();
    expect(screen.getByText('搜到 14 人 · 新增 9 人')).toBeInTheDocument();
    expect(screen.getAllByText('评分：fit 1 / not_fit 8')[0]).toBeInTheDocument();
    const notesPanel = screen.getByRole('tabpanel', { name: '运行笔记' });
    expect(within(notesPanel).getByRole('list', { name: '运行笔记流' })).toHaveAttribute('aria-live', 'polite');
    expect(within(notesPanel).getAllByText('第 1 轮检索完成，正在等待下一步。').length).toBeGreaterThan(0);
    expect(within(notesPanel).queryByText(/第 1 轮围绕 Streaming Data/)).not.toBeInTheDocument();
    expect(within(notesPanel).queryByText(/00:00/)).not.toBeInTheDocument();
    expect(screen.getByTestId('latest-streaming-note')).toHaveClass('is-latest');
    expect(screen.getByTestId('latest-streaming-note')).toHaveClass('is-waiting');
    expect(within(notesPanel).queryByText('检索')).not.toBeInTheDocument();
    expect(within(notesPanel).queryByText('反思')).not.toBeInTheDocument();
    expect(within(notesPanel).queryByRole('button', { name: /第 1 轮反思/ })).not.toBeInTheDocument();
    expect(screen.queryByText('runtime_round_completed')).not.toBeInTheDocument();
  });

  it('keeps completed runtime timestamps out of running notes', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    });
    const storyEvents = [
      event({
        globalSeq: 1,
        eventName: 'runtime_run_started',
        createdAt: '2026-05-09T00:00:00Z',
        payload: { type: 'run_started', payload: { stage: 'runtime' } },
      }),
      event({
        globalSeq: 2,
        eventName: 'runtime_round_completed',
        createdAt: '2026-05-09T00:08:00Z',
        payload: {
          type: 'round_completed',
          roundNo: 1,
          payload: { raw_candidate_count: 3, unique_new_count: 2, newly_scored_count: 2, fit_count: 1, not_fit_count: 1 },
        },
      }),
      event({
        globalSeq: 3,
        eventName: 'runtime_run_completed',
        createdAt: '2026-05-09T00:14:25Z',
        payload: { type: 'run_completed', message: 'Run completed.', payload: { rounds_executed: 1 } },
      }),
    ];

    renderWorkbench('/sessions/session-1', (url) => {
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
        return eventsResponse(storyEvents);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('第 1 轮关键词')).toBeInTheDocument();
    const notesPanel = screen.getByRole('tabpanel', { name: '运行笔记' });
    expect(within(notesPanel).queryByText(/耗时 14分25秒 · 检索轮次 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/34\.0s/)).not.toBeInTheDocument();
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

    await userEvent.click(await screen.findByRole('button', { name: '修改' }));
    const mustHaves = await screen.findByLabelText('Must-haves');
    expect(mustHaves).toHaveValue('Python APIs');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Unsaved session one draft');

    await userEvent.click(screen.getByRole('link', { name: /Data Search Lead/ }));

    expect(await screen.findByTestId('active-session-title')).toHaveTextContent('Data Search Lead');
    await userEvent.click(await screen.findByRole('button', { name: '修改' }));
    expect(await screen.findByLabelText('Must-haves')).toHaveValue('Search ranking');

    await userEvent.click(screen.getByRole('button', { name: '保存标准' }));

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
    expect(screen.getByLabelText('CTS')).toBeChecked();
    expect(screen.getByLabelText('Liepin')).not.toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Create session' }));

    await waitFor(() => expect(postRequests).toHaveLength(1));
    expect(postRequests[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(postRequests[0].body).toEqual({
      jobTitle: 'AI Recruiter Engineer',
      jdText: 'Coordinate multi-source sourcing.',
      notes: 'Keep Liepin blocked until login.',
      sourceKinds: ['cts'],
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

  it('renders a selectable strategy graph reflection node', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceRuns: [
        {
          ...session().sourceRuns[0],
          status: 'completed',
          cardsScannedCount: 12,
          uniqueCandidatesCount: 4,
        },
      ],
      sourceCards: [
        {
          ...session().sourceCards[0],
          status: 'completed',
          cardsScannedCount: 12,
          uniqueCandidatesCount: 4,
        },
      ],
    });

    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return candidateQueueResponse([]);
      }
      if (url === '/api/workbench/detail-open-requests?session_id=session-1') {
        return jsonResponse({ requests: [] });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([
          event({
            globalSeq: 1,
            eventName: 'runtime_round_completed',
            sourceKind: 'cts',
            sourceRunId: 'src-cts',
            payload: {
              type: 'round_completed',
              roundNo: 1,
              payload: {
                executed_queries: [{ query_terms: ['Kafka'] }],
                raw_candidate_count: 12,
                unique_new_count: 4,
                newly_scored_count: 4,
                fit_count: 1,
                not_fit_count: 3,
                reflection_summary: '需要放宽 Kafka 关键词。',
              },
            },
          }),
          event({
            globalSeq: 2,
            eventName: 'workbench_note_created',
            sourceKind: 'cts',
            sourceRunId: 'src-cts',
            payload: { text: '需要放宽 Kafka 关键词。', eventSeq: 2 },
          }),
        ]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await screen.findByTestId('strategy-flow');
    const reflectionNode = await screen.findByRole('button', { name: /第 1 轮反思/ });

    expect(screen.queryByText(/节点 \d+ \/ \d+/)).not.toBeInTheDocument();
    expect(document.querySelector('.canvas-legend')).not.toBeInTheDocument();

    fireEvent.click(reflectionNode);

    expect(reflectionNode).toHaveAttribute('aria-pressed', 'true');
  });

  it('opens node detail tab with reflection rationale when a graph node is selected', async () => {
    const { fetchMock } = renderWorkbenchWithRound({
      reflection_summary: '需要放宽 Kafka 关键词。',
      reflection_rationale: '强候选人常把 Kafka 写在项目描述里。',
      next_direction: '加入实时数仓同义词。',
    });

    fireEvent.click(await screen.findByRole('button', { name: /第 1 轮反思/ }));

    expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('需要放宽 Kafka 关键词。').length).toBeGreaterThan(0);
    expect(screen.getByText('强候选人常把 Kafka 写在项目描述里。')).toBeInTheDocument();
    expect(screen.getByText('加入实时数仓同义词。')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('graph-candidates'))).toBe(false);

    await userEvent.click(screen.getByRole('tab', { name: '运行笔记' }));
    expect(screen.getByRole('tab', { name: '运行笔记' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: '候选人队列' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /第 1 轮反思/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('running note entry does not select a graph node', async () => {
    renderWorkbenchWithRound({
      reflection_summary: '需要放宽 Kafka 关键词。',
      reflection_rationale: '强候选人常把 Kafka 写在项目描述里。',
      next_direction: '加入实时数仓同义词。',
    });

    await userEvent.click(await screen.findByRole('tab', { name: '运行笔记' }));
    const notesPanel = screen.getByRole('tabpanel', { name: '运行笔记' });
    const note = (await within(notesPanel).findAllByText('需要放宽 Kafka 关键词。'))[0];
    await userEvent.click(note);

    expect(screen.getByRole('tab', { name: '运行笔记' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /第 1 轮反思/ })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByText('强候选人常把 Kafka 写在项目描述里。')).not.toBeInTheDocument();
  });

  it('loads graph node candidates and lazily expands safe resume snapshots', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [{ ...session().sourceCards[0], status: 'completed', cardsScannedCount: 14, uniqueCandidatesCount: 9 }],
      sourceRuns: [{ ...session().sourceRuns[0], status: 'completed', cardsScannedCount: 14, uniqueCandidatesCount: 9 }],
    });
    const graphCandidateId = 'graph-candidate-token-1';
    const secondGraphCandidateId = 'graph-candidate-token-2';

    const { fetchMock } = renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      if (url === '/api/workbench/sessions') return jsonResponse({ sessions: [currentSession] });
      if (url === '/api/workbench/sessions/session-1') return jsonResponse(currentSession);
      if (url === '/api/workbench/sessions/session-1/candidates') return candidateQueueResponse([]);
      if (url.startsWith('/api/workbench/detail-open-requests')) return jsonResponse({ requests: [] });
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([
          event({
            globalSeq: 1,
            eventName: 'runtime_round_completed',
            sourceKind: 'cts',
            sourceRunId: 'src-cts',
            payload: {
              roundNo: 1,
              payload: {
                executed_queries: [{ query_terms: ['Flink CDC', '实时数仓'] }],
                raw_candidate_count: 14,
                unique_new_count: 9,
                newly_scored_count: 9,
                fit_count: 2,
                not_fit_count: 7,
                reflection_summary: '继续看实时平台经验。',
              },
            },
          }),
        ]);
      }
      if (url.startsWith('/api/workbench/sessions/session-1/graph-candidates?')) {
        const parsed = new URL(url, 'http://localhost');
        expect(parsed.searchParams.get('node_id')).toBe('cts-round-1-result');
        if (parsed.searchParams.get('cursor') === 'cursor-2') {
          return jsonResponse({
            nodeId: 'cts-round-1-result',
            nodeScope: { sessionId: 'session-1', source: 'cts', roundId: '1', nodeKind: 'recall' },
            items: [
              {
                graphCandidateId: secondGraphCandidateId,
                sourceKind: 'cts',
                sourceRunId: 'src-cts',
                nodeKind: 'recall',
                roundNo: 1,
                laneType: 'generic_explore',
                queryRole: 'primary',
                relationshipKind: 'new',
                displayName: '候选人乙',
                title: '搜索工程师',
                company: 'SearchCo',
                location: '杭州',
                sourceBadges: ['CTS'],
                score: 88,
                fitBucket: 'fit',
                summary: '负责召回和语义排序。',
                matchedMustHaves: ['检索排序'],
                strengths: ['召回经验'],
                missingRisks: [],
                reviewItemId: null,
                evidenceLevel: null,
                detailOpenRequestId: null,
                canExpandResume: false,
                canMarkPromising: false,
                canReject: false,
                canSaveNote: false,
                canRequestDetail: false,
                canOpenProvider: false,
              },
            ],
            nextCursor: null,
            totalSourceResults: 2,
            totalGraphCandidates: 2,
            totalEstimate: 2,
            coverage: {
              sourceResultIdsSeen: ['sr-1', 'sr-2'],
              missingSafeIdentityCount: 0,
              missingSnapshotCount: 0,
              forbiddenSnapshotCount: 0,
              droppedRows: 0,
            },
            truncated: false,
            generatedAt: '2026-05-09T00:00:01Z',
            recoveryState: 'ready',
            recoveryReason: null,
          });
        }
        expect(parsed.searchParams.get('cursor')).toBeNull();
        return jsonResponse({
          nodeId: 'cts-round-1-result',
          nodeScope: { sessionId: 'session-1', source: 'cts', roundId: '1', nodeKind: 'recall' },
          items: [
            {
              graphCandidateId,
              sourceKind: 'cts',
              sourceRunId: 'src-cts',
              nodeKind: 'recall',
              roundNo: 1,
              laneType: 'generic_explore',
              queryRole: 'primary',
              relationshipKind: 'new',
              displayName: '候选人甲',
              title: '数据平台工程师',
              company: 'SearchCo',
              location: '上海',
              sourceBadges: ['CTS'],
              score: 91,
              fitBucket: 'fit',
              summary: '负责实时推荐排序与 Flink CDC 数据链路。',
              matchedMustHaves: ['Flink CDC'],
              strengths: ['实时平台经验'],
              missingRisks: [],
              reviewItemId: null,
              evidenceLevel: null,
              detailOpenRequestId: null,
              canExpandResume: true,
              canMarkPromising: false,
              canReject: false,
              canSaveNote: false,
              canRequestDetail: false,
              canOpenProvider: false,
            },
          ],
          nextCursor: 'cursor-2',
          totalSourceResults: 2,
          totalGraphCandidates: 2,
          totalEstimate: 2,
          coverage: {
            sourceResultIdsSeen: ['sr-1', 'sr-2'],
            missingSafeIdentityCount: 0,
            missingSnapshotCount: 0,
            forbiddenSnapshotCount: 0,
            droppedRows: 0,
          },
          truncated: true,
          generatedAt: '2026-05-09T00:00:00Z',
          recoveryState: 'ready',
          recoveryReason: null,
        });
      }
      if (url === `/api/workbench/sessions/session-1/graph-candidates/${graphCandidateId}/resume-snapshot`) {
        return jsonResponse({
          graphCandidateId,
          status: 'ready',
          reason: null,
          profile: {
            displayName: '候选人甲',
            headline: '数据平台工程师',
            company: 'SearchCo',
            location: '上海',
            summary: '长期做实时数据平台。',
          },
          workExperience: [
            {
              company: 'SearchCo',
              title: '数据平台工程师',
              duration: '2021-2026',
              summary: '项目：实时推荐排序和 CDC 数据同步。',
            },
          ],
          education: [{ school: 'ZJU', degree: '硕士', major: '计算机' }],
          projects: [{ name: '实时推荐排序', summary: '支撑千万级特征更新。' }],
          skills: ['Flink', 'Kafka', 'FastAPI'],
          sourceEvidence: [{ label: 'summary', text: '安全摘要证据' }],
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await screen.findByTestId('strategy-flow');
    fireEvent.click(await screen.findByRole('button', { name: /搜到 14 人 · 新增 9 人/ }));

    expect(await screen.findByText('召回简历')).toBeInTheDocument();
    expect(await screen.findByText(/已加载 [12] \/ 总计 2/)).toBeInTheDocument();
    expect(screen.getByText('本节点共 2 份简历')).toBeInTheDocument();
    expect(await screen.findByText('候选人甲')).toBeInTheDocument();
    expect(screen.getByText('负责实时推荐排序与 Flink CDC 数据链路。')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('resume-snapshot'))).toBe(false);

    await userEvent.click(screen.getByRole('button', { name: '加载更多候选人' }));
    expect(await screen.findByText('候选人乙')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '展开完整简历' }));

    expect(await screen.findByText('项目：实时推荐排序和 CDC 数据同步。')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '收起完整简历' }));
    expect(screen.queryByText('项目：实时推荐排序和 CDC 数据同步。')).not.toBeInTheDocument();
  });

  it('candidate evidence action opens related strategy graph node', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [
        { ...session().sourceCards[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 3 },
        {
          ...session().sourceCards[1],
          status: 'completed',
          authState: 'not_required',
          connectionStatus: 'connected',
          cardsScannedCount: 18,
          uniqueCandidatesCount: 1,
        },
      ],
      sourceRuns: [
        { ...session().sourceRuns[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 3 },
        {
          ...session().sourceRuns[1],
          status: 'completed',
          authState: 'not_required',
          cardsScannedCount: 18,
          uniqueCandidatesCount: 1,
        },
      ],
    });
    const liepinCandidate = candidateReviewItem({
      reviewItemId: 'review-liepin-1',
      sourceBadges: ['Liepin'],
      evidenceLevel: 'card',
      evidence: [
        {
          evidenceId: 'liepin-evidence-1',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'card',
          score: 88,
          fitBucket: 'fit',
          matchedMustHaves: ['FastAPI'],
          matchedPreferences: ['agent tooling'],
          missingRisks: ['detail not opened'],
          strengths: ['Built search ranking'],
          weaknesses: ['Needs Kafka confirmation'],
          createdAt: '2026-05-09T00:04:00Z',
        },
      ],
    });

    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      if (url === '/api/workbench/sessions') return jsonResponse({ sessions: [currentSession] });
      if (url === '/api/workbench/sessions/session-1') return jsonResponse(currentSession);
      if (url === '/api/workbench/sessions/session-1/candidates') return candidateQueueResponse([liepinCandidate]);
      if (url.startsWith('/api/workbench/detail-open-requests')) {
        return jsonResponse({
          requests: [
            detailOpenRequest({
              requestId: 'detail-request-liepin-1',
              reviewItemId: 'review-liepin-1',
              candidate: {
                reviewItemId: 'review-liepin-1',
                displayName: '候选人 A',
                title: 'Search Engineer',
                company: 'SearchCo',
                location: 'Shanghai',
                summary: 'Built search ranking',
                aggregateScore: 88,
                evidenceLevel: 'card',
                sourceBadges: ['Liepin'],
                matchedMustHaves: ['FastAPI'],
                matchedPreferences: ['agent tooling'],
                missingRisks: ['detail not opened'],
              },
            }),
          ],
        });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([
          event({ globalSeq: 1, eventName: 'source_run_started', sourceKind: 'cts', sourceRunId: 'src-cts' }),
          event({ globalSeq: 2, eventName: 'source_run_started', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
          event({
            globalSeq: 3,
            eventName: 'liepin_card_search_completed',
            sourceKind: 'liepin',
            sourceRunId: 'src-liepin',
            payload: { cardsScannedCount: 18, uniqueCandidatesCount: 1 },
          }),
        ]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await openFinalShortlistNode();
    await userEvent.click(await screen.findByRole('button', { name: '查看策略节点' }));

    expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /候选人初筛/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /详情审批 · 1 个/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows all selected sources in one graph and one business running note stream', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [
        {
          ...session().sourceCards[0],
          status: 'completed',
          cardsScannedCount: 9,
          uniqueCandidatesCount: 9,
        },
        {
          ...session().sourceCards[1],
          status: 'completed',
          authState: 'not_required',
          connectionId: 'conn-liepin-1',
          connectionStatus: 'connected',
          connectionWarningCode: null,
          connectionWarningMessage: null,
          cardsScannedCount: 30,
          uniqueCandidatesCount: 5,
        },
      ],
    });
    const timeline = [
      event({
        globalSeq: 1,
        eventName: 'runtime_round_completed',
        sourceKind: 'cts',
        sourceRunId: 'src-cts',
        payload: {
          roundNo: 1,
          payload: {
            executed_queries: [{ query_terms: ['Flink CDC'] }],
            raw_candidate_count: 14,
            unique_new_count: 9,
            newly_scored_count: 9,
            fit_count: 1,
            not_fit_count: 8,
          },
        },
      }),
      event({
        globalSeq: 2,
        eventName: 'liepin_card_search_completed',
        sourceKind: 'liepin',
        sourceRunId: 'src-liepin',
        payload: { cardsScannedCount: 30, uniqueCandidatesCount: 5 },
      }),
      event({
        globalSeq: 3,
        eventName: 'candidate_review_item_upserted',
        sourceKind: 'liepin',
        sourceRunId: 'src-liepin',
        payload: { reviewItemId: 'review-liepin', autoDetailScore: 88 },
      }),
      event({
        globalSeq: 4,
        eventName: 'workbench_note_created',
        sourceKind: null,
        sourceRunId: null,
        payload: { text: '多源检索已进入同一个运行笔记流。', eventSeq: 4 },
      }),
    ];

    renderWorkbench('/sessions/session-1', (url) => {
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
        return eventsResponse(timeline);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('第 1 轮关键词')).toBeInTheDocument();
    expect(screen.getByText('猎聘简介抓取 · 30 张')).toBeInTheDocument();
    expect(screen.getByText('候选人初筛 · 1 人')).toBeInTheDocument();
    const notesPanel = screen.getByRole('tabpanel', { name: '运行笔记' });
    expect(within(notesPanel).getAllByText('多源检索已进入同一个运行笔记流。').length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Source')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('View')).not.toBeInTheDocument();
    expect(document.querySelector('.canvas-legend')).not.toBeInTheDocument();
  });

  it('loads safe candidate and detail approval data once at the workbench shell level', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [
        { ...session().sourceCards[0], status: 'completed' },
        { ...session().sourceCards[1], status: 'completed', connectionStatus: 'connected' },
      ],
    });

    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [currentSession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(currentSession);
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return candidateQueueResponse([
          candidateReviewItem({
            reviewItemId: 'review-liepin-1',
            displayName: '候选人 A',
            sourceBadges: ['Liepin'],
            aggregateScore: 91,
            evidence: [
              {
                evidenceId: 'evidence-liepin-1',
                sourceRunId: 'src-liepin',
                sourceKind: 'liepin',
                evidenceLevel: 'card',
                score: 91,
                fitBucket: 'fit',
                matchedMustHaves: [],
                matchedPreferences: [],
                missingRisks: [],
                strengths: [],
                weaknesses: [],
                createdAt: '2026-05-09T00:00:03Z',
              },
            ],
          }),
        ]);
      }
      if (url.startsWith('/api/workbench/detail-open-requests')) {
        return jsonResponse({
          requests: [
            detailOpenRequest({
              requestId: 'detail-req-1',
              reviewItemId: 'review-liepin-1',
              status: 'pending',
            }),
          ],
        });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([
          event({ globalSeq: 1, eventName: 'source_run_started', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
          event({ globalSeq: 2, eventName: 'liepin_card_search_completed', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
        ]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('候选人初筛 · 1 人')).toBeInTheDocument();
    expect(screen.getByText('AI 简介判断最高 91 分')).toBeInTheDocument();
    expect(screen.getByText('详情审批 · 1 个')).toBeInTheDocument();
    expect(screen.queryByText('候选人 A')).not.toBeInTheDocument();

    await openFinalShortlistNode();
    expect(await screen.findByText('候选人 A')).toBeInTheDocument();

    await openDetailApprovalNode();
    expect(screen.getByText('批准后占用 1 次详情额度')).toBeInTheDocument();
  });

  it('starts the selected session sources from the central strategy button', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceRuns: [
        {
          ...session().sourceRuns[0],
          status: 'blocked',
        },
        {
          ...session().sourceRuns[1],
          status: 'blocked',
          authState: 'not_required',
        },
      ],
      sourceCards: [
        {
          ...session().sourceCards[0],
          status: 'blocked',
        },
        {
          ...session().sourceCards[1],
          status: 'blocked',
          authState: 'not_required',
          connectionId: 'conn-liepin-1',
          connectionStatus: 'connected',
          connectionWarningCode: null,
          connectionWarningMessage: null,
        },
      ],
    });
    const startRequests: Array<{ body: unknown; headers: Headers }> = [];

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
      if (url === '/api/workbench/sessions/session-1/start' && init.method === 'POST') {
        startRequests.push({ body: init.body ? JSON.parse(String(init.body)) : null, headers: new Headers(init.headers) });
        return jsonResponse({
          sessionId: 'session-1',
          sourceRuns: [
            {
              sessionId: 'session-1',
              sourceRunId: 'src-cts',
              sourceKind: 'cts',
              status: 'queued',
              job: {
                jobId: 'job-cts',
                sourceRunId: 'src-cts',
                status: 'queued',
                attemptCount: 0,
                errorMessage: null,
                createdAt: '2026-05-09T00:03:00Z',
                updatedAt: '2026-05-09T00:03:00Z',
              },
            },
            {
              sessionId: 'session-1',
              sourceRunId: 'src-liepin',
              sourceKind: 'liepin',
              status: 'queued',
              job: {
                jobId: 'job-liepin',
                sourceRunId: 'src-liepin',
                status: 'queued',
                attemptCount: 0,
                errorMessage: null,
                createdAt: '2026-05-09T00:03:00Z',
                updatedAt: '2026-05-09T00:03:00Z',
              },
            },
          ],
          blockedSources: [],
        }, { status: 202 });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(screen.queryByRole('button', { name: '启动全部' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动 CTS' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动猎聘' })).not.toBeInTheDocument();

    expect(await screen.findByText('需求拆解')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '启动检索' }));

    await waitFor(() => expect(startRequests).toHaveLength(1));
    expect(startRequests[0]?.body).toBeNull();
    expect(startRequests[0]?.headers.get('X-CSRF-Token')).toBe('csrf-token');
  });

  it('keeps source search unavailable when every source is terminal or disconnected', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [
        {
          ...session().sourceCards[0],
          status: 'completed',
        },
        {
          ...session().sourceCards[1],
          status: 'blocked',
          connectionStatus: 'login_required',
        },
      ],
    });

    renderWorkbench('/sessions/session-1', (url) => {
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

    expect(await screen.findByText('需求拆解')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动检索' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动 Agent' })).not.toBeInTheDocument();
  });

  it('shows an empty completed state for CTS-only sessions with no candidates', async () => {
    const ctsOnlySession = session({
      sourceRuns: [
        {
          ...session().sourceRuns[0],
          status: 'completed',
        },
      ],
      sourceCards: [
        {
          ...session().sourceCards[0],
          status: 'completed',
        },
      ],
    });

    renderWorkbench('/sessions/session-1', (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse({ user }, { headers: { 'X-CSRF-Token': 'csrf-token' } });
      }
      if (url === '/api/workbench/sessions') {
        return jsonResponse({ sessions: [ctsOnlySession] });
      }
      if (url === '/api/workbench/sessions/session-1') {
        return jsonResponse(ctsOnlySession);
      }
      if (url === '/api/workbench/sessions/session-1/candidates') {
        return jsonResponse({ items: [] });
      }
      if (url.startsWith('/api/workbench/detail-open-requests')) {
        return jsonResponse({ requests: [] });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse([
          event({ eventName: 'source_run_completed', sourceRunId: 'src-cts', sourceKind: 'cts' }),
        ]);
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByText('单源')).toBeInTheDocument();
    await openFinalShortlistNode();
    expect(await screen.findByText('未找到匹配候选人')).toBeInTheDocument();
    expect(screen.getByText('已完成检索，但当前条件没有候选人进入短名单。')).toBeInTheDocument();
    expect(within(screen.getByTestId('source-card-cts')).getByText('完成')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '已完成' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('source-card-liepin')).not.toBeInTheDocument();
  });

  it('shows Liepin detail counters and updates the session detail policy', async () => {
    const currentSession = session({
      requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
      sourceCards: [
        {
          ...session().sourceCards[0],
        },
        {
          ...session().sourceCards[1],
          status: 'completed',
          authState: 'not_required',
          connectionId: 'conn-liepin-1',
          connectionStatus: 'connected',
          connectionWarningCode: null,
          connectionWarningMessage: null,
          detailOpenUsedCount: 7,
          detailOpenBlockedCount: 2,
        },
      ],
    });
    const policyRequests: Array<{ headers: Headers; body: unknown }> = [];

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
      if (url === '/api/workbench/sessions/session-1/source-runs/liepin/policy' && init.method === 'PUT') {
        policyRequests.push({
          headers: new Headers(init.headers),
          body: JSON.parse(String(init.body)),
        });
        return jsonResponse({
          sessionId: 'session-1',
          sourceKind: 'liepin',
          detailOpenMode: 'bypass_confirm',
          updatedAt: '2026-05-09T00:06:00Z',
        });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    const liepin = await screen.findByTestId('source-card-liepin');
    expect(liepin).toHaveTextContent('DETAIL');
    expect(liepin).toHaveTextContent('7');
    expect(liepin).toHaveTextContent('BLOCK');
    expect(liepin).toHaveTextContent('2');

    await userEvent.selectOptions(within(liepin).getByLabelText('详情模式'), 'bypass_confirm');

    await waitFor(() => expect(policyRequests).toHaveLength(1));
    expect(policyRequests[0].headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(policyRequests[0].body).toEqual({ detailOpenMode: 'bypass_confirm' });
  });

  it('renders final shortlist node candidates with source badges and evidence level', async () => {
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

    expect(screen.queryByTestId('candidate-card-review-1')).not.toBeInTheDocument();

    await openFinalShortlistNode();
    const card = await screen.findByTestId('candidate-card-review-1');

    expect(card).toHaveTextContent('Lin Qian');
    expect(card).toHaveTextContent('Senior Backend Engineer');
    expect(card).toHaveTextContent('CTS');
    expect(card).toHaveTextContent('final');
    expect(card).toHaveTextContent('FastAPI / retrieval systems');
    expect(card).toHaveTextContent('benchmark depth unclear');
  });

  it('creates detail requests for Liepin card evidence from candidate cards', async () => {
    const requests: Array<{ url: string; body?: unknown; headers: Headers }> = [];
    const liepinCandidate = candidateReviewItem({
      reviewItemId: 'review-liepin',
      sourceBadges: ['Liepin'],
      evidenceLevel: 'card',
      evidence: [
        {
          evidenceId: 'evidence-liepin',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'card',
          score: null,
          fitBucket: 'card',
          matchedMustHaves: ['FastAPI'],
          matchedPreferences: [],
          missingRisks: ['Detail page not opened yet.'],
          strengths: [],
          weaknesses: [],
          createdAt: '2026-05-09T00:04:00Z',
        },
      ],
    });

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
        return candidateQueueResponse([liepinCandidate]);
      }
      if (url === '/api/workbench/sessions/session-1/candidates/review-liepin/detail-open-requests') {
        requests.push({
          url,
          body: JSON.parse(String(init.body)),
          headers: new Headers(init.headers),
        });
        return jsonResponse(detailOpenRequest(), { status: 202 });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await openFinalShortlistNode();
    const card = await screen.findByTestId('candidate-card-review-liepin');
    expect(within(card).queryByRole('button', { name: 'Open Liepin' })).not.toBeInTheDocument();
    await userEvent.click(within(card).getByRole('button', { name: 'Request detail' }));

    await waitFor(() => expect(requests.map((request) => request.url)).toContain('/api/workbench/sessions/session-1/candidates/review-liepin/detail-open-requests'));
    expect(requests.every((request) => request.headers.get('X-CSRF-Token') === 'csrf-token')).toBe(true);
    expect(requests.find((request) => request.body)?.body).toEqual({ idempotencyKey: 'detail:review-liepin' });
    expect(await within(card).findByText('Detail request is waiting for approval.')).toBeInTheDocument();
  });

  it('opens known Liepin detail actions without showing another detail request', async () => {
    const requests: Array<{ url: string; headers: Headers }> = [];
    const liepinCandidate = candidateReviewItem({
      reviewItemId: 'review-liepin',
      sourceBadges: ['Liepin'],
      evidenceLevel: 'detail',
      evidence: [
        {
          evidenceId: 'evidence-liepin',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'detail',
          score: null,
          fitBucket: 'card',
          matchedMustHaves: ['FastAPI'],
          matchedPreferences: [],
          missingRisks: [],
          strengths: [],
          weaknesses: [],
          createdAt: '2026-05-09T00:04:00Z',
        },
      ],
    });

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
        return candidateQueueResponse([liepinCandidate]);
      }
      if (url === '/api/workbench/sessions/session-1/candidates/review-liepin/provider-actions/open') {
        requests.push({ url, headers: new Headers(init.headers) });
        return jsonResponse({
          actionKind: 'managed_browser',
          sourceKind: 'liepin',
          connectionId: 'conn-liepin-1',
          reviewItemId: 'review-liepin',
          budgetImpact: 'none',
          message: 'Open an already-known Liepin detail view in the managed browser without reserving another budget slot.',
        });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await openFinalShortlistNode();
    const card = await screen.findByTestId('candidate-card-review-liepin');
    expect(within(card).queryByRole('button', { name: 'Request detail' })).not.toBeInTheDocument();
    await userEvent.click(within(card).getByRole('button', { name: 'Open Liepin' }));

    await waitFor(() =>
      expect(requests.map((request) => request.url)).toContain(
        '/api/workbench/sessions/session-1/candidates/review-liepin/provider-actions/open',
      ),
    );
    expect(requests.every((request) => request.headers.get('X-CSRF-Token') === 'csrf-token')).toBe(true);
    expect(await within(card).findByText(/without reserving another budget slot/i)).toBeInTheDocument();
  });

  it('renders the detail approval queue and approves pending detail requests', async () => {
    const approveRequests: string[] = [];
    const approvedDetailRequest = detailOpenRequest({
      status: 'approved',
      ledger: {
        ledgerId: 'dol-1',
        status: 'leased',
        budgetDay: '2026-05-09',
        leaseExpiresAt: '2026-05-09T00:15:00Z',
      },
      providerAction: {
        actionKind: 'managed_browser',
        sourceKind: 'liepin',
        connectionId: 'conn-liepin-1',
        reviewItemId: 'review-liepin',
        budgetImpact: 'reserved',
        message: 'Detail view lease is reserved. Continue in the managed Liepin browser.',
      },
    });
    let currentDetailRequest = detailOpenRequest();

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
      if (url === '/api/workbench/detail-open-requests?session_id=session-1') {
        return jsonResponse({ requests: [currentDetailRequest] });
      }
      if (url === '/api/workbench/detail-open-requests/dor-1/approve') {
        approveRequests.push(new Headers(init.headers).get('X-CSRF-Token') ?? '');
        currentDetailRequest = approvedDetailRequest;
        return jsonResponse(approvedDetailRequest);
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await openDetailApprovalNode();
    expect((await screen.findAllByText('详情审批')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('pending')).length).toBeGreaterThan(0);
    expect(await screen.findByText('Lin Qian')).toBeInTheDocument();
    expect(await screen.findByText(/Agent recommends opening detail/i)).toBeInTheDocument();
    expect(await screen.findByText('批准后占用 1 次详情额度')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '批准打开' }));

    await waitFor(() => expect(approveRequests).toEqual(['csrf-token']));
    await userEvent.click(await screen.findByRole('button', { name: 'Open Liepin' }));
    expect(await screen.findByText('Detail view lease is reserved. Continue in the managed Liepin browser.')).toBeInTheDocument();
  });

  it('shows budget impact text for non-pending detail request states', async () => {
    const requests = [
      detailOpenRequest({
        requestId: 'dor-approved',
        status: 'approved',
        ledger: {
          ledgerId: 'dol-approved',
          status: 'leased',
          budgetDay: '2026-05-09',
          leaseExpiresAt: '2026-05-09T00:15:00Z',
        },
      }),
      detailOpenRequest({
        requestId: 'dor-rejected',
        status: 'rejected',
      }),
      detailOpenRequest({
        requestId: 'dor-blocked',
        status: 'blocked',
        blockedReason: 'detail_budget_exhausted',
      }),
      detailOpenRequest({
        requestId: 'dor-bypassed',
        status: 'bypassed',
        candidate: null,
      }),
    ];

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
      if (url === '/api/workbench/detail-open-requests?session_id=session-1') {
        return jsonResponse({ requests });
      }
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    await openDetailApprovalNode();
    expect(await screen.findByText('详情额度已预留')).toBeInTheDocument();
    expect(screen.getByText('已跳过，不占用额度')).toBeInTheDocument();
    expect(screen.getByText('阻塞 · detail_budget_exhausted')).toBeInTheDocument();
    expect(screen.getByText('绕过确认，后台已按策略处理')).toBeInTheDocument();
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

    await openFinalShortlistNode();
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

    await openFinalShortlistNode();
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

    expect((await screen.findAllByText('Own <script>alert("must")</script> safely')).length).toBeGreaterThan(0);
    expect(container.querySelector('script')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '修改' }));
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

    await userEvent.click(await screen.findByRole('button', { name: '修改' }));
    const mustHaves = await screen.findByLabelText('Must-haves');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Python APIs\nFastAPI');
    await userEvent.clear(screen.getByLabelText('Query hints'));
    await userEvent.type(screen.getByLabelText('Query hints'), 'site:github.com python platform');
    await userEvent.click(screen.getByRole('button', { name: '保存标准' }));

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

    await userEvent.click(screen.getByRole('button', { name: '确认标准' }));

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

    await userEvent.click(await screen.findByRole('button', { name: '修改' }));
    const mustHaves = await screen.findByLabelText('Must-haves');
    await userEvent.clear(mustHaves);
    await userEvent.type(mustHaves, 'Visible unsaved must-have');
    await userEvent.click(screen.getByRole('button', { name: '确认标准' }));

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

  it('keeps source cards free of per-source start actions and preserves Liepin login actions', async () => {
    let currentSession = session();

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
      if (url.startsWith('/api/workbench/events?after_seq=0')) {
        return eventsResponse();
      }
      throw new Error(`Unexpected request ${url}`);
    });

    expect(await screen.findByTestId('source-card-cts')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动 CTS' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动猎聘' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '连接猎聘' })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: '确认标准' }));

    await waitFor(() => expect(screen.queryByRole('button', { name: '启动 CTS' })).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '启动猎聘' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '连接猎聘' })).toBeEnabled();
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
          detailOpenUsedCount: 0,
          detailOpenBlockedCount: 0,
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
