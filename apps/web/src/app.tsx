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
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, type WorkbenchApi } from './api';
import {
  RECRUITER_PHASES,
  RECRUITER_RUN_DURATION_SECONDS,
  clampRunElapsed,
  getRecruiterChannelSnapshot,
  getRecruiterRunSnapshot,
  type PlaybackState,
  type RecruiterCandidate,
  type RecruiterGraphEdge,
  type RecruiterGraphNode,
  type RecruiterRunSnapshot,
} from './recruiterAnimation';
import type {
  BootstrapResponse,
  CreateWorkbenchSessionInput,
  MeResponse,
  SourceKind,
  WorkbenchCandidateReviewItem,
  WorkbenchCandidateReviewItemUpdateInput,
  WorkbenchCandidateReviewQueueResponse,
  WorkbenchEvent,
  WorkbenchEventListResponse,
  WorkbenchLiepinLoginHandoffResponse,
  WorkbenchRequirementTriage,
  WorkbenchRequirementTriageInput,
  WorkbenchSession,
  WorkbenchSessionListResponse,
  WorkbenchSettingsResponse,
  WorkbenchSettingsSource,
  WorkbenchSourceConnection,
  WorkbenchSourceConnectionListResponse,
  WorkbenchSourceCard,
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
const WorkbenchRuntimeContext = createContext<WorkbenchRouterContext | null>(null);
const PlaybackContext = createContext<{
  playbackState: PlaybackState;
  elapsedSeconds: number;
  snapshot: RecruiterRunSnapshot;
  startPlayback: () => void;
  togglePlayback: () => void;
  resetPlayback: () => void;
} | null>(null);

function sessionKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId] as const;
}

function eventListKey(afterSeq = 0) {
  return ['workbench', 'events', afterSeq] as const;
}

function candidateQueueKey(sessionId: string) {
  return ['workbench', 'sessions', sessionId, 'candidates'] as const;
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

function usePlayback() {
  const playback = useContext(PlaybackContext);
  if (!playback) {
    throw new Error('Playback context is missing.');
  }
  return playback;
}

function useRecruiterPlayback() {
  const [playbackState, setPlaybackState] = useState<PlaybackState>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const elapsedRef = useRef(0);

  useEffect(() => {
    elapsedRef.current = elapsedSeconds;
  }, [elapsedSeconds]);

  useEffect(() => {
    if (playbackState !== 'running') {
      return undefined;
    }

    const startedAt = performance.now() - elapsedRef.current * 1000;
    const intervalId = window.setInterval(() => {
      const nextElapsed = clampRunElapsed((performance.now() - startedAt) / 1000);
      elapsedRef.current = nextElapsed;
      setElapsedSeconds(nextElapsed);
      if (nextElapsed >= RECRUITER_RUN_DURATION_SECONDS) {
        setPlaybackState('complete');
        window.clearInterval(intervalId);
      }
    }, 100);

    return () => window.clearInterval(intervalId);
  }, [playbackState]);

  function startPlayback() {
    if (playbackState === 'complete' || elapsedRef.current >= RECRUITER_RUN_DURATION_SECONDS) {
      elapsedRef.current = 0;
      setElapsedSeconds(0);
    }
    setPlaybackState('running');
  }

  function togglePlayback() {
    if (playbackState === 'running') {
      setPlaybackState('paused');
      return;
    }
    startPlayback();
  }

  function resetPlayback() {
    elapsedRef.current = 0;
    setElapsedSeconds(0);
    setPlaybackState('idle');
  }

  const snapshot = useMemo(
    () => getRecruiterRunSnapshot(playbackState, elapsedSeconds),
    [elapsedSeconds, playbackState],
  );

  return {
    playbackState,
    elapsedSeconds: snapshot.elapsedSeconds,
    snapshot,
    startPlayback,
    togglePlayback,
    resetPlayback,
  };
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
  const playback = useRecruiterPlayback();
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
    <PlaybackContext.Provider value={playback}>
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
          <span className="mono-label">ROUND</span>
          <span className="run-dots" aria-hidden="true">
            <span className={playback.playbackState !== 'idle' ? 'active' : ''} />
            <span className={playback.snapshot.elapsedSeconds >= 10 ? 'active' : ''} />
            <span className={playback.snapshot.elapsedSeconds >= 26 ? 'active' : ''} />
          </span>
          <span className="source-dot" aria-hidden="true" />
          <span className="mono-label status-text">{playback.snapshot.statusLabel}</span>
          <span className="topbar-divider" />
          <span className="mono-label">{playback.snapshot.topTimer}</span>
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
      <AppPlaybackBar />
    </main>
    </PlaybackContext.Provider>
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

function AppPlaybackBar() {
  const { playbackState, snapshot, togglePlayback, resetPlayback } = usePlayback();
  const playbackStarted = playbackState !== 'idle';
  const running = playbackState === 'running';

  return (
    <footer
      className={`playback-bar ${playbackState}`}
      style={{ '--run-progress': `${snapshot.progressPercent}%` } as CSSProperties}
    >
      <button
        className="play-button"
        type="button"
        aria-label={running ? 'Pause playback' : playbackState === 'complete' ? 'Replay playback' : playbackStarted ? 'Resume playback' : 'Start playback'}
        onClick={togglePlayback}
      >
        {running ? '||' : playbackState === 'complete' ? '↻' : '>'}
      </button>
      <button className="mini-control" type="button" aria-label="Restart timeline" onClick={resetPlayback}>
        ↺
      </button>
      <button className="mini-control" type="button" aria-label="Timeline clock">
        ○
      </button>
      <div className="timeline-track" aria-label="Run timeline">
        {RECRUITER_PHASES.map((phase) => (
          <span
            key={phase.id}
            className={playbackStarted && snapshot.elapsedSeconds >= phase.at ? `phase-dot active ${phase.tone}` : `phase-dot ${phase.tone}`}
          >
            {phase.label}
          </span>
        ))}
      </div>
      <span className="elapsed mono-label">{snapshot.bottomTimer}</span>
    </footer>
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
  const eventsQuery = useWorkbenchEvents(api);
  const [sourceFilter, setSourceFilter] = useState<SourceKind | 'all'>('all');
  const triageApproved = session.requirementTriage.status === 'approved';
  const sessionEvents = (eventsQuery.data?.events ?? []).filter((event) => event.sessionId === session.sessionId);
  const visibleEvents =
    sourceFilter === 'all' ? sessionEvents : sessionEvents.filter((event) => event.sourceKind === sourceFilter);
  const strategyEvents = visibleEvents.filter((event) => event.eventName !== 'session_created');
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
          <span>多源</span>
          <span>{session.status}</span>
          <span>{session.sourceCards.length} sources</span>
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
        <div className="bonus-tags">
          <span>快消 / 互联网大厂双背景</span>
          <span>MBA 或海外背景优先</span>
          <span>有 IPO 前经验 加分</span>
        </div>
        <p className="section-label source-section-label">检索渠道</p>
        <div className="source-card-list">
          {session.sourceCards.map((card) => (
            <SourceCard key={card.sourceRunId} card={card} sessionId={session.sessionId} triageApproved={triageApproved} />
          ))}
        </div>
        <RequirementTriageGate key={session.sessionId} session={session} />
      </section>

      <section className="strategy-panel">
        <StrategyCanvas
          events={strategyEvents}
          loading={eventsQuery.isLoading}
          error={eventsQuery.isError}
          sourceFilter={sourceFilter}
          onSourceFilterChange={setSourceFilter}
        />
      </section>

      <section className="right-rail">
        <ActivityLog events={strategyEvents} loading={eventsQuery.isLoading} error={eventsQuery.isError} />
        <CandidateReviewQueue sessionId={session.sessionId} />
      </section>
    </div>
  );
}

function CandidateReviewQueue({ sessionId }: { sessionId: string }) {
  const { api } = useWorkbenchRuntime();
  const { playbackState, snapshot } = usePlayback();
  const query = useCandidateReviewItems(api, sessionId);
  const items = query.data?.items ?? [];
  const showDemoCandidates = items.length === 0 && playbackState !== 'idle' && snapshot.candidates.length > 0;
  const queueCount = showDemoCandidates ? snapshot.shortlistCount : items.length;
  const queueTarget = showDemoCandidates ? 4 : sessionQueueTarget(items.length);

  return (
    <div className="queue-panel">
      <div className="queue-heading">
        <span>候选人短名单</span>
        <strong>{queueCount} / {Math.max(queueCount, queueTarget)}</strong>
      </div>
      {query.isLoading ? <p className="muted">Loading candidates</p> : null}
      {query.isError ? <p className="form-error" role="alert">Could not load candidates</p> : null}
      {!query.isLoading && !query.isError && items.length === 0 && !showDemoCandidates ? (
        <div className="queue-empty">
          <strong>等待检索结果...</strong>
          <span>候选人会随着检索进度进入短名单。</span>
        </div>
      ) : null}
      {showDemoCandidates ? (
        <div className="candidate-list">
          {snapshot.candidates.map((candidate) => (
            <ReferenceCandidateCard key={candidate.id} candidate={candidate} />
          ))}
        </div>
      ) : null}
      {items.length > 0 ? (
        <div className="candidate-list">
          {items.map((item) => (
            <CandidateReviewCard key={item.reviewItemId} item={item} sessionId={sessionId} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ReferenceCandidateCard({ candidate }: { candidate: RecruiterCandidate }) {
  return (
    <article className="candidate-card reference-candidate">
      <div className="candidate-card-head">
        <div className="candidate-rank">{candidate.rank}</div>
        <div>
          <strong>
            {candidate.name} <span>{candidate.meta}</span>
          </strong>
          <span>{candidate.title}</span>
          <span>{candidate.current}</span>
        </div>
        <div className="score-badge">
          {candidate.score}
          <small>SCORE</small>
        </div>
      </div>
      <span className="source-badge amber-badge">{candidate.evidenceTag}</span>
      <ul className="candidate-evidence">
        {candidate.evidence.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="why-box">
        <span>WHY</span>
        {candidate.why}
      </div>
      <div className="candidate-source-row">
        <span className="source-badge">{candidate.source}</span>
        <small>{candidate.id}</small>
      </div>
    </article>
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
      </div>
    </article>
  );
}

function CandidateFactList({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="candidate-facts">
      <span>{label}</span>
      <p>{values.slice(0, 4).join(' / ')}</p>
    </div>
  );
}

function RequirementTriageGate({ session }: { session: WorkbenchSession }) {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
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

type TriageForm = Record<keyof WorkbenchRequirementTriageInput, string>;

function triageToForm(triage: WorkbenchRequirementTriage): TriageForm {
  return {
    mustHaves: linesFromList(triage.mustHaves),
    niceToHaves: linesFromList(triage.niceToHaves),
    synonyms: linesFromList(triage.synonyms),
    seniorityFilters: linesFromList(triage.seniorityFilters),
    exclusions: linesFromList(triage.exclusions),
    generatedQueryHints: linesFromList(triage.generatedQueryHints),
  };
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

function sourceSubtitle(card: WorkbenchSourceCard, playbackVisible: boolean, channelSubtitle: string): string {
  if (playbackVisible) {
    return channelSubtitle;
  }
  if (card.sourceKind === 'cts') {
    return '结构化简历库';
  }
  if (card.connectionStatus === 'connected') {
    return '猎聘账号通道';
  }
  return '登录后加入检索';
}

function sourceActionLabel(card: WorkbenchSourceCard): string {
  if (card.status === 'running') {
    return '运行中';
  }
  if (card.sourceKind === 'cts') {
    return '启动 CTS';
  }
  if (card.connectionStatus === 'connected') {
    return '启动猎聘';
  }
  if (card.connectionId) {
    return '继续登录';
  }
  return '连接猎聘';
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
  if (card.sourceKind === 'cts' && !triageApproved) {
    return '确认条件后启动 CTS。';
  }
  if (card.sourceKind === 'liepin' && !triageApproved) {
    return '确认条件后启动猎聘。';
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
  const { playbackState, snapshot } = usePlayback();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const channel = getRecruiterChannelSnapshot(card.sourceKind, snapshot.elapsedSeconds);
  const playbackVisible = playbackState !== 'idle';
  const startMutation = useMutation({
    mutationFn: () => api.startSourceRun(sessionId, { sourceKind: card.sourceKind }),
    onSuccess: (started) => {
      queryClient.setQueryData<WorkbenchSession>(sessionKey(sessionId), (current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          sourceRuns: current.sourceRuns.map((run) =>
            run.sourceKind === started.sourceKind ? { ...run, status: started.status } : run,
          ),
          sourceCards: current.sourceCards.map((sourceCard) =>
            sourceCard.sourceKind === started.sourceKind ? { ...sourceCard, status: started.status } : sourceCard,
          ),
        };
      });
      void queryClient.invalidateQueries({ queryKey: sessionKey(sessionId), exact: true });
      void queryClient.invalidateQueries({ queryKey: sessionListKey, exact: true });
    },
  });
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
  const isCts = card.sourceKind === 'cts';
  const isRunning = card.status === 'running';
  const liepinConnected = card.sourceKind === 'liepin' && card.connectionStatus === 'connected';
  const scannedCount = playbackVisible ? channel.scanned : (card.cardsScannedCount ?? 0);
  const hitCount = playbackVisible ? channel.hits : (card.uniqueCandidatesCount ?? 0);
  const statusTone = playbackVisible && channel.active ? 'running' : sourceStatusTone(card);
  const warning = sourceWarningMessage(card, triageApproved);
  const ctsDisabled = !triageApproved || startMutation.isPending || isRunning;
  const liepinDisabled = startMutation.isPending || createConnectionMutation.isPending || isRunning || (liepinConnected && !triageApproved);
  const actionLabel = sourceActionLabel(card);
  const actionDisabled = isCts ? ctsDisabled : liepinDisabled;
  const handleSourceAction = () => {
    if (isCts || liepinConnected) {
      startMutation.mutate();
      return;
    }
    if (card.connectionId) {
      void navigate({ to: '/connections/liepin/$connectionId/login', params: { connectionId: card.connectionId } });
      return;
    }
    createConnectionMutation.mutate();
  };

  return (
    <article className="source-card" data-testid={`source-card-${card.sourceKind}`}>
      <div className="source-card-head">
        <div className="source-identity">
          <span className={`source-icon ${card.sourceKind}`} aria-hidden="true" />
          <div>
            <strong>{card.label}</strong>
            <span>{sourceSubtitle(card, playbackVisible, channel.subtitle)}</span>
          </div>
        </div>
        <span className={`source-dot ${statusTone}`} aria-hidden="true" />
      </div>
      <div className="source-progress-row">
        <span className={`source-status-pill ${statusTone}`}>{playbackVisible ? channel.status : sourceStatusLabel(card)}</span>
        <span>
          扫描 <strong>{formatNumber(scannedCount)}</strong> · 命中 <strong>{formatNumber(hitCount)}</strong>
        </span>
      </div>
      <div className="source-card-signal" aria-label={`${card.label} source state`}>
        <span>{sourceAccessLabel(card)}</span>
        <span>{isCts ? '批量检索' : '顺序查看'}</span>
        <span>{isCts ? '可回放' : '额度保护'}</span>
      </div>
      {warning ? <p className="source-warning">{warning}</p> : null}
      <button
        className={isCts || liepinConnected ? 'source-action-button primary' : 'source-action-button'}
        type="button"
        disabled={actionDisabled}
        onClick={handleSourceAction}
      >
        {actionLabel}
      </button>
    </article>
  );
}

function ReadyStatePanel() {
  const { startPlayback } = usePlayback();

  return (
    <div className="canvas-ready">
      <div className="ready-icon" aria-hidden="true">
        AI
      </div>
      <h2>准备就绪</h2>
      <p>
        Agent 将解析岗位简报，并行访问检索渠道，通过反思与灵光迭代搜索，生成候选人短名单。
      </p>
      <button className="central-start" type="button" onClick={startPlayback}>
        启动检索
      </button>
    </div>
  );
}

function StrategyCanvas({
  events,
  loading,
  error,
  sourceFilter,
  onSourceFilterChange,
}: {
  events: WorkbenchEvent[];
  loading: boolean;
  error: boolean;
  sourceFilter: SourceKind | 'all';
  onSourceFilterChange: (source: SourceKind | 'all') => void;
}) {
  const { playbackState, snapshot } = usePlayback();
  const playbackStarted = playbackState !== 'idle';
  const nodes = playbackStarted ? snapshot.graphNodes : events.length > 0 ? makeStrategyNodes(events) : [];
  const edges = playbackStarted ? snapshot.graphEdges : [];
  const nodeCount = playbackStarted ? snapshot.nodeCount : nodes.length;

  return (
    <>
      <div className="canvas-toolbar">
        <div>
          <span className="section-label">检索策略图</span>
          <span className="mono-line">节点 {nodeCount} / 27</span>
        </div>
        <label className="canvas-filter">
          <span>Source</span>
          <select value={sourceFilter} onChange={(event) => onSourceFilterChange(event.target.value as SourceKind | 'all')}>
            <option value="all">All sources</option>
            <option value="cts">CTS</option>
            <option value="liepin">Liepin</option>
          </select>
        </label>
      </div>
      {loading ? <div className="canvas-ready compact">Loading timeline</div> : null}
      {error ? <div className="canvas-ready compact" role="alert">Could not load timeline</div> : null}
      {!loading && !error && nodes.length === 0 ? <ReadyStatePanel /> : null}
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
          <GraphEdges nodes={nodes} edges={edges} />
          {nodes.map((node, index) => (
            <article
              key={`${node.id}-${index}`}
              className="graph-node"
              data-tone={node.tone}
              data-kind={node.kind}
              style={{ '--node-x': `${node.x}%`, '--node-y': `${node.y}%` } as CSSProperties}
            >
              <span>{node.kind}</span>
              <strong>{node.label}</strong>
              <small>{node.detail}</small>
            </article>
          ))}
          {snapshot.completionText ? <div className="completion-toast">{snapshot.completionText}</div> : null}
        </div>
      ) : null}
    </>
  );
}

function GraphEdges({ nodes, edges }: { nodes: RecruiterGraphNode[]; edges: RecruiterGraphEdge[] }) {
  if (edges.length === 0) {
    return null;
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return (
    <svg className="graph-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      {edges.map((edge) => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        if (!from || !to) {
          return null;
        }
        const midX = (from.x + to.x) / 2;
        return (
          <path
            key={`${edge.from}-${edge.to}`}
            className={`graph-edge ${edge.tone}`}
            d={`M ${from.x} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${to.x} ${to.y}`}
          />
        );
      })}
    </svg>
  );
}

function ActivityLog({ events, loading, error }: { events: WorkbenchEvent[]; loading: boolean; error: boolean }) {
  const { playbackState, snapshot } = usePlayback();
  const playbackStarted = playbackState !== 'idle';
  const demoEvents = playbackStarted ? snapshot.logEntries.slice(-8) : [];

  return (
    <div className="right-log">
      <div className="right-section-head">
        <p className="section-label">岗位简报</p>
        <span className="source-dot" />
      </div>
      {loading ? <p className="muted">Loading timeline</p> : null}
      {error ? <p className="form-error" role="alert">Could not load timeline</p> : null}
      {!loading && !error && events.length === 0 && demoEvents.length === 0 ? (
        <div className="timeline-empty">{playbackStarted ? '正在初始化检索任务…' : 'No timeline events yet'}</div>
      ) : null}
      {demoEvents.length > 0 ? (
        <ol className="log-list">
          {demoEvents.map((event) => (
            <li key={event.id} className={`log-${event.tag.toLowerCase()}`}>
              <span>{event.tag}</span>
              <strong>{event.text}</strong>
            </li>
          ))}
        </ol>
      ) : null}
      {demoEvents.length === 0 && events.length > 0 ? (
        <ol className="log-list">
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
  );
}

function makeStrategyNodes(events: WorkbenchEvent[]): RecruiterGraphNode[] {
  return events.slice(0, 8).map((event, index) => {
    const label = event.eventName.replace(/^runtime_/, '').replaceAll('_', ' ');
    return {
      id: `event-${event.globalSeq}`,
      at: index,
      kind: strategyKind(event),
      label,
      detail: sourceLabel(event.sourceKind),
      x: 14 + (index % 4) * 22,
      y: 24 + Math.floor(index / 4) * 28,
      tone: strategyTone(event),
    };
  });
}

function strategyKind(event: WorkbenchEvent): RecruiterGraphNode['kind'] {
  if (event.eventName.includes('search')) {
    return '检索';
  }
  if (event.eventName.includes('candidate') || event.eventName.includes('scor')) {
    return '命中';
  }
  if (event.eventName.includes('reflection')) {
    return '反思';
  }
  if (event.eventName.includes('final')) {
    return '灵光';
  }
  return '拆解';
}

function strategyTone(event: WorkbenchEvent): RecruiterGraphNode['tone'] {
  const kind = strategyKind(event);
  if (kind === '检索') {
    return 'teal';
  }
  if (kind === '命中') {
    return 'green';
  }
  if (kind === '反思') {
    return 'violet';
  }
  if (kind === '灵光') {
    return 'amber';
  }
  return 'blue';
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

function sourceLabel(sourceKind: SourceKind | null): string {
  if (sourceKind === 'cts') {
    return 'CTS';
  }
  if (sourceKind === 'liepin') {
    return 'Liepin';
  }
  return 'Session';
}

function CreateSessionForm() {
  const { api } = useWorkbenchRuntime();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState<CreateWorkbenchSessionInput>({ jobTitle: '', jdText: '', notes: '' });
  const [error, setError] = useState('');

  const mutation = useMutation<WorkbenchSession, Error, CreateWorkbenchSessionInput>({
    mutationFn: (input) => api.createSession(input),
    onSuccess: (created) => {
      queryClient.setQueryData<WorkbenchSessionListResponse>(sessionListKey, (current) => {
        const existing = current?.sessions ?? [];
        return { sessions: [created, ...existing.filter((item) => item.sessionId !== created.sessionId)] };
      });
      queryClient.setQueryData(sessionKey(created.sessionId), created);
      setForm({ jobTitle: '', jdText: '', notes: '' });
      void navigate({ to: '/sessions/$sessionId', params: { sessionId: created.sessionId } });
    },
    onError: (err) => setError(err.message),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    mutation.mutate({
      jobTitle: form.jobTitle.trim(),
      jdText: form.jdText.trim(),
      notes: form.notes.trim(),
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
