import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from './api';
import { createApp } from './app';
import type { CandidateDetailResponse, RunResponse } from './types';

function buildCandidateDetail(): CandidateDetailResponse {
  return {
    candidate: {
      candidateId: 'cand-1',
      externalIdentityId: 'cand-1',
      name: 'Lin Qian',
      title: 'Senior Python Agent Engineer',
      company: 'Hewa Talent Cloud',
      location: '上海',
      summary: 'Must 92/100, preferred 65/100, risk 8/100.',
    },
    resumeView: {
      snapshotId: 'snapshot-cand-1',
      projection: {
        workYear: 8,
        currentLocation: '上海',
        expectedLocation: '上海',
        jobState: 'open',
        expectedSalary: '面议',
        age: 34,
        education: [],
        workExperience: [],
        workSummaries: ['python', 'agent'],
        projectNames: ['Resume ranking CLI'],
      },
    },
    aiAnalysis: {
      status: 'completed',
      summary: 'Reasoning summary',
      evidenceSpans: ['python', 'agent'],
      riskFlags: [],
    },
    verdictHistory: [],
  };
}

function buildCompletedRun(): RunResponse {
  return {
    runId: 'run-1',
    status: 'completed',
    errorMessage: null,
    finalShortlist: [
      {
        candidateId: 'cand-1',
        externalIdentityId: 'cand-1',
        name: 'Lin Qian',
        title: 'Senior Python Agent Engineer',
        company: 'Hewa Talent Cloud',
        location: '上海',
        summary: 'Must 92/100, preferred 65/100, risk 8/100.',
        reason: 'Direct Python agent experience with tracing and ranking.',
        score: 0.92,
        sourceRound: 1,
      },
    ],
  };
}

function setupApp(api: ApiClient) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const app = createApp(root, api, { pollIntervalMs: 100 });
  return { root, app };
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('createApp', () => {
  it('requires only jd before enabling start', () => {
    const api: ApiClient = {
      createRun: vi.fn(),
      getRun: vi.fn(),
      getCandidateDetail: vi.fn(),
    };
    const { root, app } = setupApp(api);
    const button = root.querySelector<HTMLButtonElement>('#start-button');
    const jdInput = root.querySelector<HTMLTextAreaElement>('#jd-input');
    const notesInput = root.querySelector<HTMLTextAreaElement>('#notes-input');

    expect(button?.disabled).toBe(true);

    jdInput!.value = 'JD';
    jdInput!.dispatchEvent(new Event('input', { bubbles: true }));
    expect(button?.disabled).toBe(false);

    notesInput!.value = 'Notes';
    notesInput!.dispatchEvent(new Event('input', { bubbles: true }));
    expect(button?.disabled).toBe(false);

    app.destroy();
  });

  it('starts a run with empty notes', async () => {
    const api: ApiClient = {
      createRun: vi.fn().mockResolvedValue({ runId: 'run-1', status: 'queued' }),
      getRun: vi.fn().mockResolvedValue({
        runId: 'run-1',
        status: 'failed',
        errorMessage: 'stop',
        finalShortlist: [],
      } satisfies RunResponse),
      getCandidateDetail: vi.fn(),
    };
    const { root, app } = setupApp(api);
    const button = root.querySelector<HTMLButtonElement>('#start-button');
    const jdInput = root.querySelector<HTMLTextAreaElement>('#jd-input');

    jdInput!.value = 'JD';
    jdInput!.dispatchEvent(new Event('input', { bubbles: true }));
    button!.click();

    await Promise.resolve();
    expect(api.createRun).toHaveBeenCalledWith({
      jdText: 'JD',
      sourcingPreferenceText: '',
    });

    app.destroy();
  });

  it('polls for shortlist and caches candidate detail', async () => {
    vi.useFakeTimers();
    const api: ApiClient = {
      createRun: vi.fn().mockResolvedValue({ runId: 'run-1', status: 'queued' }),
      getRun: vi
        .fn()
        .mockResolvedValueOnce({
          runId: 'run-1',
          status: 'running',
          errorMessage: null,
          finalShortlist: [],
        } satisfies RunResponse)
        .mockResolvedValueOnce(buildCompletedRun()),
      getCandidateDetail: vi.fn().mockResolvedValue(buildCandidateDetail()),
    };
    const { root, app } = setupApp(api);
    const button = root.querySelector<HTMLButtonElement>('#start-button');
    const jdInput = root.querySelector<HTMLTextAreaElement>('#jd-input');
    const notesInput = root.querySelector<HTMLTextAreaElement>('#notes-input');

    jdInput!.value = 'JD';
    jdInput!.dispatchEvent(new Event('input', { bubbles: true }));
    notesInput!.value = 'Notes';
    notesInput!.dispatchEvent(new Event('input', { bubbles: true }));
    button!.click();

    await Promise.resolve();
    expect(api.createRun).toHaveBeenCalledTimes(1);
    expect(api.getRun).toHaveBeenCalledTimes(1);
    expect(root.textContent).toContain('Agent 正在执行');

    await vi.advanceTimersByTimeAsync(100);
    expect(root.textContent).toContain('Lin Qian');
    expect(root.textContent).toContain('Top 5');

    const toggleButton = root.querySelector<HTMLElement>('[data-action="toggle-resume"]');
    toggleButton?.click();
    await Promise.resolve();
    expect(api.getCandidateDetail).toHaveBeenCalledTimes(1);
    expect(root.textContent).toContain('Resume ranking CLI');

    toggleButton?.click();
    toggleButton?.click();
    await Promise.resolve();
    expect(api.getCandidateDetail).toHaveBeenCalledTimes(1);

    app.destroy();
  });
});
