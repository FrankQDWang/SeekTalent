import {
  QueryClient,
  QueryClientProvider,
  useMutation,
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
  useNavigate,
} from '@tanstack/react-router';
import type { RouterHistory } from '@tanstack/react-router';
import type { CSSProperties, FormEvent } from 'react';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { ApiError, type WorkbenchApi } from './api';
import type { RecruiterGraphNode } from './recruiterAnimation';
import { buildRunStory, displayTriageFromStory, type RunStory, type SourceFilter } from './runStory';
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
const WorkbenchRuntimeContext = createContext<WorkbenchRouterContext | null>(null);

function sessionKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId] as const;
}

function eventListKey(afterSeq = 0) {
  return ['workbench', 'events', afterSeq] as const;
}

function candidateQueueKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId, 'candidates'] as const;
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

function useWorkbenchEvents(api: WorkbenchApi) {
  return useQuery({
    queryKey: eventListKey(0),
    queryFn: () => api.listEvents(0),
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

function useWorkbenchEventStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      return undefined;
    }

    const stream = new EventSource('/api/workbench/events/stream');
    const handleEvent = (message: MessageEvent<string>) => {
      const event = parseWorkbenchEvent(message.data);
      if (!event) {
        return;
      }
      const eventsKey = eventListKey(0);
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
  }, [queryClient]);
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
    payload: parsed.payload ?? {},
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
  const queryClient = useQueryClient();
  useWorkbenchEventStream();
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
          <Link to="/sessions" className="brand-mark" aria-label="SeekTalent sessions">
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
      <div className="rail-head">
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
            <span>候选人短名单</span>
            <strong>0 / 0</strong>
          </div>
          <div className="queue-empty">
            <strong>No session selected</strong>
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
  const eventsQuery = useWorkbenchEvents(api);
  const candidateItemsQuery = useCandidateReviewItems(api, session.sessionId);
  const detailOpenRequestsQuery = useDetailOpenRequests(api, session.sessionId);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null);
  const [startError, setStartError] = useState('');
  const triageApproved = session.requirementTriage.status === 'approved';
  const sessionSourceKinds = useMemo(() => session.sourceCards.map((card) => card.sourceKind), [session.sourceCards]);
  useEffect(() => {
    if (sourceFilter !== 'all' && !sessionSourceKinds.includes(sourceFilter)) {
      setSourceFilter('all');
    }
  }, [sessionSourceKinds, sourceFilter]);
  const sessionEvents = (eventsQuery.data?.events ?? []).filter((event) => event.sessionId === session.sessionId);
  const visibleEvents =
    sourceFilter === 'all' ? sessionEvents : sessionEvents.filter((event) => event.sourceKind === sourceFilter);
  const strategyEvents = visibleEvents.filter((event) => event.eventName !== 'session_created');
  const candidateReviewItems = candidateItemsQuery.data?.items ?? [];
  const detailOpenRequests = detailOpenRequestsQuery.data?.requests ?? [];
  const sessionStory = useMemo(
    () => buildRunStory({ session, events: sessionEvents, candidateReviewItems, detailOpenRequests, sourceFilter: 'all' }),
    [candidateReviewItems, detailOpenRequests, session, sessionEvents],
  );
  const visibleStory = useMemo(
    () => buildRunStory({ session, events: sessionEvents, candidateReviewItems, detailOpenRequests, sourceFilter }),
    [candidateReviewItems, detailOpenRequests, session, sessionEvents, sourceFilter],
  );
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
  const handleSelectGraphNode = (node: RecruiterGraphNode) => setSelectedGraphNodeId(node.id);
  const startSessionMutation = useMutation({
    mutationFn: () => api.startSession(session.sessionId),
    onMutate: () => setStartError(''),
    onError: (error) => setStartError(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: sessionKey(session.sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
  });
  const requirementLines = session.jdText
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5);

  return (
    <div className="reference-grid">
      <section className="jd-panel">
        <div className="panel-heading">
          <p className="section-label">岗位简报</p>
          <h2 data-testid="active-session-title">{session.jobTitle}</h2>
          <span className="mono-line">Project · {session.sessionId.slice(-12)}</span>
        </div>
        <div className="jd-pills">
          <span>{session.sourceCards.length > 1 ? '多源' : '单源'}</span>
          <span>{session.status}</span>
          <span>{session.sourceCards.length === 1 ? '1 source' : `${String(session.sourceCards.length)} sources`}</span>
        </div>
        {session.notes ? (
          <div className="client-line">
            <span>客户</span>
            <p>{session.notes}</p>
          </div>
        ) : null}
        <div className="requirement-summary">
          <span>硬性要求</span>
          <ol>
            {(requirementLines.length > 0 ? requirementLines : [session.jdText]).map((line, index) => (
              <li key={`${line}-${String(index)}`}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <p>{line}</p>
              </li>
            ))}
          </ol>
        </div>
        <CriteriaHighlights triage={displayTriage} mode={criteriaMode} />
        <p className="section-label source-section-label">检索渠道</p>
        <div className="source-card-list">
          {session.sourceCards.map((card) => (
            <SourceCard key={card.sourceRunId} card={card} sessionId={session.sessionId} triageApproved={triageApproved} />
          ))}
        </div>
        <RequirementTriageGate key={session.sessionId} session={session} runtimeStory={sessionStory} />
      </section>

      <section className="strategy-panel">
        <StrategyCanvas
          events={strategyEvents}
          loading={eventsQuery.isLoading}
          error={eventsQuery.isError}
          sourceFilter={sourceFilter}
          onSourceFilterChange={setSourceFilter}
          sourceKinds={sessionSourceKinds}
          canStart={canStartSession}
          startError={startError}
          starting={startSessionMutation.isPending}
          onStart={() => startSessionMutation.mutate()}
          story={visibleStory}
          selectedNodeId={selectedGraphNodeId}
          onSelectNode={handleSelectGraphNode}
        />
      </section>

      <section className="right-rail">
        <ActivityLog
          events={strategyEvents}
          loading={eventsQuery.isLoading}
          error={eventsQuery.isError}
          story={visibleStory}
          sourceFilter={sourceFilter}
          onSourceFilterChange={setSourceFilter}
          sourceKinds={sessionSourceKinds}
        />
        <DetailOpenRequestQueue sessionId={session.sessionId} query={detailOpenRequestsQuery} />
        <CandidateReviewQueue session={session} query={candidateItemsQuery} />
      </section>
    </div>
  );
}

function CandidateReviewQueue({
  session,
  query,
}: {
  session: WorkbenchSession;
  query: ReturnType<typeof useCandidateReviewItems>;
}) {
  const items = query.data?.items ?? [];
  const queueCount = items.length;
  const queueTarget = sessionQueueTarget(items.length);
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
        <span>候选人短名单</span>
        <strong>{queueCount} / {Math.max(queueCount, queueTarget)}</strong>
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
            <CandidateReviewCard key={item.reviewItemId} item={item} sessionId={session.sessionId} />
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

function sessionQueueTarget(count: number): number {
  return Math.max(4, count);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function CandidateReviewCard({ item, sessionId }: { item: WorkbenchCandidateReviewItem; sessionId: string }) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const [note, setNote] = useState(item.note);
  const [noteDirty, setNoteDirty] = useState(false);
  const [error, setError] = useState('');
  const [providerMessage, setProviderMessage] = useState('');
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
      {item.missingRisks.length > 0 ? <CandidateFactList label="Risk" values={item.missingRisks} /> : null}
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
  const [form, setForm] = useState(() => triageToForm(session.requirementTriage));
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) {
      setForm(triageToForm(session.requirementTriage));
    }
  }, [dirty, session.requirementTriage]);

  function patchTriage(triage: WorkbenchRequirementTriage) {
    setForm(triageToForm(triage));
    setDirty(false);
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
      if (dirty) {
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

  function useRuntimeCriteria() {
    setDirty(true);
    setForm(triageInputToForm(runtimeStory.criteria));
  }

  const approved = session.requirementTriage.status === 'approved';
  const mutating = saveMutation.isPending || approveMutation.isPending;

  return (
    <form className="triage-gate" onSubmit={submit}>
      <div className="triage-head">
        <div>
          <p className="section-label">Requirement triage gate</p>
          <h3>Search criteria</h3>
        </div>
        <span className={approved ? 'status-pill approved' : 'status-pill'}>{session.requirementTriage.status}</span>
      </div>
      {hasRuntimeCriteria ? (
        <RuntimeCriteriaSummary criteria={runtimeStory.criteria} onUse={useRuntimeCriteria} />
      ) : null}
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
          Save triage
        </button>
        <button
          className="primary-action"
          type="button"
          disabled={mutating || approved}
          onClick={() => approveMutation.mutate(formToTriageInput(form))}
        >
          Approve triage
        </button>
      </div>
    </form>
  );
}

function RuntimeCriteriaSummary({
  criteria,
  onUse,
}: {
  criteria: WorkbenchRequirementTriageInput;
  onUse: () => void;
}) {
  const rows = criteriaRows(criteria);
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="runtime-criteria-summary" aria-label="Runtime extracted search criteria">
      <div className="runtime-criteria-head">
        <span>后台运行提取</span>
        <button className="secondary-link compact" type="button" onClick={onUse}>
          填入表单
        </button>
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

function criteriaRows(triage: WorkbenchRequirementTriageInput): Array<[string, string[]]> {
  const rows: Array<[string, string[]]> = [
    ['Must', triage.mustHaves],
    ['Nice', triage.niceToHaves],
    ['Synonyms', triage.synonyms],
    ['Seniority', triage.seniorityFilters],
    ['Exclude', triage.exclusions],
    ['Query', triage.generatedQueryHints],
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
  startError,
  starting,
}: {
  canStart?: boolean;
  onStart?: () => void;
  startError?: string;
  starting?: boolean;
}) {
  return (
    <div className="canvas-ready">
      <div className="ready-icon" aria-hidden="true">
        AI
      </div>
      <h2>准备就绪</h2>
      <p>
        确认左侧 Search criteria 后，启动本 session 已选择的检索源。这里会随着后台事件生成策略流程。
      </p>
      {onStart && (canStart || startError) ? (
        <button className="central-start" type="button" disabled={!canStart || starting} onClick={onStart}>
          {starting ? '启动中' : '启动检索'}
        </button>
      ) : null}
      {startError ? <p className="form-error" role="alert">{startError}</p> : null}
    </div>
  );
}

function SourceFilterControl({
  sourceFilter,
  onSourceFilterChange,
  sourceKinds,
  label = 'Source',
}: {
  sourceFilter: SourceFilter;
  onSourceFilterChange: (source: SourceFilter) => void;
  sourceKinds: SourceKind[];
  label?: string;
}) {
  return (
    <label className="canvas-filter">
      <span>{label}</span>
      <select value={sourceFilter} onChange={(event) => onSourceFilterChange(event.target.value as SourceFilter)}>
        <option value="all">All sources</option>
        {sourceKinds.includes('cts') ? <option value="cts">CTS</option> : null}
        {sourceKinds.includes('liepin') ? <option value="liepin">Liepin</option> : null}
      </select>
    </label>
  );
}

function StrategyCanvas({
  events,
  loading,
  error,
  sourceFilter,
  onSourceFilterChange,
  sourceKinds,
  canStart,
  onStart,
  startError,
  starting,
  story,
  selectedNodeId,
  onSelectNode,
}: {
  events: WorkbenchEvent[];
  loading: boolean;
  error: boolean;
  sourceFilter: SourceFilter;
  onSourceFilterChange: (source: SourceFilter) => void;
  sourceKinds: SourceKind[];
  canStart: boolean;
  onStart: () => void;
  startError: string;
  starting: boolean;
  story: RunStory;
  selectedNodeId: string | null;
  onSelectNode: (node: RecruiterGraphNode) => void;
}) {
  const hasStory = story.graphNodes.length > 0;
  const nodes = hasStory ? story.graphNodes : [];
  const nodeCount = nodes.length;
  const nodeTotal = hasStory ? story.nodeTotal : 0;
  const activeLaneKinds = sourceKinds.filter((sourceKind) => nodes.some((node) => node.lane === sourceKind));

  return (
    <>
      <div className="canvas-toolbar">
        <div>
          <span className="section-label">检索策略图</span>
          <span className="mono-line">节点 {nodeCount} / {nodeTotal}</span>
        </div>
        <SourceFilterControl sourceFilter={sourceFilter} onSourceFilterChange={onSourceFilterChange} sourceKinds={sourceKinds} />
      </div>
      {loading ? <div className="canvas-ready compact">Loading timeline</div> : null}
      {error ? <div className="canvas-ready compact" role="alert">Could not load timeline</div> : null}
      {!loading && !error && nodes.length === 0 ? (
        <ReadyStatePanel canStart={canStart} onStart={onStart} startError={startError} starting={starting} />
      ) : null}
      {nodes.length > 0 ? (
        <div className="strategy-canvas" data-testid="strategy-canvas">
          <div className="canvas-legend">
            {[
              ['拆解', 'blue'],
              ['检索', 'teal'],
              ['命中', 'green'],
              ['反思', 'violet'],
              ['灵光', 'amber'],
            ].map(([label, tone]) => (
              <span key={label} className={`legend-${tone}`}>
                {label}
              </span>
            ))}
          </div>
          <div className="graph-grid" aria-hidden="true" />
          {sourceFilter === 'all' && activeLaneKinds.length > 1 ? <SourceLaneBands sourceKinds={activeLaneKinds} /> : null}
          <StrategyGraph story={story} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
          {canStart || startError ? (
            <div className="canvas-start-overlay">
              <button className="central-start" type="button" disabled={!canStart || starting} onClick={onStart}>
                {starting ? '启动中' : '启动检索'}
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
  events,
  loading,
  error,
  story,
  sourceFilter,
  onSourceFilterChange,
  sourceKinds,
}: {
  events: WorkbenchEvent[];
  loading: boolean;
  error: boolean;
  story: RunStory;
  sourceFilter: SourceFilter;
  onSourceFilterChange: (source: SourceFilter) => void;
  sourceKinds: SourceKind[];
}) {
  const hasStory = story.logEntries.length > 0;
  const businessEvents = hasStory ? story.logEntries.slice(-10) : [];
  const [showDeveloperLog, setShowDeveloperLog] = useState(false);

  return (
    <div className="right-log">
      <div className="right-section-head">
        <p className="section-label">运行笔记</p>
        <SourceFilterControl
          sourceFilter={sourceFilter}
          onSourceFilterChange={onSourceFilterChange}
          sourceKinds={sourceKinds}
          label="View"
        />
      </div>
      {loading ? <p className="muted">Loading timeline</p> : null}
      {error ? <p className="form-error" role="alert">Could not load timeline</p> : null}
      {!loading && !error && businessEvents.length === 0 ? (
        <div className="timeline-empty">
          {events.length > 0 ? '已有后台技术事件，等待可转写的业务流程事件。' : 'No timeline events yet'}
        </div>
      ) : null}
      {businessEvents.length > 0 ? (
        <ol className="log-list">
          {businessEvents.map((event) => (
            <li key={event.id} className={`log-${event.tag.toLowerCase()}`}>
              <span>{event.tag}</span>
              <strong>
                {event.sourceLabel && event.sourceKind !== 'all' ? <em className="log-source-badge">{event.sourceLabel}</em> : null}
                {event.text}
              </strong>
            </li>
          ))}
        </ol>
      ) : null}
      {events.length > 0 ? (
        <div className="developer-log-panel">
          <button className="secondary-link" type="button" onClick={() => setShowDeveloperLog((value) => !value)}>
            {showDeveloperLog ? 'Hide developer log' : 'Developer log'}
          </button>
          {showDeveloperLog ? (
            <ol className="log-list developer-log-list">
              {events.slice(-8).map((event) => (
                <li key={event.globalSeq}>
                  <span>{logTag(event)}</span>
                  <strong>{event.eventName}</strong>
                  <small>{event.createdAt || `#${String(event.globalSeq)}`}</small>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function logTag(event: WorkbenchEvent): string {
  if (event.eventName.startsWith('runtime_')) {
    return 'THINK';
  }
  if (event.eventName.includes('source_run')) {
    return 'PLAN';
  }
  return 'SYS';
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
