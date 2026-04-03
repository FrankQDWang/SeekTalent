import type { ApiClient } from './api';
import type {
  AgentShortlistCandidate,
  CandidateDetailResponse,
  DetailLoadState,
  ResumeEducationItem,
  ResumeProjection,
  ResumeWorkExperienceItem,
  RunStatus,
} from './types';

type ShortlistRow = {
  rank: number;
  candidate: AgentShortlistCandidate;
};

type AppState = {
  jdText: string;
  sourcingPreferenceText: string;
  runId: string;
  status: RunStatus | 'idle';
  errorMessage: string;
  isStarting: boolean;
  shortlist: ShortlistRow[];
  expandedCandidateId: string | null;
  detailCache: Record<string, DetailLoadState>;
};

type AppOptions = {
  pollIntervalMs?: number;
};

const EMPTY_DETAIL_STATE: DetailLoadState = { status: 'idle', detail: null, error: '' };

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function candidateMeta(candidate: AgentShortlistCandidate): string {
  return [candidate.title, candidate.company, candidate.location].filter(Boolean).join(' · ');
}

function formatScore(candidate: AgentShortlistCandidate): string {
  return `${String(Math.round(candidate.score * 100))}%`;
}

function fallbackText(value: string | number | null | undefined): string {
  if (value === null || value === undefined) {
    return '未提供';
  }
  const normalized = String(value).trim();
  return normalized.length > 0 ? normalized : '未提供';
}

function workExperienceMeta(item: ResumeWorkExperienceItem): string {
  if (item.duration?.trim()) {
    return item.duration.trim();
  }
  const start = item.startTime?.trim() || '未提供';
  const end = item.endTime?.trim() || '至今';
  return `${start} - ${end}`;
}

function resumeOverview(projection: ResumeProjection): Array<{ label: string; value: string }> {
  return [
    { label: '工作年限', value: fallbackText(projection.workYear) },
    { label: '当前地点', value: fallbackText(projection.currentLocation) },
    { label: '期望地点', value: fallbackText(projection.expectedLocation) },
    { label: '求职状态', value: fallbackText(projection.jobState) },
    { label: '期望薪资', value: fallbackText(projection.expectedSalary) },
    { label: '年龄', value: fallbackText(projection.age) },
  ];
}

function educationMeta(item: ResumeEducationItem): string {
  const start = item.startTime?.trim();
  const end = item.endTime?.trim();
  if (start || end) {
    return `${start || '未提供'} - ${end || '至今'}`;
  }
  return item.major.trim() || '未提供';
}

function renderResumeDetail(detailState: DetailLoadState, candidateId: string): string {
  if (detailState.status === 'loading' || detailState.status === 'idle') {
    return '<div class="resume-panel"><p class="detail-loading">正在加载完整简历…</p></div>';
  }

  if (detailState.status === 'error') {
    return `
      <div class="resume-panel">
        <div class="resume-state">
          <p class="detail-error">${escapeHtml(detailState.error)}</p>
          <button class="resume-retry" type="button" data-action="retry-detail" data-candidate-id="${escapeHtml(candidateId)}">重新加载简历</button>
        </div>
      </div>
    `;
  }

  const projection = detailState.detail.resumeView.projection;
  const overviewHtml = resumeOverview(projection)
    .map(
      (item) => `
        <div class="resume-overview-item">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
        </div>
      `,
    )
    .join('');

  const workExperienceHtml =
    projection.workExperience.length > 0
      ? projection.workExperience
          .map(
            (item) => `
              <article class="resume-item">
                <div class="resume-item-head">
                  <strong>${escapeHtml(fallbackText(item.title))}</strong>
                  <span>${escapeHtml(fallbackText(item.company))}</span>
                </div>
                <p class="resume-meta">${escapeHtml(workExperienceMeta(item))}</p>
                <p class="detail-text compact">${escapeHtml(fallbackText(item.summary))}</p>
              </article>
            `,
          )
          .join('')
      : '<p class="resume-empty">未提供</p>';

  const educationHtml =
    projection.education.length > 0
      ? projection.education
          .map(
            (item) => `
              <article class="resume-item">
                <div class="resume-item-head">
                  <strong>${escapeHtml(fallbackText(item.school))}</strong>
                  <span>${escapeHtml(fallbackText(item.degree))}</span>
                </div>
                <p class="resume-meta">${escapeHtml(`${fallbackText(item.major)} · ${educationMeta(item)}`)}</p>
              </article>
            `,
          )
          .join('')
      : '<p class="resume-empty">未提供</p>';

  const workSummariesHtml =
    projection.workSummaries.length > 0
      ? `<ul class="detail-bullets">${projection.workSummaries
          .map((item) => `<li>${escapeHtml(fallbackText(item))}</li>`)
          .join('')}</ul>`
      : '<p class="resume-empty">未提供</p>';

  const projectNamesHtml =
    projection.projectNames.length > 0
      ? `<div class="resume-tags">${projection.projectNames
          .map((item) => `<span class="resume-tag">${escapeHtml(fallbackText(item))}</span>`)
          .join('')}</div>`
      : '<p class="resume-empty">未提供</p>';

  return `
    <div class="resume-panel">
      <section class="resume-section">
        <p class="detail-label">基础概览</p>
        <div class="resume-overview-grid">${overviewHtml}</div>
      </section>

      <section class="resume-section">
        <p class="detail-label">工作经历</p>
        <div class="resume-list">${workExperienceHtml}</div>
      </section>

      <section class="resume-section">
        <p class="detail-label">教育经历</p>
        <div class="resume-list">${educationHtml}</div>
      </section>

      <section class="resume-section">
        <p class="detail-label">工作摘要</p>
        ${workSummariesHtml}
      </section>

      <section class="resume-section">
        <p class="detail-label">项目名称</p>
        ${projectNamesHtml}
      </section>
    </div>
  `;
}

function renderCandidateCard(row: ShortlistRow, detailState: DetailLoadState, expanded: boolean): string {
  const candidate = row.candidate;
  return `
    <article class="candidate-card">
      <div class="result-summary">
        <span class="rank">${row.rank < 10 ? `0${String(row.rank)}` : String(row.rank)}</span>
        <div class="summary-copy">
          <strong>${escapeHtml(candidate.name)}</strong>
          <p>${escapeHtml(candidateMeta(candidate))}</p>
        </div>
        <span class="score">${escapeHtml(formatScore(candidate))}</span>
      </div>

      <div class="result-detail">
        <p class="detail-label">推荐原因</p>
        <p class="detail-text">${escapeHtml(candidate.reason)}</p>
      </div>

      <div class="resume-shell">
        <button
          class="resume-toggle"
          type="button"
          data-action="toggle-resume"
          data-candidate-id="${escapeHtml(candidate.candidateId)}"
          aria-expanded="${expanded ? 'true' : 'false'}"
        >
          <span class="resume-toggle-title">完整简历</span>
          <span class="resume-toggle-action">${expanded ? '收起简历' : '查看完整简历'}</span>
          <span class="resume-toggle-icon" aria-hidden="true">${expanded ? '-' : '+'}</span>
        </button>
        ${expanded ? renderResumeDetail(detailState, candidate.candidateId) : ''}
      </div>
    </article>
  `;
}

function buildStatusLine(state: AppState): string {
  if (state.isStarting || state.status === 'queued' || state.status === 'running') {
    return 'Agent 正在执行';
  }
  if (state.status === 'completed') {
    return state.shortlist.length > 0 ? `已完成 · 返回 ${String(state.shortlist.length)} 份简历` : '已完成 · 没有找到候选人';
  }
  return '';
}

function buildPlaceholder(state: AppState): string {
  if (state.errorMessage) {
    return escapeHtml(state.errorMessage);
  }
  const statusLine = buildStatusLine(state);
  if (statusLine) {
    return escapeHtml(statusLine);
  }
  return '结果会出现在这里';
}

function createShell(root: HTMLElement): {
  jdInput: HTMLTextAreaElement;
  notesInput: HTMLTextAreaElement;
  startButton: HTMLButtonElement;
  statusLine: HTMLParagraphElement;
  errorLine: HTMLParagraphElement;
  resultsBody: HTMLDivElement;
  resultsTopLabel: HTMLSpanElement;
} {
  root.innerHTML = `
    <main class="layout">
      <section class="workspace">
        <section class="intake-panel">
          <p class="section-label">输入</p>

          <label class="field">
            <span>JD</span>
            <textarea id="jd-input" rows="10" placeholder="粘贴职位描述"></textarea>
          </label>

          <label class="field">
            <span>寻访偏好</span>
            <textarea id="notes-input" rows="6" placeholder="粘贴寻访须知"></textarea>
          </label>

          <div class="actions">
            <button id="start-button" class="primary-action" type="button">开始运行</button>
            <p id="status-line" class="inline-state"></p>
            <p id="error-line" class="inline-state error"></p>
          </div>
        </section>

        <section class="results">
          <header class="results-head">
            <p class="section-label">结果</p>
            <span id="results-top-label" hidden>Top 5</span>
          </header>
          <div id="results-body" class="results-body"></div>
        </section>
      </section>
    </main>
  `;

  return {
    jdInput: root.querySelector('#jd-input') as HTMLTextAreaElement,
    notesInput: root.querySelector('#notes-input') as HTMLTextAreaElement,
    startButton: root.querySelector('#start-button') as HTMLButtonElement,
    statusLine: root.querySelector('#status-line') as HTMLParagraphElement,
    errorLine: root.querySelector('#error-line') as HTMLParagraphElement,
    resultsBody: root.querySelector('#results-body') as HTMLDivElement,
    resultsTopLabel: root.querySelector('#results-top-label') as HTMLSpanElement,
  };
}

export function createApp(root: HTMLElement, api: ApiClient, options: AppOptions = {}) {
  const pollIntervalMs = options.pollIntervalMs ?? 2500;
  const refs = createShell(root);
  const state: AppState = {
    jdText: '',
    sourcingPreferenceText: '',
    runId: '',
    status: 'idle',
    errorMessage: '',
    isStarting: false,
    shortlist: [],
    expandedCandidateId: null,
    detailCache: {},
  };

  let pollTimer: number | null = null;
  let destroyed = false;

  const canStart = () =>
    !state.isStarting &&
    state.status !== 'queued' &&
    state.status !== 'running' &&
    state.jdText.trim().length > 0;

  const clearPoll = () => {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  };

  const render = () => {
    refs.jdInput.value = state.jdText;
    refs.notesInput.value = state.sourcingPreferenceText;
    refs.jdInput.disabled = state.isStarting || state.status === 'queued' || state.status === 'running';
    refs.notesInput.disabled = refs.jdInput.disabled;
    refs.startButton.disabled = !canStart();
    refs.startButton.textContent = state.isStarting || state.status === 'queued' || state.status === 'running' ? '运行中…' : '开始运行';

    refs.statusLine.textContent = state.errorMessage ? '' : buildStatusLine(state);
    refs.errorLine.textContent = state.errorMessage;
    refs.errorLine.hidden = state.errorMessage.length === 0;

    refs.resultsTopLabel.hidden = state.shortlist.length === 0;
    if (state.shortlist.length === 0) {
      refs.resultsBody.innerHTML = `<div class="placeholder"><p>${buildPlaceholder(state)}</p></div>`;
      return;
    }

    refs.resultsBody.innerHTML = state.shortlist
      .map((row) =>
        renderCandidateCard(
          row,
          state.detailCache[row.candidate.candidateId] ?? EMPTY_DETAIL_STATE,
          state.expandedCandidateId === row.candidate.candidateId,
        ),
      )
      .join('');
  };

  const syncRun = async () => {
    if (!state.runId || destroyed) {
      return;
    }

    try {
      const response = await api.getRun(state.runId);
      state.status = response.status;
      state.errorMessage = response.errorMessage ?? '';
      if (response.status === 'completed') {
        clearPoll();
        state.shortlist = response.finalShortlist.map((candidate, index) => ({ rank: index + 1, candidate }));
        render();
        return;
      }
      if (response.status === 'failed') {
        clearPoll();
        render();
        return;
      }
      render();
      clearPoll();
      pollTimer = window.setTimeout(() => {
        void syncRun();
      }, pollIntervalMs);
    } catch (error) {
      clearPoll();
      state.status = 'failed';
      state.errorMessage = error instanceof Error ? error.message : '请求失败，请稍后重试。';
      render();
    }
  };

  const loadDetail = async (candidateId: string, force = false) => {
    if (!state.runId) {
      return;
    }
    const cached = state.detailCache[candidateId];
    if (!force && (cached?.status === 'loading' || cached?.status === 'loaded')) {
      return;
    }
    state.detailCache[candidateId] = { status: 'loading', detail: null, error: '' };
    render();
    try {
      const detail = await api.getCandidateDetail(state.runId, candidateId);
      state.detailCache[candidateId] = { status: 'loaded', detail, error: '' };
    } catch (error) {
      state.detailCache[candidateId] = {
        status: 'error',
        detail: null,
        error: error instanceof Error ? error.message : '加载简历失败，请稍后重试。',
      };
    }
    render();
  };

  const startRun = async () => {
    if (!canStart()) {
      return;
    }
    clearPoll();
    state.runId = '';
    state.status = 'idle';
    state.errorMessage = '';
    state.shortlist = [];
    state.expandedCandidateId = null;
    state.detailCache = {};
    state.isStarting = true;
    render();

    try {
      const response = await api.createRun({
        jdText: state.jdText.trim(),
        sourcingPreferenceText: state.sourcingPreferenceText.trim(),
      });
      state.runId = response.runId;
      state.status = response.status;
      state.isStarting = false;
      render();
      await syncRun();
    } catch (error) {
      clearPoll();
      state.isStarting = false;
      state.status = 'failed';
      state.errorMessage = error instanceof Error ? error.message : '启动运行失败，请稍后重试。';
      render();
    }
  };

  refs.jdInput.addEventListener('input', () => {
    state.jdText = refs.jdInput.value;
    render();
  });
  refs.notesInput.addEventListener('input', () => {
    state.sourcingPreferenceText = refs.notesInput.value;
    render();
  });
  refs.startButton.addEventListener('click', () => {
    void startRun();
  });
  refs.resultsBody.addEventListener('click', (event) => {
    const target = event.target as HTMLElement | null;
    if (!target) {
      return;
    }
    const toggleButton = target.closest<HTMLElement>('[data-action="toggle-resume"]');
    if (toggleButton) {
      const candidateId = toggleButton.dataset.candidateId;
      if (!candidateId) {
        return;
      }
      if (state.expandedCandidateId === candidateId) {
        state.expandedCandidateId = null;
        render();
        return;
      }
      state.expandedCandidateId = candidateId;
      render();
      void loadDetail(candidateId);
      return;
    }

    const retryButton = target.closest<HTMLElement>('[data-action="retry-detail"]');
    if (retryButton) {
      const candidateId = retryButton.dataset.candidateId;
      if (!candidateId) {
        return;
      }
      void loadDetail(candidateId, true);
    }
  });

  render();

  return {
    destroy() {
      destroyed = true;
      clearPoll();
    },
    getState() {
      return structuredClone(state) as AppState;
    },
  };
}
