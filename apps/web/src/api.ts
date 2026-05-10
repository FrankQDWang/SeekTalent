import type {
  BootstrapResponse,
  CreateWorkbenchSessionInput,
  MeResponse,
  StartWorkbenchSourceRunInput,
  WorkbenchCandidateReviewItem,
  WorkbenchCandidateReviewItemUpdateInput,
  WorkbenchCandidateReviewQueueResponse,
  WorkbenchEventListResponse,
  WorkbenchLiepinLoginHandoffResponse,
  WorkbenchRequirementTriage,
  WorkbenchRequirementTriageInput,
  WorkbenchSession,
  WorkbenchSessionListResponse,
  WorkbenchSettingsResponse,
  WorkbenchSourceConnection,
  WorkbenchSourceConnectionListResponse,
  WorkbenchSourceRunStartResponse,
} from './types';

type BootstrapInput = {
  email: string;
  password: string;
  displayName: string;
};

type LoginInput = {
  email: string;
  password: string;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export type WorkbenchApi = {
  bootstrap(input: BootstrapInput): Promise<BootstrapResponse>;
  login(input: LoginInput): Promise<void>;
  logout(): Promise<void>;
  me(): Promise<MeResponse>;
  listSessions(): Promise<WorkbenchSessionListResponse>;
  createSession(input: CreateWorkbenchSessionInput): Promise<WorkbenchSession>;
  getSession(sessionId: string): Promise<WorkbenchSession>;
  listCandidateReviewItems(sessionId: string): Promise<WorkbenchCandidateReviewQueueResponse>;
  updateCandidateReviewItem(
    sessionId: string,
    reviewItemId: string,
    input: WorkbenchCandidateReviewItemUpdateInput,
  ): Promise<WorkbenchCandidateReviewItem>;
  updateRequirementTriage(sessionId: string, input: WorkbenchRequirementTriageInput): Promise<WorkbenchRequirementTriage>;
  approveRequirementTriage(sessionId: string): Promise<WorkbenchRequirementTriage>;
  startSourceRun(sessionId: string, input: StartWorkbenchSourceRunInput): Promise<WorkbenchSourceRunStartResponse>;
  listEvents(afterSeq?: number): Promise<WorkbenchEventListResponse>;
  settings(): Promise<WorkbenchSettingsResponse>;
  listSourceConnections(): Promise<WorkbenchSourceConnectionListResponse>;
  createLiepinConnection(): Promise<WorkbenchSourceConnection>;
  getSourceConnection(connectionId: string): Promise<WorkbenchSourceConnection>;
  startLiepinLogin(connectionId: string): Promise<WorkbenchLiepinLoginHandoffResponse>;
};

const CSRF_HEADER = 'X-CSRF-Token';
const EVENT_PAGE_LIMIT = 200;
const EVENT_MAX_PAGES = 25;

function isMutating(method: string | undefined) {
  return method !== undefined && method.toUpperCase() !== 'GET' && method.toUpperCase() !== 'HEAD';
}

async function responseMessage(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
  return payload?.detail ?? payload?.error ?? `Request failed with status ${String(response.status)}`;
}

export function createWorkbenchApi(): WorkbenchApi {
  let csrfToken = '';

  async function request<T>(path: string, init: RequestInit = {}, csrfRetry = true): Promise<T> {
    const method = init.method ?? 'GET';
    const headers = new Headers(init.headers);
    const shouldSendCsrf = csrfToken && isMutating(method) && !headers.has(CSRF_HEADER);
    if (init.body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (shouldSendCsrf) {
      headers.set(CSRF_HEADER, csrfToken);
    }

    const response = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: 'include',
    });

    const refreshedCsrf = response.headers.get(CSRF_HEADER);
    if (refreshedCsrf) {
      csrfToken = refreshedCsrf;
    }

    if (!response.ok) {
      if (response.status === 403 && csrfRetry && shouldSendCsrf) {
        await request<MeResponse>('/api/auth/me', {}, false);
        return request<T>(path, init, false);
      }
      throw new ApiError(await responseMessage(response), response.status);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  return {
    bootstrap(input) {
      return request<BootstrapResponse>('/api/auth/bootstrap', {
        method: 'POST',
        body: JSON.stringify(input),
      });
    },
    login(input) {
      return request<void>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify(input),
      });
    },
    logout() {
      return request<void>('/api/auth/logout', { method: 'POST' });
    },
    me() {
      return request<MeResponse>('/api/auth/me');
    },
    listSessions() {
      return request<WorkbenchSessionListResponse>('/api/workbench/sessions');
    },
    createSession(input) {
      return request<WorkbenchSession>('/api/workbench/sessions', {
        method: 'POST',
        body: JSON.stringify(input),
      });
    },
    getSession(sessionId) {
      return request<WorkbenchSession>(`/api/workbench/sessions/${encodeURIComponent(sessionId)}`);
    },
    listCandidateReviewItems(sessionId) {
      return request<WorkbenchCandidateReviewQueueResponse>(
        `/api/workbench/sessions/${encodeURIComponent(sessionId)}/candidates`,
      );
    },
    updateCandidateReviewItem(sessionId, reviewItemId, input) {
      return request<WorkbenchCandidateReviewItem>(
        `/api/workbench/sessions/${encodeURIComponent(sessionId)}/candidates/${encodeURIComponent(reviewItemId)}`,
        {
          method: 'PUT',
          body: JSON.stringify(input),
        },
      );
    },
    updateRequirementTriage(sessionId, input) {
      return request<WorkbenchRequirementTriage>(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/triage`, {
        method: 'PUT',
        body: JSON.stringify(input),
      });
    },
    approveRequirementTriage(sessionId) {
      return request<WorkbenchRequirementTriage>(
        `/api/workbench/sessions/${encodeURIComponent(sessionId)}/triage/approve`,
        { method: 'POST' },
      );
    },
    startSourceRun(sessionId, input) {
      return request<WorkbenchSourceRunStartResponse>(
        `/api/workbench/sessions/${encodeURIComponent(sessionId)}/source-runs`,
        {
          method: 'POST',
          body: JSON.stringify(input),
        },
      );
    },
    async listEvents(afterSeq = 0) {
      const events: WorkbenchEventListResponse['events'] = [];
      let cursor = afterSeq;
      for (let page = 0; page < EVENT_MAX_PAGES; page += 1) {
        const response = await request<WorkbenchEventListResponse>(
          `/api/workbench/events?after_seq=${encodeURIComponent(String(cursor))}&limit=${String(EVENT_PAGE_LIMIT)}`,
        );
        events.push(...response.events);
        if (response.events.length < EVENT_PAGE_LIMIT) {
          break;
        }
        cursor = response.events[response.events.length - 1].globalSeq;
      }
      return { events };
    },
    settings() {
      return request<WorkbenchSettingsResponse>('/api/workbench/settings');
    },
    listSourceConnections() {
      return request<WorkbenchSourceConnectionListResponse>('/api/workbench/source-connections');
    },
    createLiepinConnection() {
      return request<WorkbenchSourceConnection>('/api/workbench/source-connections/liepin', { method: 'POST' });
    },
    getSourceConnection(connectionId) {
      return request<WorkbenchSourceConnection>(
        `/api/workbench/source-connections/${encodeURIComponent(connectionId)}`,
      );
    },
    startLiepinLogin(connectionId) {
      return request<WorkbenchLiepinLoginHandoffResponse>(
        `/api/workbench/source-connections/${encodeURIComponent(connectionId)}/login`,
        { method: 'POST' },
      );
    },
  };
}
