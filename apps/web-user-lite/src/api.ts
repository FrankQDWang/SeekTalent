import type { CandidateDetailResponse, CreateRunResponse, RunResponse } from './types';

export type ApiClient = {
  createRun(input: { jdText: string; sourcingPreferenceText?: string }): Promise<CreateRunResponse>;
  getRun(runId: string): Promise<RunResponse>;
  getCandidateDetail(runId: string, candidateId: string): Promise<CandidateDetailResponse>;
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  const payload = (await response.json().catch(() => null)) as { error?: string } | null;
  if (!response.ok) {
    throw new Error(payload?.error ?? `Request failed with status ${String(response.status)}`);
  }
  return payload as T;
}

export function createHttpApi(): ApiClient {
  return {
    createRun(input) {
      return requestJson<CreateRunResponse>('/api/runs', {
        method: 'POST',
        body: JSON.stringify(input),
      });
    },
    getRun(runId) {
      return requestJson<RunResponse>(`/api/runs/${encodeURIComponent(runId)}`);
    },
    getCandidateDetail(runId, candidateId) {
      return requestJson<CandidateDetailResponse>(
        `/api/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}`,
      );
    },
  };
}
