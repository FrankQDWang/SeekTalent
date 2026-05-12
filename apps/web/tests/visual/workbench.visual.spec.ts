import { expect, type Page, test, type TestInfo } from '@playwright/test';
import { compare } from 'odiff-bin';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASELINE_DIR = path.join(__dirname, 'baselines');
const SESSION_ID = 'session-visual';
const UPDATE_BASELINES = process.env.UPDATE_VISUAL_BASELINES === '1';

type Box = {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
};

const user = {
  userId: 'user-visual',
  email: 'visual@example.com',
  displayName: 'Visual Reviewer',
  role: 'admin',
  workspaceId: 'workspace-visual',
};

const triage = {
  sessionId: SESSION_ID,
  status: 'approved',
  mustHaves: ['AI platform product leadership', 'B2B SaaS growth', 'multi-source recruiting workflows'],
  niceToHaves: ['猎头业务理解', 'workflow automation', 'benchmark mindset'],
  synonyms: ['talent intelligence', 'candidate discovery', 'executive search'],
  seniorityFilters: ['director+', 'principal', 'founding product'],
  exclusions: ['pure consumer growth', 'junior IC only'],
  generatedQueryHints: ['AI recruiting agent', 'enterprise search PM', 'talent graph workflow'],
  createdAt: '2026-05-10T00:00:00Z',
  updatedAt: '2026-05-10T00:00:00Z',
  approvedAt: '2026-05-10T00:01:00Z',
};

const sourceCards = [
  {
    sourceRunId: 'src-cts-visual',
    sourceKind: 'cts',
    label: 'CTS',
    status: 'completed',
    authState: 'not_required',
    cardsScannedCount: 186,
    uniqueCandidatesCount: 18,
    detailOpenUsedCount: 0,
    detailOpenBlockedCount: 0,
    warningCode: null,
    warningMessage: null,
  },
  {
    sourceRunId: 'src-liepin-visual',
    sourceKind: 'liepin',
    label: 'Liepin',
    status: 'completed',
    authState: 'login_required',
    cardsScannedCount: 96,
    uniqueCandidatesCount: 12,
    detailOpenUsedCount: 3,
    detailOpenBlockedCount: 1,
    warningCode: null,
    warningMessage: null,
    connectionId: 'conn-liepin-visual',
    connectionStatus: 'connected',
    connectionWarningCode: null,
    connectionWarningMessage: null,
  },
];

const session = {
  sessionId: SESSION_ID,
  workspaceId: 'workspace-visual',
  ownerUserId: 'user-visual',
  jobTitle: 'AI Recruiting Platform VP',
  jdText: [
    'Own multi-source candidate discovery across CTS and Liepin.',
    'Separate must-haves from nice-to-haves before Boolean expansion.',
    'Use profile seeds to reverse-engineer shared attributes.',
    'Protect Liepin detail-open budget with sequential review.',
  ].join('\n'),
  notes: 'Internal executive search pilot / high-end roles / LAN workbench',
  status: 'draft',
  requirementTriage: triage,
  sourceRuns: sourceCards,
  sourceCards,
};

const events = [
  {
    globalSeq: 1,
    sessionSeq: 1,
    sessionId: SESSION_ID,
    sourceRunId: 'src-cts-visual',
    sourceKind: 'cts',
    eventName: 'requirements_approved',
    payload: { message: 'Requirement triage approved.' },
    createdAt: '2026-05-10T00:01:00Z',
  },
  {
    globalSeq: 2,
    sessionSeq: 2,
    sessionId: SESSION_ID,
    sourceRunId: 'src-cts-visual',
    sourceKind: 'cts',
    eventName: 'runtime_round_completed',
    payload: {
      type: 'round_completed',
      roundNo: 1,
      payload: {
        executed_queries: [{ query_terms: ['AI platform', 'multi-source recruiting'] }],
        raw_candidate_count: 18,
        unique_new_count: 6,
        newly_scored_count: 6,
        fit_count: 2,
        not_fit_count: 4,
        reflection_summary: '收窄到有多源招聘工作流经验的产品负责人。',
        reflection_rationale: '强候选人集中在 enterprise search 和 talent intelligence 产品线。',
        next_direction: '保留 AI platform，加入 recruiting workflow 和 executive search 关键词。',
      },
    },
    createdAt: '2026-05-10T00:02:00Z',
  },
  {
    globalSeq: 3,
    sessionSeq: 3,
    sessionId: SESSION_ID,
    sourceRunId: 'src-liepin-visual',
    sourceKind: 'liepin',
    eventName: 'liepin_card_search_completed',
    payload: { scanned: 96, candidates: 12 },
    createdAt: '2026-05-10T00:03:00Z',
  },
];

const candidate = {
  reviewItemId: 'review-liepin-visual',
  sessionId: SESSION_ID,
  status: 'promising',
  note: 'Strong operator profile, detail already approved.',
  displayName: 'Candidate A',
  title: 'VP Product, Talent Intelligence',
  company: 'Enterprise AI Platform',
  location: 'Shanghai',
  summary: 'Led recruiting workflow automation and enterprise search products.',
  aggregateScore: 92,
  fitBucket: 'fit',
  sourceBadges: ['CTS', 'Liepin'],
  evidenceLevel: 'detail',
  matchedMustHaves: ['AI platform product leadership', 'multi-source recruiting workflows'],
  matchedPreferences: ['猎头业务理解'],
  missingRisks: ['Compensation band needs confirmation'],
  strengths: ['Built search workflows', 'Understands recruiter triage'],
  weaknesses: ['Limited public benchmark evidence'],
  evidence: [
    {
      evidenceId: 'ev-liepin-visual',
      sourceRunId: 'src-liepin-visual',
      sourceKind: 'liepin',
      evidenceLevel: 'detail',
      score: 92,
      fitBucket: 'fit',
      matchedMustHaves: ['AI platform product leadership'],
      matchedPreferences: ['猎头业务理解'],
      missingRisks: ['Compensation band needs confirmation'],
      strengths: ['Built search workflows'],
      weaknesses: ['Limited public benchmark evidence'],
      createdAt: '2026-05-10T00:03:00Z',
    },
  ],
  createdAt: '2026-05-10T00:03:00Z',
  updatedAt: '2026-05-10T00:04:00Z',
};

async function mockWorkbenchApi(page: Page) {
  await page.addInitScript(() => {
    class MockEventSource extends EventTarget {
      readonly url: string;
      readyState = 1;

      constructor(url: string) {
        super();
        this.url = url;
        queueMicrotask(() => this.dispatchEvent(new Event('open')));
      }

      close() {
        this.readyState = 2;
      }
    }

    Object.defineProperty(window, 'EventSource', {
      configurable: true,
      writable: true,
      value: MockEventSource,
    });
  });

  await page.route('**/api/**', async (route) => {
    const requestUrl = new URL(route.request().url());
    const pathWithQuery = `${requestUrl.pathname}${requestUrl.search}`;
    const json = (payload: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'X-CSRF-Token': 'visual-csrf-token' },
        body: JSON.stringify(payload),
      });

    if (requestUrl.pathname === '/api/auth/me') {
      return json({ user });
    }
    if (requestUrl.pathname === '/api/workbench/sessions') {
      return json({ sessions: [session] });
    }
    if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}`) {
      return json(session);
    }
    if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/candidates`) {
      return json({ items: [candidate] });
    }
    if (requestUrl.pathname === '/api/workbench/events') {
      const afterSeq = Number(requestUrl.searchParams.get('after_seq') ?? '0');
      return json({ events: events.filter((event) => event.globalSeq > afterSeq) });
    }
    if (pathWithQuery === `/api/workbench/detail-open-requests?session_id=${SESSION_ID}`) {
      return json({
        requests: [
          {
            requestId: 'dor-visual',
            sessionId: SESSION_ID,
            reviewItemId: candidate.reviewItemId,
            status: 'approved',
            detailOpenMode: 'human_confirm',
            blockedReason: null,
            ledger: {
              ledgerId: 'dol-visual',
              status: 'leased',
              budgetDay: '2026-05-10',
              leaseExpiresAt: '2026-05-10T00:15:00Z',
            },
            providerAction: {
              actionKind: 'managed_browser',
              sourceKind: 'liepin',
              connectionId: 'conn-liepin-visual',
              reviewItemId: candidate.reviewItemId,
              budgetImpact: 'reserved',
              message: 'Detail view lease is reserved. Continue in the managed Liepin browser.',
            },
            createdAt: '2026-05-10T00:04:00Z',
            updatedAt: '2026-05-10T00:04:00Z',
          },
        ],
      });
    }
    if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/source-runs/liepin/policy`) {
      return json({
        sessionId: SESSION_ID,
        sourceKind: 'liepin',
        detailOpenMode: 'human_confirm',
        updatedAt: '2026-05-10T00:05:00Z',
      });
    }
    if (requestUrl.pathname === '/api/workbench/source-connections') {
      return json({
        connections: [
          {
            connectionId: 'conn-liepin-visual',
            sourceKind: 'liepin',
            label: 'Liepin',
            status: 'connected',
            warningCode: null,
            warningMessage: null,
            createdAt: '2026-05-10T00:00:00Z',
            updatedAt: '2026-05-10T00:01:00Z',
            connectedAt: '2026-05-10T00:01:00Z',
          },
        ],
      });
    }

    return json({ detail: `Unhandled visual mock route: ${pathWithQuery}` }, 404);
  });
}

async function openWorkbench(page: Page) {
  await page.goto(`/sessions/${SESSION_ID}`);
  await expect(page.getByTestId('active-session-title')).toHaveText(session.jobTitle);
  await expect(page.getByTestId('source-card-cts')).toBeVisible();
  await expect(page.getByTestId('source-card-liepin')).toBeVisible();
}

async function captureAndCompare(page: Page, testInfo: TestInfo, name: string, maxDiffPercentage: number) {
  await fs.mkdir(BASELINE_DIR, { recursive: true });
  const baselinePath = path.join(BASELINE_DIR, `${name}.png`);
  const actualPath = testInfo.outputPath(`${name}.png`);
  const diffPath = testInfo.outputPath(`${name}.diff.png`);

  await page.screenshot({ path: actualPath, fullPage: false });

  if (UPDATE_BASELINES) {
    await fs.copyFile(actualPath, baselinePath);
    return actualPath;
  }

  await expect(async () => {
    await fs.access(baselinePath);
  }, `Missing visual baseline ${baselinePath}; run UPDATE_VISUAL_BASELINES=1 bun --bun run test:visual`).toPass();

  const result = await compare(baselinePath, actualPath, diffPath, {
    antialiasing: true,
    threshold: 0.12,
  });

  if (result.match) {
    return actualPath;
  }
  expect(result.reason).toBe('pixel-diff');
  expect(result.diffPercentage).toBeLessThanOrEqual(maxDiffPercentage);
  return actualPath;
}

async function boxes(page: Page, selectors: Record<string, string>) {
  return page.evaluate((input) => {
    const result: Record<string, Box> = {};
    for (const [name, selector] of Object.entries(input)) {
      const element = document.querySelector(selector);
      if (!element) {
        throw new Error(`Missing selector ${selector}`);
      }
      const rect = element.getBoundingClientRect();
      result[name] = {
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      };
    }
    return result;
  }, selectors);
}

function overlaps(a: Box, b: Box) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
    bodyWidth: document.body.scrollWidth,
  }));
  expect(Math.max(overflow.documentWidth, overflow.bodyWidth)).toBeLessThanOrEqual(overflow.viewportWidth + 1);
}

async function expectNoVisibleControlTextOverflow(page: Page) {
  const overflowing = await page.evaluate(() => {
    return [...document.querySelectorAll('button, a, .source-badge, .status-pill, .rail-item span')]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      })
      .filter((element) => element.scrollWidth > element.clientWidth + 1)
      .map((element) => element.textContent?.trim() ?? element.className.toString());
  });
  expect(overflowing).toEqual([]);
}

test.describe('workbench visual smoke', () => {
  test.beforeEach(async ({ page }) => {
    await page.clock.install({ time: new Date('2026-05-10T08:00:00Z') });
    await mockWorkbenchApi(page);
  });

  test('desktop workbench renders the interactive strategy graph and node details', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await openWorkbench(page);

    const desktop = await boxes(page, {
      topbar: '.topbar',
      sessionRail: '.session-rail',
      workbenchMain: '.workbench-main',
      jdPanel: '.jd-panel',
      strategyPanel: '.strategy-panel',
      rightRail: '.right-rail',
      strategyFlow: '.strategy-flow',
      rightTabs: '.right-workbench-tabs',
    });

    expect(desktop.sessionRail.right).toBeLessThanOrEqual(desktop.workbenchMain.left + 1);
    expect(desktop.jdPanel.right).toBeLessThanOrEqual(desktop.strategyPanel.left + 1);
    expect(desktop.strategyPanel.right).toBeLessThanOrEqual(desktop.rightRail.left + 1);
    expect(desktop.topbar.bottom).toBeLessThanOrEqual(desktop.workbenchMain.top + 1);
    expect(desktop.strategyFlow.width).toBeGreaterThan(600);
    expect(desktop.rightTabs.top).toBeGreaterThanOrEqual(desktop.rightRail.top);
    expect(overlaps(desktop.jdPanel, desktop.strategyPanel)).toBe(false);
    expect(overlaps(desktop.strategyPanel, desktop.rightRail)).toBe(false);

    await expect(page.getByTestId('strategy-flow')).toBeVisible();
    await expect(page.getByRole('tab')).toHaveCount(2);
    await expect(page.getByRole('tab', { name: '候选人队列' })).toHaveCount(0);
    await expect(page.getByRole('tab', { name: '运行笔记' })).toHaveAttribute('aria-selected', 'true');
    const reflectionNode = page.getByRole('button', { name: /第 1 轮反思/ });
    await expect(reflectionNode).toBeVisible();
    await reflectionNode.click();
    await expect(page.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('强候选人集中在 enterprise search 和 talent intelligence 产品线。')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectNoVisibleControlTextOverflow(page);
    await captureAndCompare(page, testInfo, 'desktop-graph-detail', 0.35);
  });

  test('1024px workbench keeps graph and node detail reachable', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1024, height: 900 });
    await openWorkbench(page);

    const tablet = await boxes(page, {
      topbar: '.topbar',
      sessionRail: '.session-rail',
      workbenchMain: '.workbench-main',
      jdPanel: '.jd-panel',
      strategyPanel: '.strategy-panel',
      rightRail: '.right-rail',
    });

    expect(tablet.topbar.bottom).toBeLessThanOrEqual(tablet.workbenchMain.top + 1);
    expect(tablet.jdPanel.right).toBeLessThanOrEqual(tablet.strategyPanel.left + 1);
    expect(tablet.rightRail.top).toBeGreaterThanOrEqual(tablet.strategyPanel.bottom - 1);
    await expect(page.getByTestId('strategy-flow')).toBeVisible();
    await page.getByRole('button', { name: /第 1 轮反思/ }).click();
    await page.locator('.right-workbench-tabs').scrollIntoViewIfNeeded();
    await expect(page.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('保留 AI platform，加入 recruiting workflow 和 executive search 关键词。')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectNoVisibleControlTextOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await captureAndCompare(page, testInfo, 'tablet-1024-node-detail', 0.35);
  });
});
