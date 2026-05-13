import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Link,
  Outlet,
  createBrowserHistory,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
  useLocation,
  useNavigate,
} from '@tanstack/react-router';
import type { RouterHistory } from '@tanstack/react-router';
import type { CSSProperties, FormEvent, ReactNode } from 'react';
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, type WorkbenchApi } from './api';
import { NodeDetailPanel } from './NodeDetailPanel';
import type { RecruiterGraphNode } from './recruiterAnimation';
import { buildRunStory, displayTriageFromStory, type RunStory } from './runStory';
import { StrategyGraph } from './StrategyGraph';
import type {
  BootstrapResponse,
  CreateWorkbenchSessionInput,
  MeResponse,
  SourceKind,
  WorkbenchCandidateReviewItem,
  WorkbenchCandidateReviewItemUpdateInput,
  WorkbenchCandidateReviewQueueResponse,
  WorkbenchDetailOpenMode,
  WorkbenchDetailOpenRequest,
  WorkbenchDetailOpenRequestListResponse,
  WorkbenchEvent,
  WorkbenchEventListResponse,
  WorkbenchGraphCandidateListResponse,
  WorkbenchGraphCandidateResumeSnapshot,
  WorkbenchGraphCandidateSummary,
  WorkbenchLiepinLoginHandoffResponse,
  WorkbenchProviderAction,
  WorkbenchRequirementTriage,
  WorkbenchRequirementTriageInput,
  WorkbenchSession,
  WorkbenchSessionListResponse,
  WorkbenchSettingsResponse,
  WorkbenchSettingsSource,
  WorkbenchSourceConnection,
  WorkbenchSourceConnectionListResponse,
  WorkbenchSourceCard,
  WorkbenchSourceRunPolicy,
} from './types';

type WorkbenchRouterContext = {
  api: WorkbenchApi;
  queryClient: QueryClient;
};

type WorkbenchRouterOptions = {
  api: WorkbenchApi;
  queryClient?: QueryClient;
  history?: RouterHistory;
};

const sessionListKey = ['workbench', 'sessions'] as const;
const meKey = ['auth', 'me'] as const;
const settingsKey = ['workbench', 'settings'] as const;
const sourceConnectionsKey = ['workbench', 'source-connections'] as const;
const detailOpenRequestsRootKey = ['workbench', 'detail-open-requests'] as const;
const graphCandidatesRootKey = ['workbench', 'graph-candidates'] as const;
const WorkbenchRuntimeContext = createContext<WorkbenchRouterContext | null>(null);

function evidenceRefKey(
  evidenceId: string,
  sourceRunId: string,
  evidenceLevel: WorkbenchCandidateReviewItem['evidenceLevel'],
): string {
  return `${evidenceId}:${sourceRunId}:${evidenceLevel}`;
}

function candidateEvidenceGraphNodeId(
  item: WorkbenchCandidateReviewItem,
  evidenceRefToGraphNodeId: ReadonlyMap<string, string>,
  reviewItemToGraphNodeId: ReadonlyMap<string, string>,
): string | null {
  for (const evidence of item.evidence) {
    const nodeId = evidenceRefToGraphNodeId.get(
      evidenceRefKey(evidence.evidenceId, evidence.sourceRunId, evidence.evidenceLevel),
    );
    if (nodeId) {
      return nodeId;
    }
  }
  return reviewItemToGraphNodeId.get(item.reviewItemId) ?? null;
}

function candidateGraphNodePriority(node: RecruiterGraphNode): number {
  if (node.detailKind === 'liepinCardCandidates') {
    return 60;
  }
  if (node.detailKind === 'ctsRoundScoring') {
    return 50;
  }
  if (node.detailKind === 'ctsRoundResults') {
    return 40;
  }
  if (node.detailKind === 'liepinCardSearch') {
    return 30;
  }
  if (node.detailKind === 'aggregation') {
    return 20;
  }
  if (node.detailKind === 'liepinDetailApproval') {
    return 10;
  }
  return 0;
}

function setCandidateGraphNodeIndex(
  index: Map<string, string>,
  priorities: Map<string, number>,
  key: string,
  node: RecruiterGraphNode,
) {
  const priority = candidateGraphNodePriority(node);
  const existingPriority = priorities.get(key) ?? Number.NEGATIVE_INFINITY;
  if (priority > existingPriority) {
    index.set(key, node.id);
    priorities.set(key, priority);
  }
}

function sessionKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId] as const;
}

function globalEventListKey(afterSeq = 0) {
  return ['workbench', 'events', afterSeq] as const;
}

function eventListKey(sessionId: string, afterSeq = 0) {
  return ['workbench', 'sessions', sessionId, 'events', afterSeq] as const;
}

function candidateQueueKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId, 'candidates'] as const;
}

const graphCandidateSnapshotsRootKey = ['workbench', 'graph-candidate-snapshots'] as const;

function graphCandidatesKey(sessionId: string, nodeId: string | null, limit: number) {
  return [...graphCandidatesRootKey, sessionId, nodeId ?? 'none', limit] as const;
}

function graphCandidateResumeSnapshotKey(sessionId: string, graphCandidateId: string) {
  return [...graphCandidateSnapshotsRootKey, sessionId, graphCandidateId] as const;
}

function sourceRunPolicyKey(sessionId: string, sourceKind: SourceKind) {
  return ['workbench', 'sessions', sessionId, 'source-runs', sourceKind, 'policy'] as const;
}

function detailOpenRequestsKey(sessionId?: string) {
  return sessionId ? [...detailOpenRequestsRootKey, sessionId] as const : detailOpenRequestsRootKey;
}

function sourceConnectionKey(connectionId: string) {
  return ['workbench', 'source-connections', connectionId] as const;
}

const workbenchStreamEventNames = [
  'workbench_event',
] as const;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 15_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function makeRouteTree(api: WorkbenchApi, queryClient: QueryClient) {
  const rootRoute = createRootRouteWithContext<WorkbenchRouterContext>()({
    component: () => (
      <WorkbenchRuntimeContext.Provider value={{ api, queryClient }}>
        <QueryClientProvider client={queryClient}>
          <Outlet />
        </QueryClientProvider>
      </WorkbenchRuntimeContext.Provider>
    ),
  });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    beforeLoad: () => {
      throw redirect({ to: '/sessions' });
    },
  });

  const setupRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/setup',
    component: SetupPage,
  });

  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/login',
    component: LoginPage,
  });

  const protectedRoute = createRoute({
    getParentRoute: () => rootRoute,
    id: 'protected',
    beforeLoad: async ({ context }) => {
      try {
        await context.queryClient.ensureQueryData({
          queryKey: meKey,
          queryFn: () => context.api.me(),
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          throw redirect({ to: '/login' });
        }
        throw error;
      }
    },
    component: AuthenticatedLayout,
  });

  const sessionsRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/sessions',
    component: SessionsPage,
  });

  const sessionRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/sessions/$sessionId',
    component: () => {
      const { sessionId } = sessionRoute.useParams();
      return <SessionDetailPage sessionId={sessionId} />;
    },
  });

  const settingsRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/settings',
    component: SettingsPage,
  });

  const settingsSourcesRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/settings/sources',
    component: SettingsPage,
  });

  const liepinSettingsRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/settings/sources/liepin',
    component: LiepinSettingsPage,
  });

  const liepinLoginRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/connections/liepin/$connectionId/login',
    component: () => {
      const { connectionId } = liepinLoginRoute.useParams();
      return <LiepinLoginPage connectionId={connectionId} />;
    },
  });

  return rootRoute.addChildren([
    indexRoute,
    setupRoute,
    loginRoute,
    protectedRoute.addChildren([
      sessionsRoute,
      sessionRoute,
      settingsRoute,
      settingsSourcesRoute,
      liepinSettingsRoute,
      liepinLoginRoute,
    ]),
  ]);
}

export function createWorkbenchRouter({ api, queryClient = makeQueryClient(), history }: WorkbenchRouterOptions) {
  const routeTree = makeRouteTree(api, queryClient);
  return createRouter({
    routeTree,
    context: { api, queryClient },
    history: history ?? createBrowserHistory(),
    defaultPreload: 'intent',
  });
}

function useAuthUser(api: WorkbenchApi) {
  return useQuery({
    queryKey: meKey,
    queryFn: () => api.me(),
  });
}

function useSessions(api: WorkbenchApi) {
  return useQuery({
    queryKey: sessionListKey,
    queryFn: () => api.listSessions(),
  });
}

function useSettings(api: WorkbenchApi) {
  return useQuery({
    queryKey: settingsKey,
    queryFn: () => api.settings(),
  });
}

function useSourceConnections(api: WorkbenchApi) {
  return useQuery({
    queryKey: sourceConnectionsKey,
    queryFn: () => api.listSourceConnections(),
  });
}

function useSourceConnection(api: WorkbenchApi, connectionId: string) {
  return useQuery({
    queryKey: sourceConnectionKey(connectionId),
    queryFn: () => api.getSourceConnection(connectionId),
  });
}

function useSession(api: WorkbenchApi, sessionId: string) {
  return useQuery({
    queryKey: sessionKey(sessionId),
    queryFn: () => api.getSession(sessionId),
  });
}

function useCandidateReviewItems(api: WorkbenchApi, sessionId: string) {
  return useQuery({
    queryKey: candidateQueueKey(sessionId),
    queryFn: () => api.listCandidateReviewItems(sessionId),
  });
}

function useGraphCandidates(api: WorkbenchApi, sessionId: string, nodeId: string | null) {
  const limit = 50;
  return useInfiniteQuery({
    queryKey: graphCandidatesKey(sessionId, nodeId, limit),
    queryFn: ({ signal, pageParam }) =>
      api.listGraphCandidates(sessionId, nodeId ?? '', {
        limit,
        cursor: pageParam,
        signal,
      }),
    enabled: Boolean(nodeId),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}

function useGraphCandidateResumeSnapshot(
  api: WorkbenchApi,
  sessionId: string,
  graphCandidateId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: graphCandidateResumeSnapshotKey(sessionId, graphCandidateId),
    queryFn: ({ signal }) => api.getGraphCandidateResumeSnapshot(sessionId, graphCandidateId, { signal }),
    enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

function useWorkbenchEvents(api: WorkbenchApi, sessionId: string) {
  return useQuery({
    queryKey: eventListKey(sessionId, 0),
    queryFn: () => api.listSessionEvents(sessionId, 0),
  });
}

function useDetailOpenRequests(api: WorkbenchApi, sessionId: string) {
  return useQuery({
    queryKey: detailOpenRequestsKey(sessionId),
    queryFn: () => api.listDetailOpenRequests(sessionId),
  });
}

function useLiepinSourceRunPolicy(api: WorkbenchApi, sessionId: string, enabled: boolean) {
  return useQuery({
    queryKey: sourceRunPolicyKey(sessionId, 'liepin'),
    queryFn: () => api.getLiepinSourceRunPolicy(sessionId),
    enabled,
  });
}

function useWorkbenchEventStream(sessionId?: string) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      return undefined;
    }

    const eventsKey = sessionId ? eventListKey(sessionId, 0) : globalEventListKey(0);
    const currentEventList = queryClient.getQueryData<WorkbenchEventListResponse>(eventsKey);
    const lastSeq = Math.max(0, ...(currentEventList?.events.map((event) => event.globalSeq) ?? []));
    const streamPath = sessionId
      ? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/events/stream`
      : '/api/workbench/events/stream';
    const streamUrl = lastSeq > 0 ? `${streamPath}?after_seq=${encodeURIComponent(String(lastSeq))}` : streamPath;
    const stream = new EventSource(streamUrl);
    const handleEvent = (message: MessageEvent<string>) => {
      const event = parseWorkbenchEvent(message.data);
      if (!event) {
        return;
      }
      const currentEventList = queryClient.getQueryData<WorkbenchEventListResponse>(eventsKey);
      if (currentEventList && !currentEventList.events.some((item) => item.globalSeq === event.globalSeq)) {
        queryClient.setQueryData<WorkbenchEventListResponse>(eventsKey, {
          events: [...currentEventList.events, event].sort((left, right) => left.globalSeq - right.globalSeq),
        });
      }
      if (event.eventName === 'source_connection_status_changed') {
        void queryClient.invalidateQueries({ queryKey: sourceConnectionsKey, exact: true });
        void queryClient.invalidateQueries({ queryKey: settingsKey, exact: true });
        const connectionId = typeof event.payload.connectionId === 'string' ? event.payload.connectionId : '';
        if (connectionId) {
          void queryClient.invalidateQueries({ queryKey: sourceConnectionKey(connectionId), exact: true });
        }
      }
      if (event.eventName.startsWith('liepin_detail_open_')) {
        void queryClient.invalidateQueries({ queryKey: detailOpenRequestsRootKey });
      }
      if (event.eventName === 'liepin_detail_policy_updated' && event.sessionId) {
        void queryClient.invalidateQueries({ queryKey: sourceRunPolicyKey(event.sessionId, 'liepin'), exact: true });
      }
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
      if (event.sessionId) {
        void queryClient.invalidateQueries({ queryKey: sessionKey(event.sessionId), exact: true });
        if (
          event.eventName.startsWith('runtime_') ||
          event.eventName.startsWith('liepin_') ||
          event.eventName.startsWith('source_run_') ||
          event.eventName.startsWith('candidate_')
        ) {
          void queryClient.invalidateQueries({ queryKey: [...graphCandidatesRootKey, event.sessionId] });
          void queryClient.invalidateQueries({ queryKey: [...graphCandidateSnapshotsRootKey, event.sessionId] });
        }
        if (event.eventName === 'candidate_review_item_upserted' || event.eventName === 'candidate_review_item_updated') {
          void queryClient.invalidateQueries({ queryKey: candidateQueueKey(event.sessionId), exact: true });
        }
      }
    };

    for (const eventName of workbenchStreamEventNames) {
      stream.addEventListener(eventName, handleEvent);
    }

    return () => {
      for (const eventName of workbenchStreamEventNames) {
        stream.removeEventListener(eventName, handleEvent);
      }
      stream.close();
    };
  }, [queryClient, sessionId]);
}

function parseWorkbenchEvent(data: string): WorkbenchEvent | null {
  let parsed: Partial<WorkbenchEvent>;
  try {
    parsed = JSON.parse(data) as Partial<WorkbenchEvent>;
  } catch {
    return null;
  }
  if (typeof parsed.globalSeq !== 'number' || typeof parsed.eventName !== 'string') {
    return null;
  }
  return {
    globalSeq: parsed.globalSeq,
    sessionSeq: parsed.sessionSeq ?? null,
    sessionId: parsed.sessionId ?? null,
    sourceRunId: parsed.sourceRunId ?? null,
    sourceKind: parsed.sourceKind ?? null,
    eventName: parsed.eventName,
    schemaVersion: typeof parsed.schemaVersion === 'string' ? parsed.schemaVersion : undefined,
    idempotencyKey: typeof parsed.idempotencyKey === 'string' ? parsed.idempotencyKey : null,
    payload: parsed.payload ?? {},
    occurredAt: typeof parsed.occurredAt === 'string' ? parsed.occurredAt : undefined,
    createdAt: parsed.createdAt ?? '',
  };
}

function useWorkbenchRuntime(): WorkbenchRouterContext {
  const runtime = useContext(WorkbenchRuntimeContext);
  if (!runtime) {
    throw new Error('Workbench runtime context is missing.');
  }
  return runtime;
}

function SetupPage() {
  const navigate = useNavigate();
  const { api } = useWorkbenchRuntime();
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const mutation = useMutation<BootstrapResponse, Error, { email: string; password: string; displayName: string }>({
    mutationFn: (input) => api.bootstrap(input),
    onSuccess: () => {
      void navigate({ to: '/login' });
    },
    onError: (err) => setError(err.message),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    mutation.mutate({ email, password, displayName });
  }

  return (
    <AuthShell eyebrow="Initial setup" title="Create admin">
      <form className="auth-form" onSubmit={submit}>
        <label className="field">
          <span>Email</span>
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </label>
        <label className="field">
          <span>Display name</span>
          <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            minLength={8}
            required
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button className="primary-action" type="submit" disabled={mutation.isPending}>
          Create admin
        </button>
      </form>
    </AuthShell>
  );
}

function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api } = useWorkbenchRuntime();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const mutation = useMutation<void, Error, { email: string; password: string }>({
    mutationFn: (input) => api.login(input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: meKey });
      void navigate({ to: '/sessions' });
    },
    onError: (err) => setError(err.message),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    mutation.mutate({ email, password });
  }

  return (
    <AuthShell eyebrow="Workbench access" title="Log in">
      <form className="auth-form" onSubmit={submit}>
        <label className="field">
          <span>Email</span>
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </label>
        <label className="field">
          <span>Password</span>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button className="primary-action" type="submit" disabled={mutation.isPending}>
          Log in
        </button>
      </form>
    </AuthShell>
  );
}

function AuthShell({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <main className="auth-page">
      <section className="auth-panel">
        <p className="section-label">{eyebrow}</p>
        <h1>{title}</h1>
        {children}
      </section>
    </main>
  );
}

function AuthenticatedLayout() {
  const { api } = useWorkbenchRuntime();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const activeSessionId = useMemo(() => {
    const match = location.pathname.match(/^\/sessions\/([^/?#]+)$/);
    return match ? decodeURIComponent(match[1]) : undefined;
  }, [location.pathname]);
  useWorkbenchEventStream(activeSessionId);
  const userQuery = useAuthUser(api);
  const sessionsQuery = useSessions(api);
  const sessions = sessionsQuery.data?.sessions ?? [];
  const sessionError = sessionsQuery.error instanceof Error ? sessionsQuery.error.message : '';
  const logoutMutation = useMutation<void, Error>({
    mutationFn: () => api.logout(),
    onSuccess: () => {
      queryClient.clear();
      void navigate({ to: '/login' });
    },
  });

  return (
    <main className="workbench-app">
      <header className="topbar">
        <div className="brand-cluster">
          <Link to="/sessions" className="brand-mark" aria-label="New session">
            +
          </Link>
          <div>
            <strong>Recruiter / 简历智能检索</strong>
            <span>project · seektalent-workbench</span>
          </div>
        </div>
        <div className="run-cluster">
          <span className="mono-label">WORKBENCH</span>
          <span className="source-dot" aria-hidden="true" />
          <span className="mono-label status-text">{sessions.length === 1 ? '1 session' : `${String(sessions.length)} sessions`}</span>
          <span className="topbar-divider" />
          <span className="avatar">{(userQuery.data?.user.displayName ?? 'U').slice(0, 1)}</span>
          <span>{userQuery.data?.user.displayName ?? 'User'}</span>
          <Link to="/settings/sources" className="ghost-link utility-link">
            Sources
          </Link>
          <span className="utility-separator" aria-hidden="true" />
          <button className="ghost-link utility-link" type="button" onClick={() => logoutMutation.mutate()}>
            Log out
          </button>
        </div>
      </header>
      <SessionRail sessions={sessions} loading={sessionsQuery.isLoading} error={sessionError} />
      <section className="workbench-main">
        <Outlet />
      </section>
    </main>
  );
}

function SessionRail({ sessions, loading, error }: { sessions: WorkbenchSession[]; loading: boolean; error: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState('');
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return sessions;
    }
    return sessions.filter((session) => {
      return [session.jobTitle, session.jdText, session.notes].some((value) => value.toLowerCase().includes(normalized));
    });
  }, [query, sessions]);

  return (
    <aside className={collapsed ? 'session-rail collapsed' : 'session-rail'} data-testid="session-rail">
      <div className={collapsed ? 'rail-head rail-head-collapsed' : 'rail-head'}>
        <Link to="/sessions" className="rail-logo" aria-label="Sessions">
          ST
        </Link>
        <button
          className="icon-button"
          type="button"
          aria-label={collapsed ? 'Expand session rail' : 'Collapse session rail'}
          aria-expanded={!collapsed}
          aria-controls="session-rail-content"
          onClick={() => setCollapsed((value) => !value)}
        >
          {collapsed ? '>' : '<'}
        </button>
      </div>
      {!collapsed ? (
        <div id="session-rail-content" className="rail-content">
          <input
            className="rail-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions"
            aria-label="Search sessions"
          />
          <nav className="rail-list">
            {loading ? <p className="rail-empty">Loading sessions</p> : null}
            {error ? <p className="rail-empty" role="alert">Could not load sessions</p> : null}
            {!loading && !error && filtered.length === 0 ? <p className="rail-empty">No sessions</p> : null}
            {filtered.map((session) => (
              <Link
                key={session.sessionId}
                to="/sessions/$sessionId"
                params={{ sessionId: session.sessionId }}
                className="rail-item"
                activeProps={{ className: 'rail-item active' }}
              >
                <span>{session.jobTitle}</span>
                <small>{session.status}</small>
              </Link>
            ))}
          </nav>
        </div>
      ) : null}
    </aside>
  );
}

function SessionsPage() {
  return (
    <div className="reference-grid empty-session">
      <section className="jd-panel create-panel">
        <CreateSessionForm />
      </section>
      <section className="strategy-panel">
        <ReadyStatePanel />
      </section>
      <section className="right-rail">
        <div className="right-log">
          <p className="section-label">岗位简报</p>
          <div className="timeline-empty">Create a JD session to initialize the agent console.</div>
        </div>
        <div className="queue-panel">
          <div className="queue-heading">
            <span>节点详情</span>
            <strong>-</strong>
          </div>
          <div className="queue-empty">
            <strong>未选择节点</strong>
            <span>Create a session first.</span>
          </div>
        </div>
      </section>
    </div>
  );
}

function SessionDetailPage({ sessionId }: { sessionId: string }) {
  const { api } = useWorkbenchRuntime();
  const query = useSession(api, sessionId);
  const session = query.data;

  if (query.isLoading) {
    return <div className="screen-state">Loading session</div>;
  }
  if (query.isError) {
    return <div className="screen-state" role="alert">Could not load session</div>;
  }
  if (!session) {
    return <div className="screen-state">Session not found</div>;
  }

  return <WorkbenchShell session={session} />;
}

function WorkbenchShell({ session }: { session: WorkbenchSession }) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const eventsQuery = useWorkbenchEvents(api, session.sessionId);
  const candidateItemsQuery = useCandidateReviewItems(api, session.sessionId);
  const detailOpenRequestsQuery = useDetailOpenRequests(api, session.sessionId);
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null);
  const [rightDetailTab, setRightDetailTab] = useState<'notes' | 'node'>('notes');
  const [briefCollapsed, setBriefCollapsed] = useState(false);
  const [startError, setStartError] = useState('');
  const triageApproved = session.requirementTriage.status === 'approved';
  const sessionSourceKinds = useMemo(() => session.sourceCards.map((card) => card.sourceKind), [session.sourceCards]);
  const sessionEvents = eventsQuery.data?.events ?? [];
  const candidateReviewItems = useMemo(() => candidateItemsQuery.data?.items ?? [], [candidateItemsQuery.data?.items]);
  const detailOpenRequests = useMemo(
    () => detailOpenRequestsQuery.data?.requests ?? [],
    [detailOpenRequestsQuery.data?.requests],
  );
  const sessionStory = useMemo(
    () => buildRunStory({ session, events: sessionEvents, candidateReviewItems, detailOpenRequests, sourceFilter: 'all' }),
    [candidateReviewItems, detailOpenRequests, session, sessionEvents],
  );
  const visibleStory = sessionStory;
  const evidenceRefToGraphNodeId = useMemo(() => {
    const index = new Map<string, string>();
    const priorities = new Map<string, number>();
    for (const node of visibleStory.graphNodes) {
      for (const ref of node.candidateEvidenceRefs ?? []) {
        setCandidateGraphNodeIndex(
          index,
          priorities,
          evidenceRefKey(ref.evidenceId, ref.sourceRunId, ref.evidenceLevel),
          node,
        );
      }
    }
    return index;
  }, [visibleStory.graphNodes]);
  const reviewItemToGraphNodeId = useMemo(() => {
    const index = new Map<string, string>();
    const priorities = new Map<string, number>();
    for (const node of visibleStory.graphNodes) {
      for (const reviewItemId of node.candidateReviewItemIds ?? []) {
        setCandidateGraphNodeIndex(index, priorities, reviewItemId, node);
      }
    }
    return index;
  }, [visibleStory.graphNodes]);
  const selectedGraphNode = visibleStory.graphNodes.find((node) => node.id === selectedGraphNodeId) ?? null;
  useEffect(() => {
    if (!selectedGraphNodeId) {
      return;
    }
    const stillVisible = visibleStory.graphNodes.some((node) => node.id === selectedGraphNodeId);
    if (!stillVisible) {
      setSelectedGraphNodeId(null);
      if (rightDetailTab === 'node') {
        setRightDetailTab('notes');
      }
    }
  }, [rightDetailTab, selectedGraphNodeId, visibleStory.graphNodes]);
  const displayTriage = useMemo(
    () => displayTriageFromStory(session.requirementTriage, sessionStory.criteria),
    [session.requirementTriage, sessionStory.criteria],
  );
  const criteriaMode = hasTriageInput(session.requirementTriage)
    ? 'confirmed'
    : hasTriageInput(sessionStory.criteria)
      ? 'runtime'
      : 'empty';
  const canStartSession = useMemo(
    () => session.sourceCards.some((card) => isSourceRunnable(card, triageApproved)),
    [session.sourceCards, triageApproved],
  );
  const triageHasInput = hasTriageInput(session.requirementTriage);
  const triagePreparationRunning = !triageHasInput && isRequirementPreparationRunning(sessionEvents);
  const canPrepareTriage = session.sourceCards.length > 0 && !triageHasInput && !triagePreparationRunning;
  const canStartAfterApproval = useMemo(
    () => session.sourceCards.some((card) => isSourceRunnable(card, true)),
    [session.sourceCards],
  );
  const selectGraphNodeId = (nodeId: string) => {
    setSelectedGraphNodeId(nodeId);
    setRightDetailTab('node');
  };
  const handleSelectGraphNode = (node: RecruiterGraphNode) => {
    selectGraphNodeId(node.id);
  };
  function patchSessionTriage(triage: WorkbenchRequirementTriage) {
    queryClient.setQueryData<WorkbenchSession>(sessionKey(session.sessionId), (current) =>
      current ? { ...current, requirementTriage: triage } : current,
    );
    queryClient.setQueryData<WorkbenchSessionListResponse>(sessionListKey, (current) =>
      current
        ? {
            sessions: current.sessions.map((item) =>
              item.sessionId === session.sessionId ? { ...item, requirementTriage: triage } : item,
            ),
          }
        : current,
    );
  }
  const prepareTriageMutation = useMutation({
    mutationFn: () => api.prepareRequirementTriage(session.sessionId),
    onMutate: () => setStartError(''),
    onSuccess: (triage) => {
      patchSessionTriage(triage);
    },
    onError: (error) => setStartError(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: sessionKey(session.sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
      void queryClient.invalidateQueries({ queryKey: eventListKey(session.sessionId, 0), exact: true });
    },
  });
  const startSessionMutation = useMutation({
    mutationFn: () => api.startSession(session.sessionId),
    onMutate: () => setStartError(''),
    onError: (error) => setStartError(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: sessionKey(session.sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
  });
  const approveAndStartMutation = useMutation({
    mutationFn: async () => {
      const triage = await api.approveRequirementTriage(session.sessionId);
      patchSessionTriage(triage);
      return api.startSession(session.sessionId);
    },
    onMutate: () => setStartError(''),
    onError: (error) => setStartError(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: sessionKey(session.sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
      void queryClient.invalidateQueries({ queryKey: eventListKey(session.sessionId, 0), exact: true });
    },
  });
  const primaryActionLabel = !triageHasInput ? '启动 Agent' : triageApproved ? '启动检索' : '确认并开始检索';
  const primaryActionDescription = !triageHasInput
    ? 'Agent 将先拆解 JD，生成可确认的检索标准。'
    : triageApproved
      ? '启动本 session 已选择的检索源，后台事件会生成策略流程。'
      : '确认 Agent 提取的标准后，启动本 session 已选择的检索源。';
  const prepareActionPending = prepareTriageMutation.isPending || triagePreparationRunning;
  const primaryActionPending =
    prepareActionPending || approveAndStartMutation.isPending || startSessionMutation.isPending;
  const pendingRunningNote = primaryActionPending
    ? !triageHasInput
      ? '正在拆解岗位需求，准备生成可确认的检索标准。'
      : triageApproved
        ? '检索已启动，正在根据已确认标准推进所选渠道。'
        : '正在确认检索标准，并准备启动所选渠道。'
    : null;
  const primaryActionEnabled = !triageHasInput
    ? canPrepareTriage
    : triageApproved
      ? canStartSession
      : canStartAfterApproval;
  const runPrimaryAction = () => {
    if (!triageHasInput) {
      prepareTriageMutation.mutate();
    } else if (!triageApproved) {
      approveAndStartMutation.mutate();
    } else {
      startSessionMutation.mutate();
    }
  };
  return (
    <div className={`reference-grid ${briefCollapsed ? 'brief-collapsed' : ''}`}>
      {briefCollapsed ? (
        <section className="jd-panel jd-panel-collapsed" aria-label="岗位简报已收起">
          <button
            className="minimal-icon-button"
            type="button"
            aria-label="展开岗位简报列"
            onClick={() => setBriefCollapsed(false)}
          >
            ›
          </button>
        </section>
      ) : (
        <section className="jd-panel">
          <JobBrief session={session} onCollapseColumn={() => setBriefCollapsed(true)} />
          <CriteriaHighlights triage={displayTriage} mode={criteriaMode} />
          <p className="section-label source-section-label">检索渠道</p>
          <div className="source-card-list">
            {session.sourceCards.map((card) => (
              <SourceCard key={card.sourceRunId} card={card} sessionId={session.sessionId} triageApproved={triageApproved} />
            ))}
          </div>
          <RequirementTriageGate key={session.sessionId} session={session} runtimeStory={sessionStory} />
        </section>
      )}

      <section className="strategy-panel">
        <StrategyCanvas
          loading={eventsQuery.isLoading}
          error={eventsQuery.isError}
          sourceKinds={sessionSourceKinds}
          canStart={primaryActionEnabled}
          startError={startError}
          starting={primaryActionPending}
          onStart={runPrimaryAction}
          startLabel={primaryActionLabel}
          startDescription={primaryActionDescription}
          story={visibleStory}
          selectedNodeId={selectedGraphNodeId}
          onSelectNode={handleSelectGraphNode}
        />
      </section>

      <section className="right-rail">
        <RightWorkbenchTabs
          activeTab={rightDetailTab}
          onActiveTabChange={setRightDetailTab}
          notesPanel={
            <ActivityLog
              loading={eventsQuery.isLoading}
              error={eventsQuery.isError}
              pendingNote={pendingRunningNote}
              story={visibleStory}
            />
          }
          nodePanel={
            <NodeDetailPanel
              node={selectedGraphNode}
              candidatePanel={
                selectedGraphNode ? (
                  <>
                    {selectedGraphNode.detailKind === 'aggregation' ? (
                      <CandidateReviewQueue
                        session={session}
                        query={candidateItemsQuery}
                        evidenceRefToGraphNodeId={evidenceRefToGraphNodeId}
                        reviewItemToGraphNodeId={reviewItemToGraphNodeId}
                        onSelectGraphNodeId={selectGraphNodeId}
                        reviewItemIds={selectedGraphNode.candidateReviewItemIds}
                      />
                    ) : null}
                    {selectedGraphNode.detailKind !== 'aggregation' && graphNodeSupportsCandidates(selectedGraphNode) ? (
                      <GraphNodeCandidateList sessionId={session.sessionId} node={selectedGraphNode} />
                    ) : null}
                    {selectedGraphNode.detailKind === 'liepinDetailApproval' ? (
                      <DetailOpenRequestQueue sessionId={session.sessionId} query={detailOpenRequestsQuery} />
                    ) : null}
                  </>
                ) : null
              }
            />
          }
        />
      </section>
    </div>
  );
}

function RightWorkbenchTabs({
  activeTab,
  onActiveTabChange,
  notesPanel,
  nodePanel,
}: {
  activeTab: 'notes' | 'node';
  onActiveTabChange: (tab: 'notes' | 'node') => void;
  notesPanel: ReactNode;
  nodePanel: ReactNode;
}) {
  return (
    <div className="right-workbench-tabs">
      <div className="right-tab-list" role="tablist" aria-label="Workbench detail panels">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'notes'}
          aria-controls="running-notes-panel"
          id="running-notes-tab"
          onClick={() => onActiveTabChange('notes')}
        >
          运行笔记
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'node'}
          aria-controls="node-detail-panel"
          id="node-detail-tab"
          onClick={() => onActiveTabChange('node')}
        >
          节点详情
        </button>
      </div>
      <div
        id="running-notes-panel"
        role="tabpanel"
        aria-labelledby="running-notes-tab"
        hidden={activeTab !== 'notes'}
      >
        {notesPanel}
      </div>
      <div id="node-detail-panel" role="tabpanel" aria-labelledby="node-detail-tab" hidden={activeTab !== 'node'}>
        {nodePanel}
      </div>
    </div>
  );
}

function JobBrief({ session, onCollapseColumn }: { session: WorkbenchSession; onCollapseColumn: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const sourceCount = session.sourceCards.length;
  const sourceLabel = sourceCount === 1 ? '1 source' : `${String(sourceCount)} sources`;

  return (
    <section className={`job-brief-card ${expanded ? 'expanded' : ''}`} data-testid="job-brief-card">
      <div className="job-brief-head">
        <div className="panel-heading">
          <p className="section-label">岗位简报</p>
          <h2 data-testid="active-session-title">{session.jobTitle}</h2>
        </div>
        <button
          className="minimal-icon-button"
          type="button"
          aria-label="收起岗位简报列"
          onClick={onCollapseColumn}
        >
          ‹
        </button>
      </div>
      <div className="jd-pills">
        <span>{sourceCount > 1 ? '多源' : '单源'}</span>
        <span>{session.status}</span>
        <span>{sourceLabel}</span>
      </div>
      <div className="job-brief-body">
        <section className="job-brief-section">
          <div className="job-brief-section-head">
            <span>JD</span>
            <button
              className="text-inline-button"
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded ? '收起' : '展开'}
            </button>
          </div>
          <p className={expanded ? '' : 'job-brief-preview'}>{session.jdText}</p>
        </section>
        {session.notes.trim() ? (
          <section className="job-brief-section">
            <span>Notes</span>
            <p className={expanded ? '' : 'job-brief-preview short'}>{session.notes}</p>
          </section>
        ) : null}
      </div>
    </section>
  );
}

function graphNodeSupportsCandidates(node: RecruiterGraphNode) {
  return (
    node.detailKind === 'ctsRoundResults' ||
    node.detailKind === 'ctsRoundScoring' ||
    node.detailKind === 'liepinCardSearch' ||
    node.detailKind === 'liepinCardCandidates' ||
    node.detailKind === 'liepinDetailApproval' ||
    node.detailKind === 'aggregation'
  );
}

function GraphNodeCandidateList({ sessionId, node }: { sessionId: string; node: RecruiterGraphNode }) {
  const { api } = useWorkbenchRuntime();
  const query = useGraphCandidates(api, sessionId, node.id);
  const pages = query.data?.pages ?? [];
  const currentPages = pages.filter((page) => page.nodeId === node.id);
  const lastPage = currentPages[currentPages.length - 1];
  const items = currentPages.flatMap((page) => page.items);
  const total = lastPage?.totalGraphCandidates ?? lastPage?.totalEstimate ?? items.length;
  const sourceTotal = lastPage?.totalSourceResults ?? total;
  const coverage = lastPage?.coverage;
  const recoveryState = lastPage?.recoveryState;
  const title = graphCandidateListTitle(node);

  return (
    <div className="graph-candidate-panel">
      <div className="graph-candidate-heading">
        <span>{title}</span>
        <strong>{query.isLoading ? '加载中' : `已加载 ${String(items.length)} / 总计 ${String(total)}`}</strong>
      </div>
      {coverage && recoveryState !== 'recoverable_empty' ? (
        <p className="graph-candidate-coverage">
          本节点共 {String(sourceTotal)} 份简历
          {coverage.missingSnapshotCount > 0 ? `，${String(coverage.missingSnapshotCount)} 份快照暂未写入` : ''}
          {coverage.forbiddenSnapshotCount > 0 ? `，${String(coverage.forbiddenSnapshotCount)} 份受限` : ''}
        </p>
      ) : null}
      {coverage && coverage.droppedRows > 0 ? (
        <p className="form-error" role="alert">
          有 {String(coverage.droppedRows)} 份简历未能展示，请检查数据投影。
        </p>
      ) : null}
      {query.isLoading ? <p className="muted">正在读取这个节点对应的候选人...</p> : null}
      {query.isError ? <p className="form-error" role="alert">无法读取节点候选人</p> : null}
      {!query.isLoading && !query.isError && recoveryState === 'recoverable_empty' ? (
        <div className="queue-empty compact-empty">
          <strong>候选人索引需要恢复</strong>
          <span>{lastPage?.recoveryReason ?? '该节点的候选人关系暂时不可读取。'}</span>
        </div>
      ) : null}
      {!query.isLoading && !query.isError && recoveryState !== 'recoverable_empty' && items.length === 0 ? (
        <div className="queue-empty compact-empty">
          <strong>暂无候选人明细</strong>
          <span>该节点还没有可展示的候选人摘要。</span>
        </div>
      ) : null}
      {items.length > 0 ? (
        <div className="graph-candidate-list">
          {items.map((candidate) => (
            <GraphNodeCandidateCard key={candidate.graphCandidateId} sessionId={sessionId} candidate={candidate} />
          ))}
        </div>
      ) : null}
      {query.hasNextPage ? (
        <button
          className="secondary-link graph-candidate-more"
          type="button"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? '加载中' : '加载更多候选人'}
        </button>
      ) : null}
      {!query.hasNextPage && lastPage?.truncated ? <p className="muted">候选人列表已按安全上限截断。</p> : null}
    </div>
  );
}

function graphCandidateListTitle(node: RecruiterGraphNode) {
  if (node.detailKind === 'ctsRoundResults') {
    return '召回简历';
  }
  if (node.detailKind === 'ctsRoundScoring') {
    return '评分简历';
  }
  if (node.detailKind === 'liepinCardSearch' || node.detailKind === 'liepinCardCandidates') {
    return '本轮简历';
  }
  if (node.detailKind === 'liepinDetailApproval') {
    return '待处理简历';
  }
  return '本节点简历';
}

function GraphNodeCandidateCard({
  sessionId,
  candidate,
}: {
  sessionId: string;
  candidate: WorkbenchGraphCandidateSummary;
}) {
  const { api } = useWorkbenchRuntime();
  const [expanded, setExpanded] = useState(false);
  const snapshotQuery = useGraphCandidateResumeSnapshot(api, sessionId, candidate.graphCandidateId, expanded && candidate.canExpandResume);
  const subtitle = [candidate.title, candidate.company, candidate.location].filter(Boolean).join(' · ');

  return (
    <article className={`graph-candidate-card ${expanded ? 'expanded' : ''}`}>
      <div className="candidate-card-head">
        <div>
          <strong>{candidate.displayName}</strong>
          <span>{subtitle || relationshipLabel(candidate.relationshipKind)}</span>
        </div>
        <div className="score-badge">{candidate.score ?? '-'}</div>
      </div>
      <div className="badge-row">
        {candidate.sourceBadges.map((badge) => (
          <span key={badge} className="source-badge">
            {badge}
          </span>
        ))}
        <span className="source-badge muted-badge">{relationshipLabel(candidate.relationshipKind)}</span>
        {candidate.fitBucket ? <span className="source-badge muted-badge">{candidate.fitBucket}</span> : null}
      </div>
      <p className="graph-candidate-summary">{candidate.summary || '暂无简介'}</p>
      {candidate.matchedMustHaves.length > 0 ? <CandidateFactList label="Must" values={candidate.matchedMustHaves} /> : null}
      {candidate.strengths.length > 0 ? <CandidateFactList label="入围理由" values={candidate.strengths} /> : null}
      {candidate.missingRisks.length > 0 ? <CandidateFactList label="Risk" values={candidate.missingRisks} /> : null}
      <div className="candidate-actions">
        <button
          className="secondary-link"
          type="button"
          disabled={!candidate.canExpandResume}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? '收起 CTS 原始简历' : candidate.canExpandResume ? '查看 CTS 原始简历' : 'CTS 原始简历不可用'}
        </button>
      </div>
      {expanded ? <ResumeSnapshotView graphCandidateId={candidate.graphCandidateId} query={snapshotQuery} /> : null}
    </article>
  );
}

function ResumeSnapshotView({
  graphCandidateId,
  query,
}: {
  graphCandidateId: string;
  query: ReturnType<typeof useGraphCandidateResumeSnapshot>;
}) {
  if (query.isLoading) {
    return <p className="muted">正在读取安全简历快照...</p>;
  }
  if (query.isError) {
    return <p className="form-error" role="alert">无法读取简历快照</p>;
  }
  const snapshot = query.data;
  if (!snapshot) {
    return null;
  }
  if (snapshot.status !== 'ready') {
    return (
      <div className="resume-snapshot">
        <strong>{resumeSnapshotStatusLabel(snapshot.status)}</strong>
        <p>{snapshot.reason ?? '该简历快照暂时不可展示。'}</p>
      </div>
    );
  }
  return (
    <div className="resume-snapshot" data-testid={`resume-snapshot-${graphCandidateId}`}>
      {snapshot.originalResume ? (
        <OriginalResumeView resume={snapshot.originalResume} />
      ) : (
        <p className="muted">未找到 CTS 原始简历，只能展示系统整理摘要。</p>
      )}
      <details className="normalized-resume-details" open={!snapshot.originalResume}>
        <summary>系统整理摘要</summary>
        {snapshot.profile ? (
          <section>
            <strong>{snapshot.profile.displayName}</strong>
            <p>{[snapshot.profile.headline, snapshot.profile.company, snapshot.profile.location].filter(Boolean).join(' · ')}</p>
            {snapshot.profile.summary ? <p>{snapshot.profile.summary}</p> : null}
          </section>
        ) : null}
        <ResumeSnapshotList
          title="工作经历"
          items={snapshot.workExperience.map((item) => ({
            key: `${item.company}-${item.title}-${item.duration ?? ''}`,
            title: [item.title, item.company, item.duration].filter(Boolean).join(' · '),
            body: item.summary,
          }))}
        />
        <ResumeSnapshotList
          title="教育经历"
          items={snapshot.education.map((item) => ({
            key: `${item.school}-${item.degree ?? ''}-${item.major ?? ''}`,
            title: item.school,
            body: [item.degree, item.major].filter(Boolean).join(' · '),
          }))}
        />
        <ResumeSnapshotList
          title="项目"
          items={snapshot.projects.map((item) => ({
            key: item.name,
            title: item.name,
            body: item.summary,
          }))}
        />
        {snapshot.skills.length > 0 ? <CandidateFactList label="Skills" values={snapshot.skills} /> : null}
        {snapshot.sourceEvidence.length > 0 ? (
          <ResumeSnapshotList
            title="证据"
            items={snapshot.sourceEvidence.map((item) => ({
              key: `${item.label}-${item.text}`,
              title: item.label,
              body: item.text,
            }))}
          />
        ) : null}
      </details>
    </div>
  );
}

function OriginalResumeView({ resume }: { resume: NonNullable<WorkbenchGraphCandidateResumeSnapshot['originalResume']> }) {
  return (
    <section className="original-resume">
      <strong>CTS 原始简历</strong>
      {resume.sections.map((section) => (
        <section key={section.title} className="resume-snapshot-section">
          <span>{section.title}</span>
          <ul>
            {section.items.map((item, itemIndex) => (
              <li key={`${section.title}-${item.title}-${String(itemIndex)}`}>
                <strong>{item.title}</strong>
                <dl className="original-resume-fields">
                  {item.fields.map((field) => (
                    <div key={`${field.key}-${field.label}`}>
                      <dt>{field.label}</dt>
                      <dd>{field.value}</dd>
                    </div>
                  ))}
                </dl>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}

function ResumeSnapshotList({
  title,
  items,
}: {
  title: string;
  items: Array<{ key: string; title: string; body: string | null }>;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section className="resume-snapshot-section">
      <span>{title}</span>
      <ul>
        {items.map((item) => (
          <li key={item.key}>
            <strong>{item.title}</strong>
            {item.body ? <p>{item.body}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function relationshipLabel(kind: WorkbenchGraphCandidateSummary['relationshipKind']) {
  if (kind === 'new') return '新增召回';
  if (kind === 'recalled') return '召回';
  if (kind === 'scored') return '已评分';
  if (kind === 'fit') return '匹配';
  if (kind === 'not_fit') return '不匹配';
  if (kind === 'final') return '入围';
  return '详情请求';
}

function resumeSnapshotStatusLabel(status: WorkbenchGraphCandidateResumeSnapshot['status']) {
  if (status === 'snapshot_forbidden') return '简历快照受限';
  if (status === 'snapshot_not_found') return '未找到简历快照';
  return '简历快照不可用';
}

function CandidateReviewQueue({
  session,
  query,
  evidenceRefToGraphNodeId,
  reviewItemToGraphNodeId,
  onSelectGraphNodeId,
  reviewItemIds,
}: {
  session: WorkbenchSession;
  query: ReturnType<typeof useCandidateReviewItems>;
  evidenceRefToGraphNodeId: ReadonlyMap<string, string>;
  reviewItemToGraphNodeId: ReadonlyMap<string, string>;
  onSelectGraphNodeId: (nodeId: string) => void;
  reviewItemIds?: readonly string[];
}) {
  const allItems = query.data?.items ?? [];
  const reviewItemIdSet = useMemo(() => new Set(reviewItemIds ?? []), [reviewItemIds]);
  const items = reviewItemIds ? allItems.filter((item) => reviewItemIdSet.has(item.reviewItemId)) : allItems;
  const queueCount = items.length;
  const hasActiveSourceRun = session.sourceRuns.some((run) => run.status === 'queued' || run.status === 'running');
  const hasFinishedSourceRun = session.sourceRuns.some((run) => run.status === 'completed' || run.status === 'failed');
  const emptyTitle = hasFinishedSourceRun && !hasActiveSourceRun ? '未找到匹配候选人' : '等待检索结果...';
  const emptyBody =
    hasFinishedSourceRun && !hasActiveSourceRun
      ? '已完成检索，但当前条件没有候选人进入短名单。'
      : '候选人会随着检索进度进入短名单。';

  return (
    <div className="queue-panel">
      <div className="queue-heading">
        <span>最终短名单</span>
        <strong>{queueCount}</strong>
      </div>
      {query.isLoading ? <p className="muted">Loading candidates</p> : null}
      {query.isError ? <p className="form-error" role="alert">Could not load candidates</p> : null}
      {!query.isLoading && !query.isError && items.length === 0 ? (
        <div className="queue-empty">
          <strong>{emptyTitle}</strong>
          <span>{emptyBody}</span>
        </div>
      ) : null}
      {items.length > 0 ? (
        <div className="candidate-list">
          {items.map((item) => (
            <CandidateReviewCard
              key={item.reviewItemId}
              item={item}
              sessionId={session.sessionId}
              graphNodeId={candidateEvidenceGraphNodeId(item, evidenceRefToGraphNodeId, reviewItemToGraphNodeId)}
              onSelectGraphNodeId={onSelectGraphNodeId}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DetailOpenRequestQueue({
  sessionId,
  query,
}: {
  sessionId: string;
  query: ReturnType<typeof useDetailOpenRequests>;
}) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const [error, setError] = useState('');
  const [providerMessage, setProviderMessage] = useState('');
  const requests = query.data?.requests ?? [];
  const pendingRequests = requests.filter((request) => request.status === 'pending');
  const recentRequests = requests.slice(-4);
  const visibleRequests = pendingRequests.length > 0 ? pendingRequests : recentRequests;

  const refreshDetailState = () => {
    void queryClient.invalidateQueries({ queryKey: detailOpenRequestsKey(sessionId), exact: true });
    void queryClient.invalidateQueries({ queryKey: [...graphCandidatesRootKey, sessionId] });
    void queryClient.invalidateQueries({ queryKey: [...graphCandidateSnapshotsRootKey, sessionId] });
    void queryClient.invalidateQueries({ queryKey: sessionKey(sessionId), exact: true });
    void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
  };

  const approveMutation = useMutation<WorkbenchDetailOpenRequest, Error, string>({
    mutationFn: (requestId) => api.approveDetailOpenRequest(requestId),
    onSuccess: () => {
      setError('');
      setProviderMessage('');
      refreshDetailState();
    },
    onError: (err) => setError(err.message),
  });
  const rejectMutation = useMutation<WorkbenchDetailOpenRequest, Error, string>({
    mutationFn: (requestId) => api.rejectDetailOpenRequest(requestId, 'Rejected from workbench queue.'),
    onSuccess: () => {
      setError('');
      setProviderMessage('');
      refreshDetailState();
    },
    onError: (err) => setError(err.message),
  });

  if (!query.isLoading && !query.isError && visibleRequests.length === 0) {
    return null;
  }

  return (
    <div className="detail-request-panel">
      <div className="queue-heading">
        <span>详情审批</span>
        <strong>{pendingRequests.length} pending</strong>
      </div>
      {query.isLoading ? <p className="muted">Loading detail requests</p> : null}
      {query.isError ? <p className="form-error" role="alert">Could not load detail requests</p> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {providerMessage ? <p className="candidate-action-message">{providerMessage}</p> : null}
      {visibleRequests.length > 0 ? (
        <ol className="detail-request-list">
          {visibleRequests.map((request) => (
            <li key={request.requestId}>
              <div className="detail-request-main">
                <div>
                  <strong>{request.candidate?.displayName ?? 'Liepin candidate'}</strong>
                  <span>
                    {[request.candidate?.title, request.candidate?.company, request.candidate?.location]
                      .filter(Boolean)
                      .join(' · ') || request.reviewItemId.slice(-10)}
                  </span>
                </div>
                <span className={`status-pill ${request.status === 'approved' ? 'approved' : ''}`}>
                  {request.status}
                </span>
              </div>
              {request.decisionNote ? <p className="detail-request-reason">{request.decisionNote}</p> : null}
              <div className="detail-request-evidence">
                {request.candidate ? (
                  <>
                  {request.candidate.matchedMustHaves.slice(0, 3).map((value) => (
                    <span key={`must-${value}`} className="source-badge">
                      Must · {value}
                    </span>
                  ))}
                  {request.candidate.matchedPreferences.slice(0, 2).map((value) => (
                    <span key={`pref-${value}`} className="source-badge muted-badge">
                      Pref · {value}
                    </span>
                  ))}
                  </>
                ) : null}
                <span className="source-badge amber-badge">
                  {detailBudgetBadgeText(request)}
                </span>
              </div>
              {request.status === 'pending' ? (
                <div className="detail-request-actions">
                  <button
                    className="primary-action compact"
                    type="button"
                    disabled={approveMutation.isPending || rejectMutation.isPending}
                    onClick={() => approveMutation.mutate(request.requestId)}
                  >
                    批准打开
                  </button>
                  <button
                    className="secondary-link compact"
                    type="button"
                    disabled={approveMutation.isPending || rejectMutation.isPending}
                    onClick={() => rejectMutation.mutate(request.requestId)}
                  >
                    暂不打开
                  </button>
                </div>
              ) : (
                <div className="detail-request-actions">
                  {request.providerAction ? (
                    <button
                      className="secondary-link compact"
                      type="button"
                      onClick={() => setProviderMessage(request.providerAction?.message ?? '')}
                    >
                      Open Liepin
                    </button>
                  ) : null}
                  <span className="source-badge muted-badge">
                    {request.ledger?.status ?? request.blockedReason ?? request.detailOpenMode}
                  </span>
                </div>
              )}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function CandidateReviewCard({
  item,
  sessionId,
  graphNodeId,
  onSelectGraphNodeId,
}: {
  item: WorkbenchCandidateReviewItem;
  sessionId: string;
  graphNodeId: string | null;
  onSelectGraphNodeId: (nodeId: string) => void;
}) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const [note, setNote] = useState(item.note);
  const [noteDirty, setNoteDirty] = useState(false);
  const [resumeExpanded, setResumeExpanded] = useState(false);
  const [error, setError] = useState('');
  const [providerMessage, setProviderMessage] = useState('');
  const snapshotQuery = useGraphCandidateResumeSnapshot(
    api,
    sessionId,
    item.graphCandidateId ?? '',
    resumeExpanded && item.canExpandResume && Boolean(item.graphCandidateId),
  );
  const hasLiepinEvidence = item.sourceBadges.includes('Liepin') || item.evidence.some((evidence) => evidence.sourceKind === 'liepin');
  const hasLiepinDetailEvidence =
    item.evidenceLevel === 'detail' ||
    item.evidence.some((evidence) => evidence.sourceKind === 'liepin' && evidence.evidenceLevel === 'detail');

  useEffect(() => {
    setNote(item.note);
    setNoteDirty(false);
  }, [item.reviewItemId, sessionId]);

  useEffect(() => {
    if (!noteDirty) {
      setNote(item.note);
    }
  }, [item.note, noteDirty]);

  const updateMutation = useMutation<WorkbenchCandidateReviewItem, Error, WorkbenchCandidateReviewItemUpdateInput>({
    mutationFn: (input) => api.updateCandidateReviewItem(sessionId, item.reviewItemId, input),
    onSuccess: (updated) => {
      setError('');
      setNote(updated.note);
      setNoteDirty(false);
      queryClient.setQueryData<WorkbenchCandidateReviewQueueResponse>(candidateQueueKey(sessionId), (current) => {
        if (!current) {
          return current;
        }
        return {
          items: current.items.map((existing) =>
            existing.reviewItemId === updated.reviewItemId ? updated : existing,
          ),
        };
      });
    },
    onError: (err) => setError(err.message),
  });
  const providerActionMutation = useMutation<WorkbenchProviderAction, Error>({
    mutationFn: () => api.openCandidateProviderAction(sessionId, item.reviewItemId),
    onSuccess: (action) => {
      setError('');
      setProviderMessage(action.message);
    },
    onError: (err) => setError(err.message),
  });
  const detailOpenMutation = useMutation<WorkbenchDetailOpenRequest, Error>({
    mutationFn: () =>
      api.createDetailOpenRequest(sessionId, item.reviewItemId, {
        idempotencyKey: `detail:${item.reviewItemId}`,
      }),
    onSuccess: (detailRequest) => {
      setError('');
      setProviderMessage(detailOpenStatusMessage(detailRequest));
      void queryClient.invalidateQueries({ queryKey: detailOpenRequestsKey(sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionKey(sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
    onError: (err) => setError(err.message),
  });

  function updateCandidate(input: WorkbenchCandidateReviewItemUpdateInput) {
    setError('');
    updateMutation.mutate({ note, ...input });
  }

  return (
    <article className="candidate-card" data-testid={`candidate-card-${item.reviewItemId}`}>
      <div className="candidate-card-head">
        <div>
          <strong>{item.displayName}</strong>
          <span>{[item.title, item.company, item.location].filter(Boolean).join(' · ') || 'Profile summary'}</span>
        </div>
        <div className="score-badge">{item.aggregateScore ?? '-'}</div>
      </div>
      <div className="badge-row">
        {item.sourceBadges.map((badge) => (
          <span key={badge} className="source-badge">
            {badge}
          </span>
        ))}
        <span className="source-badge muted-badge">{item.evidenceLevel}</span>
        <span className={`status-pill ${item.status === 'promising' ? 'approved' : ''}`}>{item.status}</span>
      </div>
      <p>{item.summary}</p>
      {item.matchedMustHaves.length > 0 ? <CandidateFactList label="Must" values={item.matchedMustHaves} /> : null}
      {item.matchedPreferences.length > 0 ? <CandidateFactList label="加分匹配" values={item.matchedPreferences} /> : null}
      {item.strengths.length > 0 ? <CandidateFactList label="入围理由" values={item.strengths} /> : null}
      {item.missingRisks.length > 0 ? <CandidateFactList label="Risk" values={item.missingRisks} /> : null}
      {item.graphCandidateId ? (
        <>
          <div className="candidate-actions">
            <button
              className="secondary-link"
              type="button"
              disabled={!item.canExpandResume}
              aria-expanded={resumeExpanded}
              onClick={() => setResumeExpanded((current) => !current)}
            >
              {resumeExpanded ? '收起 CTS 原始简历' : item.canExpandResume ? '查看 CTS 原始简历' : 'CTS 原始简历不可用'}
            </button>
          </div>
          {resumeExpanded ? <ResumeSnapshotView graphCandidateId={item.graphCandidateId} query={snapshotQuery} /> : null}
        </>
      ) : null}
      <label className="field candidate-note">
        <span>Note</span>
        <textarea
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
            setNoteDirty(true);
          }}
          rows={3}
        />
      </label>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="candidate-actions">
        <button
          className="primary-action"
          type="button"
          disabled={updateMutation.isPending}
          onClick={() => updateCandidate({ status: 'promising' })}
        >
          Mark promising
        </button>
        <button
          className="secondary-link"
          type="button"
          disabled={updateMutation.isPending}
          onClick={() => updateCandidate({ status: 'rejected' })}
        >
          Reject
        </button>
        <button
          className="secondary-link"
          type="button"
          disabled={updateMutation.isPending}
          onClick={() => updateCandidate({})}
        >
          Save note
        </button>
        {hasLiepinEvidence ? (
          <>
            {hasLiepinDetailEvidence ? (
              <button
                className="secondary-link"
                type="button"
                disabled={providerActionMutation.isPending}
                onClick={() => providerActionMutation.mutate()}
              >
                Open Liepin
              </button>
            ) : null}
            {!hasLiepinDetailEvidence ? (
              <button
                className="secondary-link"
                type="button"
                disabled={detailOpenMutation.isPending}
                onClick={() => detailOpenMutation.mutate()}
              >
                Request detail
              </button>
            ) : null}
          </>
        ) : null}
        {graphNodeId ? (
          <button
            className="secondary-link"
            type="button"
            onClick={() => onSelectGraphNodeId(graphNodeId)}
          >
            查看策略节点
          </button>
        ) : null}
      </div>
      {providerMessage ? <p className="candidate-action-message">{providerMessage}</p> : null}
    </article>
  );
}

function detailOpenStatusMessage(detailRequest: WorkbenchDetailOpenRequest): string {
  if (detailRequest.status === 'pending') {
    return 'Detail request is waiting for approval.';
  }
  if (detailRequest.status === 'bypassed') {
    return 'Detail lease is reserved by bypass mode.';
  }
  if (detailRequest.status === 'approved') {
    return 'Detail lease is approved and reserved.';
  }
  if (detailRequest.status === 'blocked') {
    return detailRequest.blockedReason ?? 'Detail request is blocked.';
  }
  return `Detail request ${detailRequest.status}.`;
}

function detailBudgetBadgeText(detailRequest: WorkbenchDetailOpenRequest): string {
  if (detailRequest.status === 'pending') {
    return '批准后占用 1 次详情额度';
  }
  if (detailRequest.status === 'approved' || detailRequest.ledger?.status === 'leased' || detailRequest.ledger?.status === 'opened') {
    return '详情额度已预留';
  }
  if (detailRequest.status === 'rejected') {
    return '已跳过，不占用额度';
  }
  if (detailRequest.status === 'blocked') {
    return detailRequest.blockedReason ? `阻塞 · ${detailRequest.blockedReason}` : '详情打开已阻塞';
  }
  if (detailRequest.status === 'bypassed') {
    return '绕过确认，后台已按策略处理';
  }
  return `详情状态 · ${detailRequest.status}`;
}

function CandidateFactList({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="candidate-facts">
      <span>{label}</span>
      <p>{values.slice(0, 4).join(' / ')}</p>
    </div>
  );
}

function CriteriaHighlights({
  triage,
  mode,
}: {
  triage: WorkbenchRequirementTriage;
  mode: 'confirmed' | 'runtime' | 'empty';
}) {
  const chips = [
    ...triage.mustHaves.slice(0, 4),
    ...triage.niceToHaves.slice(0, 2),
    ...triage.generatedQueryHints.slice(0, 2),
  ]
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, values) => values.indexOf(item) === index)
    .slice(0, 6);
  if (chips.length === 0) {
    return null;
  }
  return (
    <div className="bonus-tags" aria-label="Extracted search criteria">
      <strong className="criteria-origin">{mode === 'runtime' ? '后台提取' : '已确认标准'}</strong>
      {chips.map((item) => (
        <span key={item}>{item}</span>
      ))}
    </div>
  );
}

function RequirementTriageGate({ session, runtimeStory }: { session: WorkbenchSession; runtimeStory: RunStory }) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const hasRuntimeCriteria = hasTriageInput(runtimeStory.criteria);
  const hasSavedTriage = hasTriageInput(session.requirementTriage);
  const reviewCriteria = useMemo(
    () => displayTriageFromStory(session.requirementTriage, runtimeStory.criteria),
    [session.requirementTriage, runtimeStory.criteria],
  );
  const [form, setForm] = useState(() => triageToForm(session.requirementTriage));
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing && !dirty) {
      setForm(triageInputToForm(reviewCriteria));
    }
  }, [dirty, editing, reviewCriteria]);

  function patchTriage(triage: WorkbenchRequirementTriage) {
    setForm(triageToForm(triage));
    setDirty(false);
    setEditing(false);
    queryClient.setQueryData<WorkbenchSession>(sessionKey(session.sessionId), (current) =>
      current ? { ...current, requirementTriage: triage } : current,
    );
    queryClient.setQueryData<WorkbenchSessionListResponse>(sessionListKey, (current) =>
      current
        ? {
            sessions: current.sessions.map((item) =>
              item.sessionId === session.sessionId ? { ...item, requirementTriage: triage } : item,
            ),
          }
        : current,
    );
  }

  const saveMutation = useMutation<WorkbenchRequirementTriage, Error, WorkbenchRequirementTriageInput>({
    mutationFn: (input) => api.updateRequirementTriage(session.sessionId, input),
    onSuccess: (triage) => {
      setError('');
      patchTriage(triage);
    },
    onError: (err) => setError(err.message),
  });

  const approveMutation = useMutation<WorkbenchRequirementTriage, Error, WorkbenchRequirementTriageInput>({
    mutationFn: async (input) => {
      if (dirty || editing || !hasSavedTriage) {
        await api.updateRequirementTriage(session.sessionId, input);
      }
      return api.approveRequirementTriage(session.sessionId);
    },
    onSuccess: (triage) => {
      setError('');
      patchTriage(triage);
    },
    onError: (err) => setError(err.message),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    saveMutation.mutate(formToTriageInput(form));
  }

  function updateForm(key: keyof TriageForm, value: string) {
    setDirty(true);
    setForm((current) => ({ ...current, [key]: value }));
  }

  function beginEditing() {
    setEditing(true);
    setDirty(false);
    setForm(triageInputToForm(reviewCriteria));
  }

  const approved = session.requirementTriage.status === 'approved';
  const mutating = saveMutation.isPending || approveMutation.isPending;
  const hasReviewCriteria = hasSavedTriage || hasRuntimeCriteria;

  if (!hasReviewCriteria && !editing) {
    return (
      <section className="triage-gate triage-gate-placeholder">
        <div className="triage-head">
          <div>
            <p className="section-label">Requirement triage gate</p>
            <h3>Search criteria</h3>
          </div>
          <span className="status-pill">{session.requirementTriage.status}</span>
        </div>
        <p className="triage-empty-copy">
          Agent 将先拆解 JD，自动生成 must-have、nice-to-have、排除项和检索提示。生成后你可以在这里审阅和微调。
        </p>
      </section>
    );
  }

  if (!editing) {
    return (
      <section className="triage-gate">
        <div className="triage-head">
          <div>
            <p className="section-label">Requirement triage gate</p>
            <h3>Search criteria</h3>
          </div>
          <span className={approved ? 'status-pill approved' : 'status-pill'}>{session.requirementTriage.status}</span>
        </div>
        <CriteriaReviewSummary criteria={reviewCriteria} mode={hasSavedTriage && approved ? 'saved' : 'runtime'} />
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="triage-actions">
          <button className="secondary-link" type="button" disabled={mutating} onClick={beginEditing}>
            修改
          </button>
          <button
            className="primary-action"
            type="button"
            disabled={mutating || approved}
            onClick={() => approveMutation.mutate(triageInputToFormInput(reviewCriteria))}
          >
            确认标准
          </button>
        </div>
      </section>
    );
  }

  return (
    <form className="triage-gate" onSubmit={submit}>
      <div className="triage-head">
        <div>
          <p className="section-label">Requirement triage gate</p>
          <h3>Search criteria</h3>
        </div>
        <span className={approved ? 'status-pill approved' : 'status-pill'}>{session.requirementTriage.status}</span>
      </div>
      <TriageTextarea label="Must-haves" value={form.mustHaves} onChange={(value) => updateForm('mustHaves', value)} />
      <TriageTextarea label="Nice-to-haves" value={form.niceToHaves} onChange={(value) => updateForm('niceToHaves', value)} />
      <TriageTextarea label="Synonyms" value={form.synonyms} onChange={(value) => updateForm('synonyms', value)} />
      <TriageTextarea
        label="Seniority filters"
        value={form.seniorityFilters}
        onChange={(value) => updateForm('seniorityFilters', value)}
      />
      <TriageTextarea label="Exclusions" value={form.exclusions} onChange={(value) => updateForm('exclusions', value)} />
      <TriageTextarea
        label="Query hints"
        value={form.generatedQueryHints}
        onChange={(value) => updateForm('generatedQueryHints', value)}
      />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="triage-actions">
        <button className="secondary-link" type="submit" disabled={mutating}>
          保存标准
        </button>
        <button
          className="secondary-link"
          type="button"
          disabled={mutating}
          onClick={() => {
            setEditing(false);
            setDirty(false);
            setForm(triageInputToForm(reviewCriteria));
          }}
        >
          取消
        </button>
        <button
          className="primary-action"
          type="button"
          disabled={mutating || approved}
          onClick={() => approveMutation.mutate(formToTriageInput(form))}
        >
          确认标准
        </button>
      </div>
    </form>
  );
}

function CriteriaReviewSummary({
  criteria,
  mode,
}: {
  criteria: WorkbenchRequirementTriageInput;
  mode: 'runtime' | 'saved';
}) {
  const rows = criteriaRows(criteria);
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="runtime-criteria-summary" aria-label="Runtime extracted search criteria">
      <div className="runtime-criteria-head">
        <span>{mode === 'runtime' ? 'Agent 提取' : '已保存标准'}</span>
      </div>
      {rows.map(([label, values]) => (
        <div key={label} className="runtime-criteria-row">
          <span>{label}</span>
          <p>{values.slice(0, 4).join(' / ')}</p>
        </div>
      ))}
    </div>
  );
}

function triageInputToFormInput(triage: WorkbenchRequirementTriageInput): WorkbenchRequirementTriageInput {
  return {
    mustHaves: [...triage.mustHaves],
    niceToHaves: [...triage.niceToHaves],
    synonyms: [...triage.synonyms],
    seniorityFilters: [...triage.seniorityFilters],
    exclusions: [...triage.exclusions],
    generatedQueryHints: [...triage.generatedQueryHints],
  };
}

type TriageForm = Record<keyof WorkbenchRequirementTriageInput, string>;

function triageToForm(triage: WorkbenchRequirementTriage): TriageForm {
  return triageInputToForm(triage);
}

function triageInputToForm(triage: WorkbenchRequirementTriageInput): TriageForm {
  return {
    mustHaves: linesFromList(triage.mustHaves),
    niceToHaves: linesFromList(triage.niceToHaves),
    synonyms: linesFromList(triage.synonyms),
    seniorityFilters: linesFromList(triage.seniorityFilters),
    exclusions: linesFromList(triage.exclusions),
    generatedQueryHints: linesFromList(triage.generatedQueryHints),
  };
}

function hasTriageInput(triage: WorkbenchRequirementTriageInput): boolean {
  return Object.values(triage).some((values) => Array.isArray(values) && values.some((value) => value.trim()));
}

function isRequirementPreparationRunning(events: WorkbenchEvent[]): boolean {
  const startedAt = maxEventSeq(
    events,
    (event) =>
      event.sourceKind === null &&
      event.sourceRunId === null &&
      (event.eventName === 'runtime_run_started' || event.eventName === 'runtime_requirements_started'),
  );
  const finishedAt = maxEventSeq(
    events,
    (event) =>
      event.sourceKind === null &&
      event.sourceRunId === null &&
      (event.eventName === 'runtime_requirements_completed' ||
        event.eventName === 'runtime_requirements_failed' ||
        event.eventName === 'requirement_triage_updated'),
  );
  return startedAt > finishedAt;
}

function maxEventSeq(events: WorkbenchEvent[], predicate: (event: WorkbenchEvent) => boolean): number {
  return events.reduce((maxSeq, event) => (predicate(event) ? Math.max(maxSeq, event.globalSeq) : maxSeq), 0);
}

function criteriaRows(triage: WorkbenchRequirementTriageInput): Array<[string, string[]]> {
  const rows: Array<[string, string[]]> = [
    ['必须条件', triage.mustHaves],
    ['加分条件', triage.niceToHaves],
    ['同义词', triage.synonyms],
    ['资历过滤', triage.seniorityFilters],
    ['排除项', triage.exclusions],
    ['检索提示', triage.generatedQueryHints],
  ];
  return rows
    .map(([label, values]): [string, string[]] => [label, values.filter((value) => value.trim())])
    .filter(([, values]) => values.length > 0);
}

function formToTriageInput(form: TriageForm): WorkbenchRequirementTriageInput {
  return {
    mustHaves: listFromLines(form.mustHaves),
    niceToHaves: listFromLines(form.niceToHaves),
    synonyms: listFromLines(form.synonyms),
    seniorityFilters: listFromLines(form.seniorityFilters),
    exclusions: listFromLines(form.exclusions),
    generatedQueryHints: listFromLines(form.generatedQueryHints),
  };
}

function listFromLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
}

function sourceStatusLabel(card: WorkbenchSourceCard): string {
  if (card.sourceKind === 'liepin' && card.connectionStatus !== 'connected') {
    return '需登录';
  }
  switch (card.status) {
    case 'queued':
      return '待命';
    case 'blocked':
      return '等待';
    case 'running':
      return '检索中';
    case 'completed':
      return '完成';
    case 'failed':
      return '异常';
    default:
      return card.status;
  }
}

function sourceLabel(sourceKind: SourceKind): string {
  return sourceKind === 'cts' ? 'CTS' : 'Liepin';
}

function isSourceRunnable(card: WorkbenchSourceCard, triageApproved: boolean): boolean {
  if (!triageApproved) {
    return false;
  }
  if (['running', 'completed', 'failed'].includes(card.status)) {
    return false;
  }
  if (card.sourceKind === 'liepin') {
    return card.connectionStatus === 'connected';
  }
  return true;
}

function sourceStatusTone(card: WorkbenchSourceCard): 'ready' | 'running' | 'blocked' | 'done' | 'failed' {
  if (card.status === 'running') {
    return 'running';
  }
  if (card.status === 'completed') {
    return 'done';
  }
  if (card.status === 'failed') {
    return 'failed';
  }
  if (card.sourceKind === 'liepin' && card.connectionStatus !== 'connected') {
    return 'blocked';
  }
  if (card.status === 'blocked') {
    return 'blocked';
  }
  return 'ready';
}

function sourceAccessLabel(card: WorkbenchSourceCard): string {
  if (card.sourceKind === 'cts') {
    return '本地库';
  }
  if (card.connectionStatus === 'connected') {
    return '账号已连接';
  }
  if (card.connectionStatus === 'login_in_progress') {
    return '登录中';
  }
  if (card.connectionStatus === 'verification_required') {
    return '待验证';
  }
  return '待连接';
}

function sourceSubtitle(card: WorkbenchSourceCard): string {
  if (card.sourceKind === 'cts') {
    return '结构化简历库';
  }
  if (card.connectionStatus === 'connected') {
    return '猎聘账号通道';
  }
  return '登录后加入检索';
}

function sourceWarningMessage(card: WorkbenchSourceCard, triageApproved: boolean): string | null {
  if (card.sourceKind === 'liepin' && card.connectionStatus !== 'connected') {
    return '连接猎聘后可加入本次检索。';
  }
  if (card.warningMessage) {
    return card.warningMessage;
  }
  if (card.connectionWarningMessage) {
    return card.connectionWarningMessage;
  }
  if (!triageApproved && !['queued', 'running', 'completed', 'failed'].includes(card.status)) {
    return '确认 Search criteria 后可启动本次检索。';
  }
  return null;
}

function linesFromList(value: string[]): string {
  return value.join('\n');
}

function TriageTextarea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field triage-field">
      <span>{label}</span>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={2} />
    </label>
  );
}

function SourceCard({
  card,
  sessionId,
  triageApproved,
}: {
  card: WorkbenchSourceCard;
  sessionId: string;
  triageApproved: boolean;
}) {
  const { api } = useWorkbenchRuntime();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [detailMode, setDetailMode] = useState<WorkbenchDetailOpenMode>('human_confirm');
  const [policyError, setPolicyError] = useState('');
  const policyQuery = useLiepinSourceRunPolicy(api, sessionId, card.sourceKind === 'liepin');
  const createConnectionMutation = useMutation<WorkbenchSourceConnection, Error>({
    mutationFn: () => api.createLiepinConnection(),
    onSuccess: (connection) => {
      queryClient.setQueryData<WorkbenchSourceConnectionListResponse>(sourceConnectionsKey, (current) => ({
        connections: [
          connection,
          ...(current?.connections.filter((item) => item.connectionId !== connection.connectionId) ?? []),
        ],
      }));
      queryClient.setQueryData(sourceConnectionKey(connection.connectionId), connection);
      void queryClient.invalidateQueries({ queryKey: sessionKey(sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
      void navigate({ to: '/connections/liepin/$connectionId/login', params: { connectionId: connection.connectionId } });
    },
  });
  const policyMutation = useMutation<WorkbenchSourceRunPolicy, Error, WorkbenchDetailOpenMode>({
    mutationFn: (mode) => api.updateLiepinSourceRunPolicy(sessionId, mode),
    onSuccess: (policy) => {
      setDetailMode(policy.detailOpenMode);
      queryClient.setQueryData(sourceRunPolicyKey(sessionId, 'liepin'), policy);
      setPolicyError('');
    },
    onError: (err) => setPolicyError(err.message),
  });
  useEffect(() => {
    if (policyQuery.data && !policyMutation.isPending) {
      setDetailMode(policyQuery.data.detailOpenMode);
    }
  }, [policyQuery.data, policyMutation.isPending]);
  const isCts = card.sourceKind === 'cts';
  const scannedCount = card.cardsScannedCount ?? 0;
  const hitCount = card.uniqueCandidatesCount ?? 0;
  const statusTone = sourceStatusTone(card);
  const warning = sourceWarningMessage(card, triageApproved);
  const needsLiepinConnection = card.sourceKind === 'liepin' && card.connectionStatus !== 'connected';
  const handleLiepinConnectionAction = () => {
    if (card.connectionId) {
      void navigate({ to: '/connections/liepin/$connectionId/login', params: { connectionId: card.connectionId } });
      return;
    }
    createConnectionMutation.mutate();
  };
  const changeDetailMode = (mode: WorkbenchDetailOpenMode) => {
    setDetailMode(mode);
    setPolicyError('');
    policyMutation.mutate(mode);
  };

  return (
    <article className="source-card" data-testid={`source-card-${card.sourceKind}`}>
      <div className="source-card-head">
        <div className="source-identity">
          <span className={`source-icon ${card.sourceKind}`} aria-hidden="true" />
          <div>
            <strong>{card.label}</strong>
            <span>{sourceSubtitle(card)}</span>
          </div>
        </div>
        <span className={`source-dot ${statusTone}`} aria-hidden="true" />
      </div>
      <div className="source-progress-row">
        <span className={`source-status-pill ${statusTone}`}>{sourceStatusLabel(card)}</span>
        <span>
          扫描 <strong>{formatNumber(scannedCount)}</strong> · 命中 <strong>{formatNumber(hitCount)}</strong>
        </span>
      </div>
      <div className="source-card-signal" aria-label={`${card.label} source state`}>
        <span>{sourceAccessLabel(card)}</span>
        <span>{isCts ? '批量检索' : '顺序查看'}</span>
        <span>{isCts ? '可回放' : '额度保护'}</span>
      </div>
      {card.sourceKind === 'liepin' ? (
        <>
          <dl className="source-state-strip detail-ledger-strip" aria-label="Liepin detail budget state">
            <div>
              <dt>DETAIL</dt>
              <dd>{formatNumber(card.detailOpenUsedCount ?? 0)}</dd>
            </div>
            <div>
              <dt>BLOCK</dt>
              <dd>{formatNumber(card.detailOpenBlockedCount ?? 0)}</dd>
            </div>
          </dl>
          <label className="source-policy-control">
            <span>详情模式</span>
            <select
              value={detailMode}
              disabled={policyMutation.isPending}
              onChange={(event) => changeDetailMode(event.target.value as WorkbenchDetailOpenMode)}
            >
              <option value="human_confirm">人工确认</option>
              <option value="bypass_confirm">绕过确认</option>
            </select>
          </label>
        </>
      ) : null}
      {warning ? <p className="source-warning">{warning}</p> : null}
      {policyError ? <p className="source-warning" role="alert">{policyError}</p> : null}
      {needsLiepinConnection ? (
        <button
          className="source-action-button"
          type="button"
          disabled={createConnectionMutation.isPending}
          onClick={handleLiepinConnectionAction}
        >
          {card.connectionId ? '继续登录' : '连接猎聘'}
        </button>
      ) : null}
    </article>
  );
}

function ReadyStatePanel({
  canStart,
  onStart,
  startDescription = 'Agent 将先拆解 JD，生成可确认的检索标准。',
  startError,
  startLabel = '启动 Agent',
  starting,
}: {
  canStart?: boolean;
  onStart?: () => void;
  startDescription?: string;
  startError?: string;
  startLabel?: string;
  starting?: boolean;
}) {
  return (
    <div className="canvas-ready">
      <div className="ready-icon" aria-hidden="true">
        AI
      </div>
      <h2>准备就绪</h2>
      <p>{startDescription}</p>
      {onStart && (canStart || starting || startError) ? (
        <button className="central-start" type="button" disabled={!canStart || starting} onClick={onStart}>
          {starting ? '处理中' : startLabel}
        </button>
      ) : null}
      {startError ? <p className="form-error" role="alert">{startError}</p> : null}
    </div>
  );
}

function StrategyCanvas({
  loading,
  error,
  sourceKinds,
  canStart,
  onStart,
  startError,
  startDescription,
  startLabel,
  starting,
  story,
  selectedNodeId,
  onSelectNode,
}: {
  loading: boolean;
  error: boolean;
  sourceKinds: SourceKind[];
  canStart: boolean;
  onStart: () => void;
  startError: string;
  startDescription: string;
  startLabel: string;
  starting: boolean;
  story: RunStory;
  selectedNodeId: string | null;
  onSelectNode: (node: RecruiterGraphNode) => void;
}) {
  const hasStory = story.graphNodes.length > 0;
  const nodes = hasStory ? story.graphNodes : [];
  const activeLaneKinds = sourceKinds.filter((sourceKind) => nodes.some((node) => node.lane === sourceKind));

  return (
    <>
      <div className="canvas-toolbar">
        <div>
          <span className="section-label">检索策略图</span>
        </div>
      </div>
      {loading ? <div className="canvas-ready compact">Loading timeline</div> : null}
      {error ? <div className="canvas-ready compact" role="alert">Could not load timeline</div> : null}
      {!loading && !error && nodes.length === 0 ? (
        <ReadyStatePanel
          canStart={canStart}
          onStart={onStart}
          startDescription={startDescription}
          startError={startError}
          startLabel={startLabel}
          starting={starting}
        />
      ) : null}
      {nodes.length > 0 ? (
        <div className="strategy-canvas" data-testid="strategy-canvas">
          <div className="graph-grid" aria-hidden="true" />
          {activeLaneKinds.length > 1 ? <SourceLaneBands sourceKinds={activeLaneKinds} /> : null}
          <StrategyGraph story={story} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
          {canStart || starting || startError ? (
            <div className="canvas-start-overlay">
              <button className="central-start" type="button" disabled={!canStart || starting} onClick={onStart}>
                {starting ? '处理中' : startLabel}
              </button>
              {startError ? <p className="form-error" role="alert">{startError}</p> : null}
            </div>
          ) : null}
          {story.completionText ? (
            <div className="completion-toast">{story.completionText}</div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function SourceLaneBands({ sourceKinds }: { sourceKinds: SourceKind[] }) {
  return (
    <div className="source-lane-bands" aria-hidden="true">
      {sourceKinds.map((sourceKind) => (
        <div
          key={sourceKind}
          className={`source-lane-band ${sourceKind}`}
          style={{ '--lane-y': `${sourceKind === 'cts' ? 30 : 70}%` } as CSSProperties}
        >
          <span>{sourceLabel(sourceKind)}</span>
        </div>
      ))}
    </div>
  );
}

function ActivityLog({
  loading,
  error,
  pendingNote,
  story,
}: {
  loading: boolean;
  error: boolean;
  pendingNote?: string | null;
  story: RunStory;
}) {
  const businessEvents = useMemo(() => {
    if (!pendingNote) {
      return story.logEntries;
    }
    const duplicate = story.logEntries.some((entry) => entry.text === pendingNote);
    if (duplicate) {
      return story.logEntries;
    }
    return [
      ...story.logEntries,
      {
        id: 'pending-running-note',
        at: Number.MAX_SAFE_INTEGER,
        tag: 'SYS' as const,
        text: pendingNote,
        sourceKind: 'all' as const,
        sourceLabel: 'All sources',
        lane: 'shared' as const,
        relatedNodeId: undefined,
        noteKind: 'waiting',
        statusHint: 'waiting',
      },
    ];
  }, [pendingNote, story.logEntries]);
  const latestLogRef = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    const latestLog = latestLogRef.current;
    if (typeof latestLog?.scrollIntoView === 'function') {
      latestLog.scrollIntoView({ block: 'end' });
    }
  }, [businessEvents.length]);

  return (
    <div className="right-log">
      <div className="right-section-head">
        <p className="section-label">运行笔记</p>
      </div>
      {loading ? <p className="muted">Loading timeline</p> : null}
      {error ? <p className="form-error" role="alert">Could not load timeline</p> : null}
      {!loading && !error && businessEvents.length === 0 ? (
        <div className="timeline-empty">
          等待 Agent 生成业务笔记。
        </div>
      ) : null}
      {businessEvents.length > 0 ? (
        <ol className="log-stream" aria-label="运行笔记流" aria-live="polite" aria-relevant="additions text">
          {businessEvents.map((event, index) => (
            <li
              key={event.id}
              ref={index === businessEvents.length - 1 ? latestLogRef : undefined}
              className={`log-line log-${event.tag.toLowerCase()}`}
            >
              <span className="log-line-dot" aria-hidden="true" />
              <p className="log-line-text">
                {event.sourceLabel && event.sourceKind !== 'all' ? <em className="log-source-badge">{event.sourceLabel}</em> : null}
                <StreamingNoteText
                  text={event.text}
                  latest={index === businessEvents.length - 1}
                  waiting={index === businessEvents.length - 1 && (event.noteKind === 'waiting' || event.statusHint === 'waiting')}
                />
              </p>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function StreamingNoteText({ text, latest, waiting }: { text: string; latest: boolean; waiting: boolean }) {
  return (
    <span
      className={`streaming-note-text${latest ? ' is-latest' : ''}${waiting ? ' is-waiting' : ''}`}
      data-testid={latest ? 'latest-streaming-note' : undefined}
    >
      <span className="streaming-note-base">{text}</span>
      <span className="streaming-note-fill" aria-hidden="true">{text}</span>
    </span>
  );
}

function CreateSessionForm() {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState<CreateWorkbenchSessionInput>({
    jobTitle: '',
    jdText: '',
    notes: '',
    sourceKinds: ['cts'],
  });
  const [error, setError] = useState('');

  const mutation = useMutation<WorkbenchSession, Error, CreateWorkbenchSessionInput>({
    mutationFn: (input) => api.createSession(input),
    onSuccess: (created) => {
      queryClient.setQueryData<WorkbenchSessionListResponse>(sessionListKey, (current) => {
        const existing = current?.sessions ?? [];
        return { sessions: [created, ...existing.filter((item) => item.sessionId !== created.sessionId)] };
      });
      queryClient.setQueryData(sessionKey(created.sessionId), created);
      setForm({ jobTitle: '', jdText: '', notes: '', sourceKinds: ['cts'] });
      void navigate({ to: '/sessions/$sessionId', params: { sessionId: created.sessionId } });
    },
    onError: (err) => setError(err.message),
  });

  function toggleSourceKind(sourceKind: SourceKind) {
    setForm((current) => {
      const sourceKinds = current.sourceKinds ?? ['cts'];
      const next = sourceKinds.includes(sourceKind)
        ? sourceKinds.filter((item) => item !== sourceKind)
        : [...sourceKinds, sourceKind];
      return { ...current, sourceKinds: next };
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    const sourceKinds = form.sourceKinds ?? ['cts'];
    if (sourceKinds.length === 0) {
      setError('Select at least one source.');
      return;
    }
    mutation.mutate({
      jobTitle: form.jobTitle.trim(),
      jdText: form.jdText.trim(),
      notes: form.notes.trim(),
      sourceKinds,
    });
  }

  return (
    <form className="create-form" onSubmit={submit}>
      <div className="panel-head">
        <p className="section-label">New session</p>
        <h2>Create session</h2>
      </div>
      <label className="field">
        <span>Job title</span>
        <input
          value={form.jobTitle}
          onChange={(event) => setForm((value) => ({ ...value, jobTitle: event.target.value }))}
          required
        />
      </label>
      <label className="field">
        <span>JD</span>
        <textarea
          value={form.jdText}
          onChange={(event) => setForm((value) => ({ ...value, jdText: event.target.value }))}
          required
          rows={8}
        />
      </label>
      <label className="field">
        <span>Notes</span>
        <textarea
          value={form.notes}
          onChange={(event) => setForm((value) => ({ ...value, notes: event.target.value }))}
          rows={4}
        />
      </label>
      <fieldset className="source-picker">
        <legend>Sources</legend>
        <label>
          <input
            type="checkbox"
            aria-label="CTS"
            checked={(form.sourceKinds ?? []).includes('cts')}
            onChange={() => toggleSourceKind('cts')}
          />
          <span>CTS</span>
          <small>结构化简历库</small>
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="Liepin"
            checked={(form.sourceKinds ?? []).includes('liepin')}
            onChange={() => toggleSourceKind('liepin')}
          />
          <span>Liepin</span>
          <small>登录后加入检索</small>
        </label>
      </fieldset>
      {error ? <p className="form-error">{error}</p> : null}
      <button className="primary-action" type="submit" disabled={mutation.isPending}>
        Create session
      </button>
    </form>
  );
}

function SettingsPage() {
  const { api } = useWorkbenchRuntime();
  const query = useSettings(api);
  const sources = query.data?.sources ?? [];

  return (
    <section className="settings-page">
      <div className="panel settings-panel">
        <div className="panel-head">
          <p className="section-label">Settings</p>
          <h2>Sources</h2>
        </div>
        {query.isLoading ? <p className="muted">Loading sources</p> : null}
        {query.isError ? <p className="form-error" role="alert">Could not load settings</p> : null}
        {!query.isError ? (
          <div className="settings-list">
            {sources.map((source) => (
              <SettingsSourceRow key={source.sourceKind} source={source} />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function SettingsSourceRow({ source }: { source: WorkbenchSettingsSource }) {
  return (
    <article className="settings-row" data-testid={`settings-source-${source.sourceKind}`}>
      <div>
        <strong>{source.label}</strong>
        <span>{source.sourceKind}</span>
      </div>
      <div className="settings-badges">
        <span>{source.enabled ? 'Enabled' : 'Disabled'}</span>
        <span>{source.authRequired ? 'Login required' : 'No login required'}</span>
        {source.sourceKind === 'liepin' ? (
          <Link to="/settings/sources/liepin" className="secondary-link">
            Configure
          </Link>
        ) : null}
      </div>
    </article>
  );
}

function LiepinSettingsPage() {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const query = useSourceConnections(api);
  const liepinConnections = (query.data?.connections ?? []).filter((connection) => connection.sourceKind === 'liepin');
  const createMutation = useMutation<WorkbenchSourceConnection, Error>({
    mutationFn: () => api.createLiepinConnection(),
    onSuccess: (connection) => {
      queryClient.setQueryData<WorkbenchSourceConnectionListResponse>(sourceConnectionsKey, (current) => ({
        connections: [
          connection,
          ...(current?.connections.filter((item) => item.connectionId !== connection.connectionId) ?? []),
        ],
      }));
      queryClient.setQueryData(sourceConnectionKey(connection.connectionId), connection);
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
  });

  return (
    <section className="settings-page">
      <div className="panel settings-panel">
        <div className="panel-head">
          <p className="section-label">Source settings</p>
          <h2>Liepin connection</h2>
        </div>
        {query.isLoading ? <p className="muted">Loading Liepin connection</p> : null}
        {query.isError ? <p className="form-error" role="alert">Could not load Liepin connection</p> : null}
        {!query.isLoading && !query.isError && liepinConnections.length === 0 ? (
          <div className="connection-empty">
            <strong>No Liepin connection</strong>
            <span>Create a scoped connection before opening the isolated login route.</span>
            <button
              className="primary-action"
              type="button"
              disabled={createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              Create Liepin connection
            </button>
          </div>
        ) : null}
        {liepinConnections.map((connection) => (
          <LiepinConnectionCard key={connection.connectionId} connection={connection} />
        ))}
      </div>
    </section>
  );
}

function LiepinConnectionCard({ connection }: { connection: WorkbenchSourceConnection }) {
  return (
    <article className="connection-card" data-testid={`source-connection-${connection.connectionId}`}>
      <div>
        <strong>{connection.label}</strong>
        <span>{connection.connectionId}</span>
      </div>
      <dl>
        <div>
          <dt>Status</dt>
          <dd>{connection.status}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{connection.updatedAt}</dd>
        </div>
      </dl>
      {connection.warningMessage ? <p>{connection.warningMessage}</p> : null}
      <Link
        to="/connections/liepin/$connectionId/login"
        params={{ connectionId: connection.connectionId }}
        className="primary-action"
      >
        Open isolated login
      </Link>
    </article>
  );
}

function LiepinLoginPage({ connectionId }: { connectionId: string }) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const query = useSourceConnection(api, connectionId);
  const [handoff, setHandoff] = useState<WorkbenchLiepinLoginHandoffResponse | null>(null);
  const loginMutation = useMutation<WorkbenchLiepinLoginHandoffResponse, Error>({
    mutationFn: () => api.startLiepinLogin(connectionId),
    onSuccess: (response) => {
      setHandoff(response);
      void queryClient.invalidateQueries({ queryKey: sourceConnectionKey(connectionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sourceConnectionsKey, exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
  });
  const connection = query.data;

  return (
    <section className="login-relay-page">
      <div className="login-relay-panel">
        <div className="panel-head">
          <p className="section-label">Isolated source login</p>
          <h2>Liepin managed-browser login</h2>
        </div>
        {query.isLoading ? <p className="muted">Loading connection</p> : null}
        {query.isError ? <p className="form-error" role="alert">Could not load connection</p> : null}
        {connection ? (
          <>
            <div className="connection-card compact">
              <div>
                <strong>{connection.label}</strong>
                <span>{connection.connectionId}</span>
              </div>
              <dl>
                <div>
                  <dt>Status</dt>
                  <dd>{handoff?.status ?? connection.status}</dd>
                </div>
                <div>
                  <dt>Mode</dt>
                  <dd>server_managed_browser</dd>
                </div>
              </dl>
              <p>
                This route is isolated from the main workbench and exposes only safe interaction state,
                never browser credential material or automation endpoints.
              </p>
              {(handoff?.warningMessage ?? connection.warningMessage) ? (
                <p>{handoff?.warningMessage ?? connection.warningMessage}</p>
              ) : null}
            </div>
            <button
              className="primary-action"
              type="button"
              disabled={loginMutation.isPending}
              onClick={() => loginMutation.mutate()}
            >
              Start isolated login
            </button>
            {loginMutation.isError ? <p className="form-error" role="alert">Could not start Liepin login</p> : null}
            {handoff ? (
              <div className="relay-status">
                <strong>{handoff.handoffState}</strong>
                <span>{handoff.safeFrameUrl ? 'Safe frame available' : 'Safe frame pending'}</span>
              </div>
            ) : null}
            {handoff?.safeFrameUrl ? (
              <iframe
                className="login-safe-frame"
                title="Liepin safe login frame"
                src={handoff.safeFrameUrl}
                sandbox="allow-forms allow-same-origin allow-scripts"
                referrerPolicy="same-origin"
              />
            ) : null}
          </>
        ) : null}
        <Link to="/settings/sources/liepin" className="secondary-link">
          Back to Liepin settings
        </Link>
      </div>
    </section>
  );
}
