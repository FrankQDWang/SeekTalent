# Interactive Strategy Graph Implementation Plan

<!-- /autoplan restore point: /Users/frankqdwang/.gstack/projects/FrankQDWang-SeekTalent/main-autoplan-restore-20260511-115534.md -->

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the recruiter workbench strategy graph into an interactive React Flow + ELK graph with complete business-node details, source-aware layout, and right-side node inspection.

**Architecture:** Keep Python Workbench APIs unchanged. `buildRunStory()` remains the pure business graph derivation layer, but it now accepts safe candidate-review and detail-open API data in addition to session/events. ELK handles per-lane left-to-right layout, the adapter stacks lanes vertically, and React Flow renders clickable nodes with a right-lower `候选人队列` / `节点详情` inspector.

**Tech Stack:** Bun, Vite, React 19, TypeScript, TanStack Router/Query, `@xyflow/react`, `elkjs`, Vitest, Testing Library, Playwright visual tests, `odiff-bin`, FastAPI Workbench API regression tests.

---

## File Structure

- Modify: `apps/web/package.json`
  - Add `@xyflow/react` and `elkjs`.
- Modify: `apps/web/bun.lock`
  - Updated by `bun add @xyflow/react elkjs`.
- Modify: `apps/web/vite.config.ts`
  - Add `test.setupFiles`.
- Create: `apps/web/src/setupTests.ts`
  - Provide React Flow DOM measurement mocks for Vitest/jsdom.
- Modify: `apps/web/src/recruiterAnimation.ts`
  - Add graph detail kind/payload types, evidence refs, and `relatedNodeId`.
- Modify: `apps/web/src/runStory.ts`
  - Change `buildRunStory()` to accept one object input.
  - Populate complete detail payloads from session, events, candidate review items, and detail-open requests.
- Modify: `apps/web/src/runStory.test.ts`
  - Cover detail contract, safe inputs, source filtering, and related ids.
- Create: `apps/web/src/strategyGraphLayout.ts`
  - Convert business graph to ELK input, run per-lane layout, stack source lanes, and provide deterministic fallback.
- Create: `apps/web/src/strategyGraphLayout.test.ts`
  - Cover lane stacking, aggregation placement, and fallback placement.
- Create: `apps/web/src/StrategyGraph.tsx`
  - React Flow canvas, custom node renderer, hidden handles, selected state, and stable nodes/edges lifecycle.
- Create: `apps/web/src/NodeDetailPanel.tsx`
  - Business inspector for all detail kinds.
- Modify: `apps/web/src/app.tsx`
  - Wire candidate/detail queries into `buildRunStory()`.
  - Replace hand-rendered graph with `StrategyGraph`.
  - Add selected-node state, source-filter clearing, and right-lower tabs.
  - Add running-note and candidate-evidence node selection.
- Modify: `apps/web/src/app.test.tsx`
  - Cover React Flow interaction, inspector tabs, source filter clearing, running-note linking, candidate evidence linking, and central start behavior.
- Modify: `apps/web/src/styles.css`
  - Import React Flow CSS and override React Flow classes to preserve the warm-paper workbench style.
- Modify: `apps/web/tests/visual/workbench.visual.spec.ts`
  - Replace deleted playback selectors with current workbench graph, inspector, source-filter, and 1024px visual coverage.
- Modify: `docs/ui.md`
  - Document the interactive strategy graph.
- Modify: `docs/superpowers/2026-05-09-multi-source-workbench-execution.md`
  - Record final verification evidence.

## Task 0: Install Graph Dependencies And Test Setup

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/bun.lock`
- Modify: `apps/web/vite.config.ts`
- Create: `apps/web/src/setupTests.ts`

- [ ] **Step 1: Add graph dependencies with Bun**

Run:

```bash
cd apps/web && bun add @xyflow/react elkjs
```

Expected:

- `apps/web/package.json` includes `@xyflow/react` and `elkjs` under `dependencies`.
- `apps/web/bun.lock` changes.

- [ ] **Step 2: Add Vitest setup file**

Modify `apps/web/vite.config.ts`:

```ts
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/setupTests.ts'],
  },
});
```

Create `apps/web/src/setupTests.ts`:

```ts
import '@testing-library/jest-dom/vitest';

class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserver as unknown as typeof globalThis.ResizeObserver;
globalThis.DOMMatrixReadOnly = class DOMMatrixReadOnly {
  m22 = 1;
} as unknown as typeof globalThis.DOMMatrixReadOnly;

Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: { get() { return 100; } },
  offsetWidth: { get() { return 180; } },
});

SVGElement.prototype.getBBox = () =>
  ({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  }) as DOMRect;
```

- [ ] **Step 3: Verify baseline frontend tests still run**

Run:

```bash
cd apps/web && bun run test src/runStory.test.ts
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/web/package.json apps/web/bun.lock apps/web/vite.config.ts apps/web/src/setupTests.ts
git commit -m "chore: add strategy graph dependencies"
```

## Task 1: Complete Graph Detail Contract

**Files:**
- Modify: `apps/web/src/recruiterAnimation.ts`
- Modify: `apps/web/src/runStory.ts`
- Modify: `apps/web/src/runStory.test.ts`

- [ ] **Step 1: Write failing tests for complete business detail payloads**

Add these tests to `apps/web/src/runStory.test.ts` inside `describe('buildRunStory', ...)`:

```ts
it('uses confirmed triage before runtime criteria in the requirements node', () => {
  const story = buildRunStory({
    session: session({
      requirementTriage: triage({
        status: 'approved',
        mustHaves: ['Flink CDC'],
        niceToHaves: ['Kafka'],
        generatedQueryHints: ['streaming platform'],
      }),
    }),
    events,
    sourceFilter: 'all',
  });

  const node = story.graphNodes.find((item) => item.id === 'requirements');
  expect(node?.detailKind).toBe('requirements');
  expect(node?.detailPayload).toEqual({
    triageStatus: 'confirmed',
    mustHaves: ['Flink CDC'],
    niceToHaves: ['Kafka'],
    queryHints: ['streaming platform'],
  });
});

it('does not label draft triage as confirmed', () => {
  const story = buildRunStory({
    session: session({
      requirementTriage: triage({
        status: 'draft',
        mustHaves: ['Flink CDC'],
      }),
    }),
    events,
    sourceFilter: 'all',
  });

  const node = story.graphNodes.find((item) => item.id === 'requirements');
  expect(node?.detailKind).toBe('requirements');
  expect(node?.detailPayload).toEqual({
    triageStatus: 'draft',
    mustHaves: ['Flink CDC'],
    niceToHaves: [],
    queryHints: [],
  });
});

it('attaches rationale and next direction to reflection nodes', () => {
  const story = buildRunStory({
    session: session(),
    events: [
      event({
        globalSeq: 2,
        sourceKind: 'cts',
        sourceRunId: 'src-cts',
        eventName: 'runtime_round_completed',
        payload: {
          roundNo: 1,
          payload: {
            executed_queries: [{ query_terms: ['Flink CDC', 'Kafka'] }],
            raw_candidate_count: 14,
            unique_new_count: 9,
            newly_scored_count: 9,
            fit_count: 1,
            not_fit_count: 8,
            reflection_summary: '需要放宽 Kafka 关键词。',
            reflection_rationale: '强候选人常把 Kafka 写在项目描述里。',
            next_direction: '加入实时数仓和 CDC 同义词。',
          },
        },
      }),
    ],
    sourceFilter: 'cts',
  });

  const node = story.graphNodes.find((item) => item.id === 'cts-round-1-reflect');
  expect(node?.detailKind).toBe('reflection');
  expect(node?.detailPayload).toEqual({
    roundNo: 1,
    summary: '需要放宽 Kafka 关键词。',
    rationale: '强候选人常把 Kafka 写在项目描述里。',
    nextDirection: '加入实时数仓和 CDC 同义词。',
  });
});

it('uses safe candidate and detail-open API data for Liepin detail nodes', () => {
  const story = buildRunStory({
    session: session(),
    events,
    candidateReviewItems: [
      {
        reviewItemId: 'review-liepin-1',
        sessionId: 'session-1',
        status: 'new',
        note: '',
        displayName: '候选人 A',
        title: '增长负责人',
        company: '某消费品牌',
        location: '上海',
        summary: 'DTC 增长经验',
        aggregateScore: 91,
        fitBucket: 'fit',
        sourceBadges: ['Liepin'],
        evidenceLevel: 'card',
        matchedMustHaves: ['DTC'],
        matchedPreferences: [],
        missingRisks: [],
        strengths: [],
        weaknesses: [],
        evidence: [
          {
            evidenceId: 'evidence-liepin-1',
            sourceRunId: 'src-liepin',
            sourceKind: 'liepin',
            evidenceLevel: 'card',
            score: 91,
            fitBucket: 'fit',
            matchedMustHaves: ['DTC'],
            matchedPreferences: [],
            missingRisks: [],
            strengths: [],
            weaknesses: [],
            createdAt: '2026-05-09T00:00:04Z',
          },
        ],
        createdAt: '2026-05-09T00:00:04Z',
        updatedAt: '2026-05-09T00:00:04Z',
      },
    ],
    detailOpenRequests: [
      {
        requestId: 'detail-req-1',
        sessionId: 'session-1',
        reviewItemId: 'review-liepin-1',
        status: 'pending',
        detailOpenMode: 'human_confirm',
        decisionNote: null,
        candidate: null,
        blockedReason: null,
        ledger: null,
        providerAction: null,
        createdAt: '2026-05-09T00:00:05Z',
        updatedAt: '2026-05-09T00:00:05Z',
      },
    ],
    sourceFilter: 'liepin',
  });

  const candidateNode = story.graphNodes.find((item) => item.id === 'liepin-card-candidates');
  expect(candidateNode?.candidateReviewItemIds).toEqual(['review-liepin-1']);
  expect(candidateNode?.candidateEvidenceRefs).toEqual([
    {
      evidenceId: 'evidence-liepin-1',
      reviewItemId: 'review-liepin-1',
      sourceRunId: 'src-liepin',
      sourceKind: 'liepin',
      evidenceLevel: 'card',
    },
  ]);
  expect(candidateNode?.detailPayload).toEqual({ candidateCount: 1, bestScore: 91 });

  const detailNode = story.graphNodes.find((item) => item.id === 'liepin-detail-approval');
  expect(detailNode?.detailKind).toBe('liepinDetailApproval');
  expect(detailNode?.detailOpenRequestIds).toEqual(['detail-req-1']);
  expect(detailNode?.detailPayload).toEqual({
    requestCount: 1,
    leasedCount: 0,
    blockedCount: 0,
    requestIds: ['detail-req-1'],
    requestSummaries: ['detail-req-1'],
    budgetText: '批准后占用 1 次详情额度',
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd apps/web && bun run test src/runStory.test.ts
```

Expected: FAIL because `buildRunStory()` still has the old positional signature and graph nodes do not have complete detail metadata.

- [ ] **Step 3: Extend graph and log types**

Modify `apps/web/src/recruiterAnimation.ts`:

```ts
import type { SourceKind } from './types';

export type RecruiterTone = 'blue' | 'teal' | 'violet' | 'amber' | 'green' | 'neutral' | 'rose';
export type RecruiterLane = 'shared' | SourceKind;

export type RecruiterGraphDetailKind =
  | 'job'
  | 'requirements'
  | 'sourceQueue'
  | 'ctsRoundQuery'
  | 'ctsRoundResults'
  | 'ctsRoundScoring'
  | 'reflection'
  | 'liepinCardSearch'
  | 'liepinCardCandidates'
  | 'liepinDetailApproval'
  | 'aggregation';

export type RecruiterCandidateEvidenceRef = {
  evidenceId: string;
  reviewItemId: string;
  sourceRunId: string;
  sourceKind: SourceKind;
  evidenceLevel: string;
};

export type RecruiterGraphDetailPayload =
  | { jobTitle: string; sourceMode: 'single' | 'multi'; jdPreview: string }
  | { triageStatus: 'confirmed' | 'draft' | 'runtime'; mustHaves: string[]; niceToHaves: string[]; queryHints: string[] }
  | { status: string; authState: string; scannedCount: number; uniqueCandidatesCount: number; warningMessage: string | null }
  | { roundNo: number; queryLabel: string; queryTerms: string[] }
  | { roundNo: number; rawCandidateCount: number; uniqueNewCount: number }
  | { roundNo: number; newlyScoredCount: number; fitCount: number; notFitCount: number }
  | { roundNo: number; summary: string; rationale?: string; nextDirection?: string }
  | { scannedCount: number; uniqueCandidatesCount: number }
  | { candidateCount: number; bestScore: number | null }
  | { requestCount: number; leasedCount: number; blockedCount: number; requestIds: string[]; requestSummaries: string[]; budgetText: string | null };

export type RecruiterGraphNode = {
  id: string;
  at: number;
  kind: '岗位' | '拆解' | '检索' | '命中' | '过滤' | '反思' | '灵光' | '排序';
  label: string;
  detail: string;
  x: number;
  y: number;
  tone: RecruiterTone;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
  detailKind?: RecruiterGraphDetailKind;
  detailPayload?: RecruiterGraphDetailPayload;
  eventIds?: number[];
  sourceRunId?: string | null;
  candidateReviewItemIds?: string[];
  candidateEvidenceRefs?: RecruiterCandidateEvidenceRef[];
  detailOpenRequestIds?: string[];
};

export type RecruiterGraphEdge = {
  from: string;
  to: string;
  tone: RecruiterTone;
  label?: string;
};

export type RecruiterLogEntry = {
  id: string;
  at: number;
  tag: 'SYS' | 'THINK' | 'PLAN' | 'SCAN' | 'HIT' | 'REFLECT' | 'AHA';
  text: string;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
  relatedNodeId?: string;
};
```

- [ ] **Step 4: Change `buildRunStory()` to a single object input**

Modify imports in `apps/web/src/runStory.ts`:

```ts
import type { RecruiterCandidateEvidenceRef } from './recruiterAnimation';
import type {
  SourceKind,
  WorkbenchCandidateReviewItem,
  WorkbenchDetailOpenRequest,
  WorkbenchEvent,
  WorkbenchRequirementTriage,
  WorkbenchRequirementTriageInput,
  WorkbenchSession,
} from './types';
```

Replace `BuildRunStoryOptions` and the function signature:

```ts
export type SourceFilter = SourceKind | 'all';

export type BuildRunStoryInput = {
  session: WorkbenchSession;
  events: WorkbenchEvent[];
  candidateReviewItems?: WorkbenchCandidateReviewItem[];
  detailOpenRequests?: WorkbenchDetailOpenRequest[];
  sourceFilter?: SourceFilter;
};

export function buildRunStory({
  session,
  events,
  candidateReviewItems = [],
  detailOpenRequests = [],
  sourceFilter = 'all',
}: BuildRunStoryInput): RunStory {
  const scopedEvents = scopeEvents(events, sourceFilter);
  const scopedCandidateItems = scopeCandidateReviewItems(candidateReviewItems, sourceFilter);
  const scopedDetailOpenRequests = scopeDetailOpenRequests(detailOpenRequests, scopedCandidateItems);
  const allRuntimeEvents = events
    .filter((event) => event.eventName.startsWith('runtime_'))
    .map(runtimeEventData)
    .filter(Boolean) as RuntimeEventData[];
```

Update direct `buildRunStory()` calls in `apps/web/src/runStory.test.ts` from:

```ts
buildRunStory(session(), events, { sourceFilter: 'all' })
```

to:

```ts
buildRunStory({ session: session(), events, sourceFilter: 'all' })
```

Update the temporary app call sites in `apps/web/src/app.tsx` without adding Task 2 variables yet:

```ts
const sessionStory = useMemo(
  () => buildRunStory({ session, events: sessionEvents, sourceFilter: 'all' }),
  [session, sessionEvents],
);
const visibleStory = useMemo(
  () => buildRunStory({ session, events: sessionEvents, sourceFilter }),
  [session, sessionEvents, sourceFilter],
);
```

- [ ] **Step 5: Populate complete payloads**

Set the job node fields:

```ts
detailKind: 'job',
detailPayload: {
  jobTitle: session.jobTitle,
  sourceMode: session.sourceCards.length > 1 ? 'multi' : 'single',
  jdPreview: clip(session.jdText, 180),
},
eventIds: [],
sourceRunId: null,
candidateReviewItemIds: [],
candidateEvidenceRefs: [],
detailOpenRequestIds: [],
```

Set the requirements node fields:

```ts
const triageCriteria = criteriaFromTriage(session.requirementTriage);
const runtimeCriteria = criteriaFromRequirements(requirements);
const hasDraftOrConfirmedTriage = hasTriageInput(triageCriteria);
const criteria = hasDraftOrConfirmedTriage ? triageCriteria : runtimeCriteria;
const triageStatus = hasDraftOrConfirmedTriage
  ? session.requirementTriage.status === 'approved'
    ? 'confirmed'
    : 'draft'
  : 'runtime';
```

```ts
detailKind: 'requirements',
detailPayload: {
  triageStatus,
  mustHaves: criteria.mustHaves,
  niceToHaves: criteria.niceToHaves,
  queryHints: criteria.generatedQueryHints,
},
eventIds: requirements ? [requirements.event.globalSeq] : [],
sourceRunId: null,
candidateReviewItemIds: [],
candidateEvidenceRefs: [],
detailOpenRequestIds: [],
```

Set source queue node fields in each source lane:

```ts
detailKind: 'sourceQueue',
detailPayload: {
  status: sourceCard?.status ?? 'queued',
  authState: sourceCard?.authState ?? 'not_required',
  scannedCount: sourceCard?.cardsScannedCount ?? 0,
  uniqueCandidatesCount: sourceCard?.uniqueCandidatesCount ?? 0,
  warningMessage: displaySafeWarning(sourceCard?.warningCode ?? null, sourceCard?.warningMessage ?? null),
},
eventIds: started ? [started.globalSeq] : [],
sourceRunId: sourceCard?.sourceRunId ?? null,
candidateReviewItemIds: [],
candidateEvidenceRefs: [],
detailOpenRequestIds: [],
```

Set CTS round fields:

```ts
detailKind: 'reflection',
detailPayload: {
  roundNo: round.roundNo,
  summary: round.reflectionSummary || '等待下一轮判断',
  rationale: round.reflectionRationale || undefined,
  nextDirection: round.nextDirection || undefined,
},
eventIds: [round.eventSeq],
sourceRunId: sourceCard?.sourceRunId ?? null,
candidateReviewItemIds: [],
candidateEvidenceRefs: [],
detailOpenRequestIds: [],
```

Set Liepin search and detail fields:

```ts
const liepinReviewIds = scopedCandidateItems
  .filter((item) => item.evidence.some((evidence) => evidence.sourceKind === 'liepin'))
  .map((item) => item.reviewItemId);
const liepinBestScore = bestScore(scopedCandidateItems.filter((item) => liepinReviewIds.includes(item.reviewItemId)));
const liepinEvidenceRefs = evidenceRefsForSource(scopedCandidateItems, 'liepin');
const liepinRequestIds = scopedDetailOpenRequests.map((request) => request.requestId);
const liepinRequestSummaries = scopedDetailOpenRequests.map(detailRequestSummary);
```

```ts
detailKind: 'liepinCardSearch',
detailPayload: {
  scannedCount: sourceCard?.cardsScannedCount ?? 0,
  uniqueCandidatesCount: sourceCard?.uniqueCandidatesCount ?? 0,
},
candidateReviewItemIds: [],
candidateEvidenceRefs: [],
detailOpenRequestIds: [],
```

```ts
detailKind: 'liepinCardCandidates',
detailPayload: { candidateCount: liepinReviewIds.length, bestScore: liepinBestScore },
candidateReviewItemIds: liepinReviewIds,
candidateEvidenceRefs: liepinEvidenceRefs,
detailOpenRequestIds: [],
```

```ts
detailKind: 'liepinDetailApproval',
detailPayload: {
  requestCount: scopedDetailOpenRequests.length,
  leasedCount: scopedDetailOpenRequests.filter((request) => request.status === 'approved' || request.ledger?.status === 'leased').length,
  blockedCount: scopedDetailOpenRequests.filter((request) => request.status === 'blocked').length,
  requestIds: liepinRequestIds,
  requestSummaries: liepinRequestSummaries,
  budgetText: detailBudgetText(scopedDetailOpenRequests),
},
candidateReviewItemIds: liepinReviewIds,
candidateEvidenceRefs: liepinEvidenceRefs,
detailOpenRequestIds: liepinRequestIds,
```

- [ ] **Step 6: Add helper functions**

Add helpers to `apps/web/src/runStory.ts`:

```ts
function criteriaFromTriage(triage: WorkbenchRequirementTriage): WorkbenchRequirementTriageInput {
  return {
    mustHaves: triage.mustHaves,
    niceToHaves: triage.niceToHaves,
    synonyms: triage.synonyms,
    seniorityFilters: triage.seniorityFilters,
    exclusions: triage.exclusions,
    generatedQueryHints: triage.generatedQueryHints,
  };
}

function hasTriageInput(criteria: WorkbenchRequirementTriageInput): boolean {
  return [
    criteria.mustHaves,
    criteria.niceToHaves,
    criteria.synonyms,
    criteria.seniorityFilters,
    criteria.exclusions,
    criteria.generatedQueryHints,
  ].some((items) => items.length > 0);
}

function scopeCandidateReviewItems(
  items: WorkbenchCandidateReviewItem[],
  sourceFilter: SourceFilter,
): WorkbenchCandidateReviewItem[] {
  if (sourceFilter === 'all') {
    return items;
  }
  return items.filter((item) => item.evidence.some((evidence) => evidence.sourceKind === sourceFilter));
}

function scopeDetailOpenRequests(
  requests: WorkbenchDetailOpenRequest[],
  candidateItems: WorkbenchCandidateReviewItem[],
): WorkbenchDetailOpenRequest[] {
  const visibleReviewItemIds = new Set(candidateItems.map((item) => item.reviewItemId));
  return requests.filter((request) => visibleReviewItemIds.has(request.reviewItemId));
}

function detailBudgetText(requests: WorkbenchDetailOpenRequest[]): string | null {
  if (requests.some((request) => request.status === 'pending')) {
    return '批准后占用 1 次详情额度';
  }
  if (requests.some((request) => request.status === 'approved' || request.ledger?.status === 'leased')) {
    return '详情额度已预留';
  }
  if (requests.some((request) => request.status === 'rejected')) {
    return '已跳过，不占用额度';
  }
  if (requests.some((request) => request.status === 'bypassed')) {
    return '绕过确认，后台已按策略处理';
  }
  return null;
}

function displaySafeWarning(code: string | null, message: string | null): string | null {
  if (!code && !message) {
    return null;
  }
  if (code === 'login_required') {
    return '需要登录后才能检索。';
  }
  if (code === 'budget_blocked') {
    return '详情额度暂不可用。';
  }
  if (code === 'connection_expired') {
    return '连接已过期，需要重新登录。';
  }
  return '源状态异常，请查看设置。';
}

function bestScore(items: WorkbenchCandidateReviewItem[]): number | null {
  const scores = items.map((item) => item.aggregateScore).filter((score): score is number => score !== null);
  return scores.length === 0 ? null : Math.max(...scores);
}

function evidenceRefsForSource(
  items: WorkbenchCandidateReviewItem[],
  sourceKind: SourceKind,
): RecruiterCandidateEvidenceRef[] {
  return items.flatMap((item) =>
    item.evidence
      .filter((evidence) => evidence.sourceKind === sourceKind)
      .map((evidence) => ({
        evidenceId: evidence.evidenceId,
        reviewItemId: item.reviewItemId,
        sourceRunId: evidence.sourceRunId,
        sourceKind: evidence.sourceKind,
        evidenceLevel: evidence.evidenceLevel,
      })),
  );
}

function detailRequestSummary(request: WorkbenchDetailOpenRequest): string {
  return request.candidate?.displayName || request.reviewItemId || request.requestId;
}
```

Extend `RoundSummary`:

```ts
type RoundSummary = {
  eventSeq: number;
  roundNo: number;
  queryLabel: string;
  queryTerms: string[];
  rawCandidateCount: number;
  uniqueNewCount: number;
  newlyScoredCount: number;
  fitCount: number;
  notFitCount: number;
  reflectionSummary: string;
  reflectionRationale: string;
  nextDirection: string;
};
```

Update `roundSummaries()`:

```ts
const queryTerms = queryTermsFromPayload(item.payload);
return {
  eventSeq: item.event.globalSeq,
  roundNo,
  queryLabel: queryTerms.length > 0 ? queryTerms.join(' / ') : queryLabel(item.payload),
  queryTerms,
  rawCandidateCount: numberValue(item.payload.raw_candidate_count) ?? numberValue(item.payload.rawCandidateCount) ?? 0,
  uniqueNewCount: numberValue(item.payload.unique_new_count) ?? numberValue(item.payload.uniqueNewCount) ?? 0,
  newlyScoredCount: numberValue(item.payload.newly_scored_count) ?? numberValue(item.payload.newlyScoredCount) ?? 0,
  fitCount: numberValue(item.payload.fit_count) ?? numberValue(item.payload.fitCount) ?? 0,
  notFitCount: numberValue(item.payload.not_fit_count) ?? numberValue(item.payload.notFitCount) ?? 0,
  reflectionSummary: stringValue(item.payload.reflection_summary) ?? stringValue(item.payload.reflectionSummary) ?? '',
  reflectionRationale: stringValue(item.payload.reflection_rationale) ?? stringValue(item.payload.reflectionRationale) ?? '',
  nextDirection: stringValue(item.payload.next_direction) ?? stringValue(item.payload.nextDirection) ?? '',
};
```

Add:

```ts
function queryTermsFromPayload(payload: Record<string, unknown>): string[] {
  const executedQueries = Array.isArray(payload.executed_queries) ? payload.executed_queries : [];
  const firstQuery = recordValue(executedQueries[0]);
  return uniqueStrings([
    ...stringsValue(firstQuery?.query_terms),
    ...stringsValue(payload.query_terms),
  ]);
}
```

Update candidate score extraction to support both event and safe API data:

```ts
function candidateScoresFromItems(items: WorkbenchCandidateReviewItem[]): CandidateScore[] {
  return items
    .map((item, index) => ({
      reviewItemId: item.reviewItemId,
      score: item.aggregateScore,
      sourceKind: item.evidence[0]?.sourceKind ?? null,
      eventSeq: index + 1,
    }))
    .filter((item): item is CandidateScore => item.score !== null);
}
```

- [ ] **Step 7: Run tests**

Run:

```bash
cd apps/web && bun run test src/runStory.test.ts
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/recruiterAnimation.ts apps/web/src/runStory.ts apps/web/src/runStory.test.ts
git commit -m "feat: complete strategy graph detail contract"
```

## Task 2: Wire Safe Candidate And Detail Data Into Story Building

**Files:**
- Modify: `apps/web/src/app.tsx`
- Modify: `apps/web/src/app.test.tsx`

- [ ] **Step 1: Write failing component test for lifted safe queue data**

Add this test to `apps/web/src/app.test.tsx`. Reuse the existing `candidateReviewItem()` and `detailOpenRequest()` helpers in that file; do not redeclare helpers with the same names.

```ts
it('loads candidate and detail approval data once at the workbench shell level', async () => {
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
      return jsonResponse(candidateQueueResponse([
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
      ]));
    }
    if (url.startsWith('/api/workbench/detail-open-requests')) {
      return jsonResponse({ requests: [detailOpenRequest({ requestId: 'detail-req-1', reviewItemId: 'review-liepin-1', status: 'pending' })] });
    }
    if (url.startsWith('/api/workbench/events?after_seq=0')) {
      return eventsResponse([
        event({ globalSeq: 1, eventName: 'source_run_started', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
        event({ globalSeq: 2, eventName: 'liepin_card_search_completed', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });

  expect(await screen.findByText('候选人 A')).toBeInTheDocument();
  expect(screen.getByText('批准后占用 1 次详情额度')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "safe candidate and detail approval data"
```

Expected: FAIL because candidate/detail queries are still owned by nested queue components instead of the workbench shell.

- [ ] **Step 3: Lift candidate/detail queries into `WorkbenchShell`**

In `WorkbenchShell`, add:

```ts
const candidateItemsQuery = useCandidateReviewItems(api, session.sessionId);
const detailOpenRequestsQuery = useDetailOpenRequests(api, session.sessionId);
const candidateReviewItems = candidateItemsQuery.data?.items ?? [];
const detailOpenRequests = detailOpenRequestsQuery.data?.requests ?? [];
```

Update story creation:

```ts
const sessionStory = useMemo(
  () =>
    buildRunStory({
      session,
      events: sessionEvents,
      candidateReviewItems,
      detailOpenRequests,
      sourceFilter: 'all',
    }),
  [candidateReviewItems, detailOpenRequests, session, sessionEvents],
);
const visibleStory = useMemo(
  () =>
    buildRunStory({
      session,
      events: sessionEvents,
      candidateReviewItems,
      detailOpenRequests,
      sourceFilter,
    }),
  [candidateReviewItems, detailOpenRequests, session, sessionEvents, sourceFilter],
);
```

Pass query results into the existing right rail components:

```tsx
<RightWorkbenchTabs
  activeTab={rightDetailTab}
  onTabChange={setRightDetailTab}
  selectedGraphNode={selectedGraphNode}
  session={session}
  candidateItemsQuery={candidateItemsQuery}
  detailOpenRequestsQuery={detailOpenRequestsQuery}
/>
```

- [ ] **Step 4: Make queue components accept lifted query data**

Change `CandidateReviewQueue` props:

```ts
function CandidateReviewQueue({
  session,
  query,
}: {
  session: WorkbenchSession;
  query: ReturnType<typeof useCandidateReviewItems>;
}) {
```

Remove the local `useWorkbenchRuntime()` and `useCandidateReviewItems()` calls from that component.

Change `DetailOpenRequestQueue` props:

```ts
function DetailOpenRequestQueue({
  sessionId,
  query,
}: {
  sessionId: string;
  query: ReturnType<typeof useDetailOpenRequests>;
}) {
```

Remove the local `useDetailOpenRequests(api, sessionId)` call from that component and keep its mutation logic unchanged.

- [ ] **Step 5: Run tests**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "safe candidate and detail approval data"
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app.tsx apps/web/src/app.test.tsx
git commit -m "feat: feed safe queue data into strategy story"
```

## Task 3: Add ELK Layout With Vertical Source Lanes

**Files:**
- Create: `apps/web/src/strategyGraphLayout.ts`
- Create: `apps/web/src/strategyGraphLayout.test.ts`

- [ ] **Step 1: Write failing lane layout tests**

Create `apps/web/src/strategyGraphLayout.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import type { RecruiterGraphEdge, RecruiterGraphNode } from './recruiterAnimation';
import { fallbackLayout, stackLanePositions, toElkGraph } from './strategyGraphLayout';

function graphNode(id: string, lane: RecruiterGraphNode['lane'], x: number, y: number): RecruiterGraphNode {
  return {
    id,
    at: 0,
    kind: id === 'final-shortlist' ? '排序' : '检索',
    label: id,
    detail: id,
    x,
    y,
    tone: 'teal',
    sourceKind: lane === 'shared' ? 'all' : lane,
    sourceLabel: lane === 'shared' ? 'All sources' : lane?.toUpperCase(),
    lane,
  };
}

const nodes = [
  graphNode('job', 'shared', 10, 50),
  graphNode('requirements', 'shared', 24, 50),
  graphNode('cts-round-1-query', 'cts', 42, 32),
  graphNode('liepin-card-search', 'liepin', 42, 68),
  graphNode('final-shortlist', 'shared', 88, 50),
];
const edges: RecruiterGraphEdge[] = [
  { from: 'job', to: 'requirements', tone: 'blue', label: '提取约束' },
  { from: 'requirements', to: 'cts-round-1-query', tone: 'teal', label: 'CTS' },
  { from: 'requirements', to: 'liepin-card-search', tone: 'teal', label: 'Liepin' },
  { from: 'cts-round-1-query', to: 'final-shortlist', tone: 'green', label: '聚合排序' },
  { from: 'liepin-card-search', to: 'final-shortlist', tone: 'green', label: '聚合排序' },
];

describe('strategy graph layout', () => {
  it('converts nodes and edges into ELK input without using lane partition as vertical layout', () => {
    const graph = toElkGraph(nodes, edges);

    expect(graph.layoutOptions?.['elk.algorithm']).toBe('layered');
    expect(graph.layoutOptions?.['elk.direction']).toBe('RIGHT');
    expect(graph.children?.find((node) => node.id === 'cts-round-1-query')?.layoutOptions).toEqual(undefined);
    expect(graph.edges?.[0]).toEqual({
      id: 'job->requirements',
      sources: ['job'],
      targets: ['requirements'],
    });
  });

  it('stacks CTS above Liepin when multiple source lanes are visible', () => {
    const positioned = stackLanePositions(
      new Map([
        ['job', { x: 10, y: 50 }],
        ['requirements', { x: 220, y: 50 }],
        ['cts-round-1-query', { x: 430, y: 20 }],
        ['liepin-card-search', { x: 430, y: 20 }],
        ['final-shortlist', { x: 760, y: 50 }],
      ]),
      nodes,
      { width: 900, height: 540 },
    );

    expect(positioned.get('cts-round-1-query')!.y).toBeLessThan(positioned.get('liepin-card-search')!.y);
    expect(positioned.get('cts-round-1-query')!.x).toBe(positioned.get('liepin-card-search')!.x);
    expect(positioned.get('final-shortlist')!.x).toBeGreaterThan(positioned.get('cts-round-1-query')!.x);
    expect(positioned.get('final-shortlist')!.x).toBeGreaterThan(positioned.get('liepin-card-search')!.x);
  });

  it('uses lane-stacked fallback positions', () => {
    const laidOut = fallbackLayout(nodes, edges, { width: 900, height: 540 });

    const cts = laidOut.nodes.find((node) => node.id === 'cts-round-1-query')!;
    const liepin = laidOut.nodes.find((node) => node.id === 'liepin-card-search')!;
    const final = laidOut.nodes.find((node) => node.id === 'final-shortlist')!;
    expect(cts.position.y).toBeLessThan(liepin.position.y);
    expect(final.position.x).toBeGreaterThan(cts.position.x);
    expect(final.position.x).toBeGreaterThan(liepin.position.x);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/web && bun run test src/strategyGraphLayout.test.ts
```

Expected: FAIL because `strategyGraphLayout.ts` does not exist.

- [ ] **Step 3: Implement layout adapter types**

Create `apps/web/src/strategyGraphLayout.ts`:

```ts
import ELK from 'elkjs/lib/elk.bundled.js';
import { Position, type Edge, type Node } from '@xyflow/react';

import type { RecruiterGraphEdge, RecruiterGraphNode } from './recruiterAnimation';

export type StrategyGraphNodeData = {
  graphNode: RecruiterGraphNode;
  selected: boolean;
};

export type StrategyGraphEdgeData = {
  graphEdge: RecruiterGraphEdge;
};

export type StrategyFlowNode = Node<StrategyGraphNodeData, 'strategy'>;
export type StrategyFlowEdge = Edge<StrategyGraphEdgeData>;

export type LaidOutStrategyGraph = {
  nodes: StrategyFlowNode[];
  edges: StrategyFlowEdge[];
};

type LayoutBounds = {
  width: number;
  height: number;
};

type Point = {
  x: number;
  y: number;
};

const NODE_WIDTH = 168;
const NODE_HEIGHT = 74;
const LANE_Y_RATIOS: Record<string, number> = {
  shared: 0.42,
  cts: 0.22,
  liepin: 0.62,
};
```

- [ ] **Step 4: Implement ELK conversion without partitioning**

Add:

```ts
export function toElkGraph(nodes: RecruiterGraphNode[], edges: RecruiterGraphEdge[]) {
  return {
    id: 'strategy-root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.spacing.nodeNode': '42',
      'elk.layered.spacing.nodeNodeBetweenLayers': '62',
      'elk.edgeRouting': 'ORTHOGONAL',
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: edges.map((edge) => ({
      id: edgeId(edge),
      sources: [edge.from],
      targets: [edge.to],
    })),
  };
}
```

- [ ] **Step 5: Implement layout, fallback, and lane stacking**

Add:

```ts
export async function layoutStrategyGraph(
  nodes: RecruiterGraphNode[],
  edges: RecruiterGraphEdge[],
  bounds: LayoutBounds,
): Promise<LaidOutStrategyGraph> {
  try {
    const elk = new ELK();
    const laidOut = await elk.layout(toElkGraph(nodes, edges));
    const rawPositions = new Map(
      (laidOut.children ?? []).map((node) => [node.id, { x: Number(node.x ?? 0), y: Number(node.y ?? 0) }]),
    );
    if (rawPositions.size === 0) {
      return fallbackLayout(nodes, edges, bounds);
    }
    return {
      nodes: flowNodes(nodes, stackLanePositions(rawPositions, nodes, bounds)),
      edges: flowEdges(edges),
    };
  } catch {
    return fallbackLayout(nodes, edges, bounds);
  }
}

export function fallbackLayout(
  nodes: RecruiterGraphNode[],
  edges: RecruiterGraphEdge[],
  bounds: LayoutBounds,
): LaidOutStrategyGraph {
  const rawPositions = new Map(nodes.map((node) => [node.id, percentPosition(node, bounds)]));
  return {
    nodes: flowNodes(nodes, stackLanePositions(rawPositions, nodes, bounds)),
    edges: flowEdges(edges),
  };
}

export function stackLanePositions(
  rawPositions: Map<string, Point>,
  nodes: RecruiterGraphNode[],
  bounds: LayoutBounds,
): Map<string, Point> {
  const visibleSourceLanes = new Set(nodes.map((node) => node.lane).filter((lane) => lane === 'cts' || lane === 'liepin'));
  const multiLane = visibleSourceLanes.size > 1;
  const maxRawX = Math.max(1, ...[...rawPositions.values()].map((point) => point.x));
  const scaleX = Math.max(1, bounds.width - NODE_WIDTH - 40) / maxRawX;
  const positioned = new Map<string, Point>();

  for (const node of nodes) {
    const raw = rawPositions.get(node.id) ?? percentPosition(node, bounds);
    const isAggregation = node.id === 'final-shortlist';
    const laneKey = node.lane ?? 'shared';
    const x = isAggregation ? bounds.width - NODE_WIDTH - 34 : Math.round(Math.max(20, raw.x * scaleX));
    const laneY = Math.round((LANE_Y_RATIOS[laneKey] ?? 0.42) * bounds.height);
    const y = multiLane ? laneY : Math.round(Math.max(20, raw.y));
    positioned.set(node.id, { x, y });
  }

  return positioned;
}
```

Add flow conversion:

```ts
function flowNodes(nodes: RecruiterGraphNode[], positions: Map<string, Point>): StrategyFlowNode[] {
  return nodes.map((node) => ({
    id: node.id,
    type: 'strategy',
    position: positions.get(node.id) ?? { x: node.x, y: node.y },
    data: { graphNode: node, selected: false },
    draggable: false,
    selectable: true,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  }));
}

function flowEdges(edges: RecruiterGraphEdge[]): StrategyFlowEdge[] {
  return edges.map((edge) => ({
    id: edgeId(edge),
    source: edge.from,
    target: edge.to,
    type: 'smoothstep',
    animated: false,
    label: edge.label,
    data: { graphEdge: edge },
    className: `strategy-flow-edge ${edge.tone}`,
  }));
}

function percentPosition(node: RecruiterGraphNode, bounds: LayoutBounds): Point {
  return {
    x: Math.round((node.x / 100) * bounds.width),
    y: Math.round((node.y / 100) * bounds.height),
  };
}

function edgeId(edge: RecruiterGraphEdge): string {
  return `${edge.from}->${edge.to}`;
}
```

- [ ] **Step 6: Run layout tests**

Run:

```bash
cd apps/web && bun run test src/strategyGraphLayout.test.ts
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/strategyGraphLayout.ts apps/web/src/strategyGraphLayout.test.ts
git commit -m "feat: add lane-stacked strategy graph layout"
```

## Task 4: Add React Flow Canvas

**Files:**
- Create: `apps/web/src/StrategyGraph.tsx`
- Modify: `apps/web/src/app.tsx`
- Modify: `apps/web/src/styles.css`
- Modify: `apps/web/src/app.test.tsx`

- [ ] **Step 1: Write failing test for selectable graph nodes**

Add this test to `apps/web/src/app.test.tsx`:

```ts
it('renders selectable strategy graph nodes through React Flow', async () => {
  const currentSession = session({
    requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    sourceCards: [{ ...session().sourceCards[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
    sourceRuns: [{ ...session().sourceRuns[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
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
      return jsonResponse({ items: [] });
    }
    if (url.startsWith('/api/workbench/detail-open-requests')) {
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
            roundNo: 1,
            payload: {
              executed_queries: [{ query_terms: ['Flink CDC'] }],
              raw_candidate_count: 14,
              unique_new_count: 9,
              newly_scored_count: 9,
              fit_count: 1,
              not_fit_count: 8,
              reflection_summary: '需要放宽 Kafka 关键词。',
            },
          },
        }),
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });

  expect(await screen.findByTestId('strategy-flow')).toBeInTheDocument();
  const reflectionNode = await screen.findByRole('button', { name: /第 1 轮反思/ });
  await userEvent.click(reflectionNode);

  expect(reflectionNode).toHaveAttribute('aria-pressed', 'true');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "selectable strategy graph"
```

Expected: FAIL because `StrategyGraph` does not exist and `StrategyCanvas` still hand-renders nodes.

- [ ] **Step 3: Create `StrategyGraph.tsx` with correct React Flow types and handles**

Create `apps/web/src/StrategyGraph.tsx`:

```tsx
import { Background, Controls, Handle, Position, ReactFlow, type NodeProps } from '@xyflow/react';
import { useEffect, useMemo, useState } from 'react';

import type { RecruiterGraphNode } from './recruiterAnimation';
import type { RunStory } from './runStory';
import {
  fallbackLayout,
  layoutStrategyGraph,
  type LaidOutStrategyGraph,
  type StrategyFlowNode,
} from './strategyGraphLayout';

type StrategyGraphProps = {
  story: RunStory;
  selectedNodeId: string | null;
  onSelectNode: (node: RecruiterGraphNode) => void;
};

const bounds = { width: 980, height: 560 };
const nodeTypes = {
  strategy: StrategyGraphNode,
};

export function StrategyGraph({ story, selectedNodeId, onSelectNode }: StrategyGraphProps) {
  const fallback = useMemo(
    () => fallbackLayout(story.graphNodes, story.graphEdges, bounds),
    [story.graphEdges, story.graphNodes],
  );
  const [laidOutGraph, setLaidOutGraph] = useState<LaidOutStrategyGraph>(fallback);

  useEffect(() => {
    const nextFallback = fallbackLayout(story.graphNodes, story.graphEdges, bounds);
    setLaidOutGraph(nextFallback);
    let cancelled = false;
    void layoutStrategyGraph(story.graphNodes, story.graphEdges, bounds).then((nextGraph) => {
      if (!cancelled) {
        setLaidOutGraph(nextGraph);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [story.graphEdges, story.graphNodes]);

  const nodes = useMemo(
    () =>
      laidOutGraph.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedNodeId,
        data: { ...node.data, selected: node.id === selectedNodeId },
      })),
    [laidOutGraph.nodes, selectedNodeId],
  );

  return (
    <ReactFlow
      className="strategy-flow"
      data-testid="strategy-flow"
      nodes={nodes}
      edges={laidOutGraph.edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.45}
      maxZoom={1.6}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onSelectNode(node.data.graphNode)}
    >
      <Background gap={24} size={1} className="strategy-flow-bg" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function StrategyGraphNode({ data }: NodeProps<StrategyFlowNode>) {
  const node = data.graphNode;
  return (
    <div className="strategy-flow-node-shell">
      <Handle className="strategy-flow-handle" type="target" position={Position.Left} />
      <button
        className="strategy-flow-node"
        data-tone={node.tone}
        data-kind={node.kind}
        type="button"
        aria-pressed={data.selected}
      >
        <span>
          {node.kind}
          {node.sourceLabel && node.sourceKind !== 'all' ? <em className="node-source-badge">{node.sourceLabel}</em> : null}
        </span>
        <strong>{node.label}</strong>
        <small>{node.detail}</small>
      </button>
      <Handle className="strategy-flow-handle" type="source" position={Position.Right} />
    </div>
  );
}
```

- [ ] **Step 2: Replace hand-rendered graph in `StrategyCanvas`**

Import:

```ts
import type { RecruiterGraphNode } from './recruiterAnimation';
import { StrategyGraph } from './StrategyGraph';
```

Add props to `StrategyCanvas`:

```ts
selectedNodeId: string | null;
onSelectNode: (node: RecruiterGraphNode) => void;
```

Replace the absolute-position node rendering inside `StrategyCanvas` with:

```tsx
<StrategyGraph story={story} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
```

Keep the canvas legend, lane bands, central `启动检索` empty state, and completion toast.

- [ ] **Step 3: Add React Flow styles**

At the top of `apps/web/src/styles.css`, add:

```css
@import '@xyflow/react/dist/style.css';
```

Add:

```css
.strategy-flow {
  width: 100%;
  height: 100%;
  min-height: 520px;
  background: transparent;
}

.strategy-flow .react-flow__attribution {
  display: none;
}

.strategy-flow .react-flow__controls {
  border: 1px solid var(--line);
  border-radius: 6px;
  box-shadow: none;
  overflow: hidden;
}

.strategy-flow-node-shell {
  position: relative;
}

.strategy-flow-handle {
  opacity: 0;
  pointer-events: none;
}

.strategy-flow-node {
  width: 168px;
  display: grid;
  gap: 2px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  text-align: left;
}

.strategy-flow-node[aria-pressed="true"] {
  outline: 2px solid color-mix(in srgb, var(--accent) 54%, transparent);
  outline-offset: 3px;
}
```

Move the existing tone rules from `.graph-node[data-tone="..."]` to also match `.strategy-flow-node[data-tone="..."]`.

- [ ] **Step 2: Run tests**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "selectable strategy graph"
cd apps/web && bun run typecheck
cd apps/web && bun run build
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/StrategyGraph.tsx apps/web/src/app.tsx apps/web/src/styles.css apps/web/src/app.test.tsx
git commit -m "feat: render interactive strategy graph"
```

## Task 5: Add Node Detail Tabs And Source Filter Selection Rules

**Files:**
- Create: `apps/web/src/NodeDetailPanel.tsx`
- Modify: `apps/web/src/app.tsx`
- Modify: `apps/web/src/app.test.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: Write failing tests for inspector and filter clearing**

Add these local test helpers to `apps/web/src/app.test.tsx` before the new tests:

```ts
function renderWorkbenchWithRound(roundPayload: Record<string, unknown> = {}) {
  const currentSession = session({
    requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    sourceCards: [{ ...session().sourceCards[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
    sourceRuns: [{ ...session().sourceRuns[0], status: 'completed', cardsScannedCount: 9, uniqueCandidatesCount: 9 }],
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
      return jsonResponse(candidateQueueResponse([]));
    }
    if (url.startsWith('/api/workbench/detail-open-requests')) {
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
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });
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
      return jsonResponse(candidateQueueResponse([]));
    }
    if (url.startsWith('/api/workbench/detail-open-requests')) {
      return jsonResponse({ requests: [] });
    }
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
```

Add tests to `apps/web/src/app.test.tsx`:

```ts
it('opens node detail tab with reflection rationale when a graph node is selected', async () => {
  renderWorkbenchWithRound({
    reflection_summary: '需要放宽 Kafka 关键词。',
    reflection_rationale: '强候选人常把 Kafka 写在项目描述里。',
    next_direction: '加入实时数仓同义词。',
  });

  await userEvent.click(await screen.findByRole('button', { name: /第 1 轮反思/ }));

  expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('需要放宽 Kafka 关键词。')).toBeInTheDocument();
  expect(screen.getByText('强候选人常把 Kafka 写在项目描述里。')).toBeInTheDocument();
  expect(screen.getByText('加入实时数仓同义词。')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('tab', { name: '候选人队列' }));
  expect(screen.getByRole('tab', { name: '候选人队列' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: /第 1 轮反思/ })).toHaveAttribute('aria-pressed', 'true');
});

it('clears selected node and returns to candidate queue when source filter hides it', async () => {
  renderWorkbenchWithMultiSourceGraph();

  const sourceSelects = await screen.findAllByLabelText(/Source|View/);
  await userEvent.selectOptions(sourceSelects[0], 'liepin');
  await userEvent.click(await screen.findByRole('button', { name: /猎聘简介抓取/ }));
  expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');

  await userEvent.selectOptions(sourceSelects[0], 'cts');

  expect(screen.getByRole('tab', { name: '候选人队列' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.queryByText(/猎聘简介抓取/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "node detail tab|source filter hides"
```

Expected: FAIL because there is no node detail tab and selected-node clearing does not exist.

- [ ] **Step 3: Implement `NodeDetailPanel.tsx`**

Create `apps/web/src/NodeDetailPanel.tsx`:

```tsx
import type { RecruiterGraphNode } from './recruiterAnimation';

export function NodeDetailPanel({ node }: { node: RecruiterGraphNode | null }) {
  if (!node) {
    return (
      <div className="node-detail-empty">
        <strong>选择策略图节点</strong>
        <span>点击关键词、命中、评分、反思或详情审批节点查看业务细节。</span>
      </div>
    );
  }

  return (
    <article className="node-detail-panel">
      <div className="node-detail-head">
        <span>{node.kind}</span>
        <strong>{node.label}</strong>
        <small>{node.sourceLabel ?? 'All sources'}</small>
      </div>
      <NodePayload node={node} />
    </article>
  );
}

function NodePayload({ node }: { node: RecruiterGraphNode }) {
  const payload = node.detailPayload;
  if (!payload || !node.detailKind) {
    return <p className="muted">本节点暂无可展示的业务明细。</p>;
  }
  if (node.detailKind === 'reflection' && 'summary' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="轮次" value={`第 ${String(payload.roundNo)} 轮`} />
        <DetailBlock label="反思结果" value={payload.summary} />
        {payload.rationale ? <DetailBlock label="判断依据" value={payload.rationale} /> : null}
        {payload.nextDirection ? <DetailBlock label="下一步方向" value={payload.nextDirection} /> : null}
      </div>
    );
  }
  if (node.detailKind === 'requirements' && 'triageStatus' in payload) {
    const sourceLabel =
      payload.triageStatus === 'confirmed' ? '用户已确认' : payload.triageStatus === 'draft' ? '待确认草稿' : '后台提取';
    return (
      <div className="node-detail-body">
        <DetailRow label="来源" value={sourceLabel} />
        <DetailBlock label="Must-have" value={payload.mustHaves.join(' / ') || '暂无'} />
        <DetailBlock label="Nice-to-have" value={payload.niceToHaves.join(' / ') || '暂无'} />
        <DetailBlock label="关键词提示" value={payload.queryHints.join(' / ') || '暂无'} />
      </div>
    );
  }
  if (node.detailKind === 'ctsRoundQuery' && 'queryTerms' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="轮次" value={`第 ${String(payload.roundNo)} 轮`} />
        <DetailBlock label="关键词" value={payload.queryTerms.join(' / ') || payload.queryLabel} />
      </div>
    );
  }
  if (node.detailKind === 'ctsRoundResults' && 'rawCandidateCount' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="搜到候选人" value={`${String(payload.rawCandidateCount)} 人`} />
        <DetailRow label="新增候选人" value={`${String(payload.uniqueNewCount)} 人`} />
      </div>
    );
  }
  if (node.detailKind === 'ctsRoundScoring' && 'newlyScoredCount' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="进入评分" value={`${String(payload.newlyScoredCount)} 人`} />
        <DetailRow label="Fit" value={`${String(payload.fitCount)} 人`} />
        <DetailRow label="Not fit" value={`${String(payload.notFitCount)} 人`} />
      </div>
    );
  }
  if (node.detailKind === 'sourceQueue' && 'status' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="状态" value={payload.status} />
        <DetailRow label="权限" value={payload.authState} />
        <DetailRow label="扫描" value={`${String(payload.scannedCount)} 张`} />
        <DetailRow label="命中" value={`${String(payload.uniqueCandidatesCount)} 人`} />
        {payload.warningMessage ? <DetailBlock label="提示" value={payload.warningMessage} /> : null}
      </div>
    );
  }
  if (node.detailKind === 'liepinCardSearch' && 'scannedCount' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="已看简介" value={`${String(payload.scannedCount)} 张`} />
        <DetailRow label="候选人" value={`${String(payload.uniqueCandidatesCount)} 人`} />
      </div>
    );
  }
  if (node.detailKind === 'liepinDetailApproval' && 'requestCount' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="详情请求" value={`${String(payload.requestCount)} 个`} />
        <DetailRow label="已预留" value={`${String(payload.leasedCount)} 个`} />
        <DetailRow label="阻塞" value={`${String(payload.blockedCount)} 个`} />
        <DetailBlock label="请求" value={payload.requestSummaries.join(' / ') || payload.requestIds.join(' / ') || '暂无'} />
        {payload.budgetText ? <DetailBlock label="额度影响" value={payload.budgetText} /> : null}
      </div>
    );
  }
  if ('candidateCount' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="候选人" value={`${String(payload.candidateCount)} 人`} />
        <DetailRow label="最高分" value={payload.bestScore === null ? '暂无' : String(payload.bestScore)} />
      </div>
    );
  }
  if ('jobTitle' in payload) {
    return (
      <div className="node-detail-body">
        <DetailRow label="岗位" value={payload.jobTitle} />
        <DetailRow label="源模式" value={payload.sourceMode === 'multi' ? '多源' : '单源'} />
        <DetailBlock label="JD 摘要" value={payload.jdPreview} />
      </div>
    );
  }
  return <p className="muted">本节点暂无可展示的业务明细。</p>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="node-detail-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DetailBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="node-detail-block">
      <span>{label}</span>
      <p>{value}</p>
    </div>
  );
}
```

- [ ] **Step 4: Add selected-node state and clearing rule**

In `WorkbenchShell`:

```ts
const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null);
const [rightDetailTab, setRightDetailTab] = useState<'candidates' | 'node'>('candidates');
const selectedGraphNode = visibleStory.graphNodes.find((node) => node.id === selectedGraphNodeId) ?? null;

useEffect(() => {
  if (!selectedGraphNodeId) {
    setRightDetailTab('candidates');
    return;
  }
  const stillVisible = visibleStory.graphNodes.some((node) => node.id === selectedGraphNodeId);
  if (!stillVisible) {
    setSelectedGraphNodeId(null);
    setRightDetailTab('candidates');
  }
}, [selectedGraphNodeId, visibleStory.graphNodes]);
```

Pass to `StrategyCanvas`:

```tsx
selectedNodeId={selectedGraphNodeId}
onSelectNode={(node) => {
  setSelectedGraphNodeId(node.id);
  setRightDetailTab('node');
}}
```

- [ ] **Step 5: Add right-lower tabs**

Create `RightWorkbenchTabs` in `apps/web/src/app.tsx`:

```tsx
function RightWorkbenchTabs({
  activeTab,
  onTabChange,
  selectedGraphNode,
  session,
  candidateItemsQuery,
  detailOpenRequestsQuery,
}: {
  activeTab: 'candidates' | 'node';
  onTabChange: (tab: 'candidates' | 'node') => void;
  selectedGraphNode: RecruiterGraphNode | null;
  session: WorkbenchSession;
  candidateItemsQuery: ReturnType<typeof useCandidateReviewItems>;
  detailOpenRequestsQuery: ReturnType<typeof useDetailOpenRequests>;
}) {
  return (
    <div className="right-workbench-tabs">
      <div className="right-tab-list" role="tablist" aria-label="Workbench detail view">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'candidates'}
          className={activeTab === 'candidates' ? 'active' : ''}
          onClick={() => onTabChange('candidates')}
        >
          候选人队列
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'node'}
          className={activeTab === 'node' ? 'active' : ''}
          onClick={() => onTabChange('node')}
        >
          节点详情
        </button>
      </div>
      {activeTab === 'candidates' ? (
        <>
          <CandidateReviewQueue session={session} query={candidateItemsQuery} />
          <DetailOpenRequestQueue sessionId={session.sessionId} query={detailOpenRequestsQuery} />
        </>
      ) : (
        <NodeDetailPanel node={selectedGraphNode} />
      )}
    </div>
  );
}
```

- [ ] **Step 6: Add tab and inspector styles**

Add styles shown below to `apps/web/src/styles.css`:

```css
.right-workbench-tabs {
  min-height: 0;
  display: grid;
  gap: 10px;
}

.right-tab-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border: 1px solid var(--line);
  border-radius: 7px;
  overflow: hidden;
  background: var(--panel);
}

.right-tab-list button {
  min-height: 30px;
  border: 0;
  border-right: 1px solid var(--line);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 800;
}

.right-tab-list button:last-child {
  border-right: 0;
}

.right-tab-list button.active,
.right-tab-list button[aria-selected="true"] {
  background: var(--surface);
  color: var(--accent);
}

.node-detail-panel,
.node-detail-empty {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface);
}

.node-detail-head,
.node-detail-body,
.node-detail-block {
  display: grid;
  gap: 6px;
}

.node-detail-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.node-detail-head span,
.node-detail-row span,
.node-detail-block span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 800;
}

.node-detail-block p,
.node-detail-empty span {
  color: var(--text-soft);
  font-size: 12px;
  line-height: 1.45;
}

@media (max-width: 1180px) and (min-width: 861px) {
  .right-rail {
    display: grid;
    grid-column: 1 / -1;
  }
}
```

- [ ] **Step 7: Run tests**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "node detail tab|source filter hides"
cd apps/web && bun run test
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/NodeDetailPanel.tsx apps/web/src/app.tsx apps/web/src/app.test.tsx apps/web/src/styles.css
git commit -m "feat: add strategy node inspector"
```

## Task 6: Link Running Notes And Candidate Evidence To Graph Nodes

**Files:**
- Modify: `apps/web/src/runStory.ts`
- Modify: `apps/web/src/app.tsx`
- Modify: `apps/web/src/app.test.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: Write failing tests for note and candidate linking**

Add tests to `apps/web/src/app.test.tsx`:

```ts
function renderWorkbenchWithCandidate(candidateOverrides: Record<string, unknown> = {}) {
  const currentSession = session({
    requirementTriage: triage({ status: 'approved', approvedAt: '2026-05-09T00:02:00Z' }),
    sourceCards: [
      { ...session().sourceCards[0], status: 'completed' },
      { ...session().sourceCards[1], status: 'completed', connectionStatus: 'connected', cardsScannedCount: 12, uniqueCandidatesCount: 1 },
    ],
  });
  const candidate = candidateReviewItem({
    reviewItemId: 'review-liepin-1',
    displayName: '候选人 A',
    sourceBadges: ['Liepin'],
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
    ...candidateOverrides,
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
      return jsonResponse(candidateQueueResponse([candidate]));
    }
    if (url.startsWith('/api/workbench/detail-open-requests')) {
      return jsonResponse({ requests: [] });
    }
    if (url.startsWith('/api/workbench/events?after_seq=0')) {
      return eventsResponse([
        event({ globalSeq: 1, eventName: 'source_run_started', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
        event({ globalSeq: 2, eventName: 'liepin_card_search_completed', sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
      ]);
    }
    throw new Error(`Unexpected request ${url}`);
  });
}

it('selects the related graph node from a running note entry', async () => {
  renderWorkbenchWithRound({ reflection_summary: '需要放宽 Kafka 关键词。' });

  await userEvent.click(await screen.findByRole('button', { name: /反思：需要放宽 Kafka 关键词。/ }));

  expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: /第 1 轮反思/ })).toHaveAttribute('aria-pressed', 'true');
});

it('selects the related graph node from a candidate evidence action', async () => {
  renderWorkbenchWithCandidate({
    reviewItemId: 'review-liepin-1',
    sourceKind: 'liepin',
    aggregateScore: 91,
  });

  await userEvent.click(await screen.findByRole('button', { name: /查看策略节点/ }));

  expect(screen.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText(/候选人/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /猎聘候选人|候选人/ })).toHaveAttribute('aria-pressed', 'true');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "running note entry|candidate evidence action"
```

Expected: FAIL because log entries and candidate cards cannot select graph nodes.

- [ ] **Step 3: Add `relatedNodeId` to log entries**

In `apps/web/src/runStory.ts`, set `relatedNodeId` when creating business logs:

```ts
{
  id: `${queryId}-log`,
  relatedNodeId: queryId,
  ...
}
{
  id: `${resultId}-log`,
  relatedNodeId: resultId,
  ...
}
{
  id: `${scoreId}-log`,
  relatedNodeId: scoreId,
  ...
}
{
  id: `${reflectId}-log`,
  relatedNodeId: reflectId,
  ...
}
```

For Liepin logs:

```ts
{
  id: `${searchId}-log`,
  relatedNodeId: searchId,
  ...
}
{
  id: `${candidateId}-log`,
  relatedNodeId: candidateId,
  ...
}
{
  id: `${detailId}-log`,
  relatedNodeId: detailId,
  ...
}
```

- [ ] **Step 4: Build candidate evidence and review-item indexes**

In `WorkbenchShell`, add:

```ts
const evidenceRefToGraphNodeId = useMemo(() => {
  const index = new Map<string, string>();
  for (const node of visibleStory.graphNodes) {
    for (const ref of node.candidateEvidenceRefs ?? []) {
      index.set(evidenceRefKey(ref.evidenceId, ref.sourceRunId, ref.evidenceLevel), node.id);
    }
  }
  return index;
}, [visibleStory.graphNodes]);

const reviewItemToGraphNodeId = useMemo(() => {
  const index = new Map<string, string>();
  for (const node of visibleStory.graphNodes) {
    for (const reviewItemId of node.candidateReviewItemIds ?? []) {
      if (!index.has(reviewItemId)) {
        index.set(reviewItemId, node.id);
      }
    }
  }
  return index;
}, [visibleStory.graphNodes]);

function selectGraphNodeId(nodeId: string) {
  setSelectedGraphNodeId(nodeId);
  setRightDetailTab('node');
}

```

Pass `selectGraphNodeId`, `evidenceRefToGraphNodeId`, and `reviewItemToGraphNodeId` to `ActivityLog`, `RightWorkbenchTabs`, and `CandidateReviewQueue`.

- [ ] **Step 5: Make running notes selectable**

Change `ActivityLog` props:

```ts
onSelectGraphNodeId: (nodeId: string) => void;
```

Render business log entries as buttons when `relatedNodeId` exists:

```tsx
{event.relatedNodeId ? (
  <button className="log-entry-button" type="button" onClick={() => onSelectGraphNodeId(event.relatedNodeId!)}>
    {event.sourceLabel && event.sourceKind !== 'all' ? <em className="log-source-badge">{event.sourceLabel}</em> : null}
    {event.text}
  </button>
) : (
  <strong>
    {event.sourceLabel && event.sourceKind !== 'all' ? <em className="log-source-badge">{event.sourceLabel}</em> : null}
    {event.text}
  </strong>
)}
```

- [ ] **Step 6: Add candidate evidence action**

Change `CandidateReviewQueue` props:

```ts
evidenceRefToGraphNodeId: Map<string, string>;
reviewItemToGraphNodeId: Map<string, string>;
onSelectGraphNodeId: (nodeId: string) => void;
```

Pass those props to `CandidateReviewCard`:

```tsx
<CandidateReviewCard
  key={item.reviewItemId}
  item={item}
  sessionId={session.sessionId}
  graphNodeId={candidateEvidenceGraphNodeId(item, evidenceRefToGraphNodeId, reviewItemToGraphNodeId)}
  onSelectGraphNodeId={onSelectGraphNodeId}
/>
```

Change `CandidateReviewCard` props:

```ts
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
```

Render this action near the candidate card actions:

```tsx
{graphNodeId ? (
  <button className="secondary-link" type="button" onClick={() => onSelectGraphNodeId(graphNodeId)}>
    查看策略节点
  </button>
) : null}
```

Add these module-level helpers in `apps/web/src/app.tsx`:

```ts
function evidenceRefKey(evidenceId: string, sourceRunId: string, evidenceLevel: string): string {
  return `${evidenceId}:${sourceRunId}:${evidenceLevel}`;
}

function candidateEvidenceGraphNodeId(
  item: WorkbenchCandidateReviewItem,
  evidenceRefToGraphNodeId: Map<string, string>,
  reviewItemToGraphNodeId: Map<string, string>,
): string | null {
  for (const evidence of item.evidence) {
    const nodeId = evidenceRefToGraphNodeId.get(evidenceRefKey(evidence.evidenceId, evidence.sourceRunId, evidence.evidenceLevel));
    if (nodeId) {
      return nodeId;
    }
  }
  return reviewItemToGraphNodeId.get(item.reviewItemId) ?? null;
}
```

- [ ] **Step 7: Add styles**

Add:

```css
.log-entry-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
  text-align: left;
  font: inherit;
  font-weight: 800;
}

.log-entry-button:hover {
  color: var(--accent);
}
```

- [ ] **Step 8: Run tests**

Run:

```bash
cd apps/web && bun run test src/app.test.tsx -t "running note entry|candidate evidence action"
cd apps/web && bun run test
cd apps/web && bun run typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/runStory.ts apps/web/src/app.tsx apps/web/src/app.test.tsx apps/web/src/styles.css
git commit -m "feat: link strategy graph to evidence"
```

## Task 7: Visual Regression And Final Verification

**Files:**
- Modify: `apps/web/tests/visual/workbench.visual.spec.ts`
- Modify: `docs/ui.md`
- Modify: `docs/superpowers/2026-05-09-multi-source-workbench-execution.md`

- [ ] **Step 1: Rewrite visual test away from playback UI**

In `apps/web/tests/visual/workbench.visual.spec.ts`, remove or replace the old playback-only assertions that click `Start playback` or assert `.playback-bar`, `.elapsed`, `.status-text`, or old `.graph-node` selectors. The visual test must exercise the current workbench surface directly.

Add this flow to the workbench visual test:

```ts
await expect(page.getByTestId('strategy-flow')).toBeVisible();
await page.getByRole('button', { name: /第 1 轮反思|反思/ }).first().click();
await expect(page.getByRole('tab', { name: '节点详情' })).toHaveAttribute('aria-selected', 'true');
await expect(page.getByRole('tab', { name: '候选人队列' })).toBeVisible();
await page.screenshot({ path: 'tests/visual/artifacts/strategy-graph-node-detail.png', fullPage: true });

await page.setViewportSize({ width: 1024, height: 768 });
await expect(page.getByRole('tab', { name: '节点详情' })).toBeVisible();
await expect(page.getByTestId('strategy-flow')).toBeVisible();
```

Expected: no visual test path still references the deleted playback controls.

- [ ] **Step 2: Run frontend verification**

Run:

```bash
cd apps/web && bun run test
cd apps/web && bun run typecheck
cd apps/web && bun run build
cd apps/web && bun run test:visual
```

Expected: PASS. Inspect the selected-node screenshot before accepting any baseline change.

- [ ] **Step 3: Run backend regression**

Run:

```bash
uv run pytest tests/test_workbench_api.py -q
```

Expected: PASS. This slice does not change Python API behavior.

- [ ] **Step 4: Manual browser verification**

Start services:

```bash
uv run seektalent-ui-api
cd apps/web && bun run dev --host 127.0.0.1 --port 5176
```

Create or reuse a fresh session through the UI, or use a documented seed fixture. Do not depend on a machine-local session id.

Open the created session:

```text
http://127.0.0.1:5176/sessions/<created-session-id>
```

Verify:

- strategy graph is visible and not blank;
- React Flow pan, zoom, and fit view work;
- `启动检索` is the only run-start action in empty graph state;
- `启动全部`, `启动 CTS`, and `启动猎聘` do not appear;
- clicking a reflection node opens `节点详情`;
- reflection detail shows summary, rationale, and next direction when present;
- clicking a running note opens the related node;
- clicking `查看策略节点` on a candidate card opens the related node;
- switching to `候选人队列` preserves selected graph state;
- switching source filter to a source that hides the selected node returns to `候选人队列`;
- source filter still changes both graph and running notes;
- at 1024px width, selecting a graph node still exposes `节点详情`.

- [ ] **Step 5: Update docs**

In `docs/ui.md`, add:

```md
## Interactive Strategy Graph

The workbench strategy graph is rendered with React Flow and laid out through ELK. It is not a workflow engine; it is a recruiter-facing projection of durable Workbench session events, source-run state, candidate evidence, and detail approval state.

Clicking a graph node opens the `节点详情` tab in the right-lower workbench area. The `候选人队列` tab remains available and is still the default candidate-review surface. Running notes and candidate evidence actions can jump to related graph nodes when the backend-safe data contains the relationship.
```

In `docs/superpowers/2026-05-09-multi-source-workbench-execution.md`, add:

```md
## Interactive Strategy Graph Verification

- React Flow graph renders from `buildRunStory()` business nodes.
- ELK layout adapter is enabled with deterministic source-lane stacking.
- Node detail tab shows business payloads for requirements, CTS rounds, reflection, Liepin card search, detail approval, and aggregation.
- Candidate queue remains available as a right-lower tab.
- Running notes and candidate evidence actions can select related graph nodes.
- Verified commands:
  - `cd apps/web && bun run test`
  - `cd apps/web && bun run typecheck`
  - `cd apps/web && bun run build`
  - `cd apps/web && bun run test:visual`
  - `uv run pytest tests/test_workbench_api.py -q`
```

- [ ] **Step 6: Final diff and commit**

Run:

```bash
git diff --check
git status --short
```

Commit:

```bash
git add apps/web/tests/visual/workbench.visual.spec.ts docs/ui.md docs/superpowers/2026-05-09-multi-source-workbench-execution.md
git commit -m "docs: verify interactive strategy graph"
```

## Self-Review Checklist

- [ ] Complete detail payload fields are implemented in Task 1.
- [ ] Candidate review items and detail-open requests enter `buildRunStory()` in Tasks 1 and 2.
- [ ] Candidate evidence action selects graph nodes in Task 6.
- [ ] ELK layout uses vertical source-lane stacking in Task 3.
- [ ] React Flow dependencies and Vitest setup are covered in Task 0; React Flow typing, hidden handles, and parent sizing are covered in Task 4.
- [ ] Source-filter selected-node clearing is covered in Task 5.
- [ ] Visual and manual verification are covered in Task 7.

## Execution Handoff

Plan complete when all tasks pass and the branch has no unexpected dirty files. Use `superpowers:subagent-driven-development` for execution because Tasks 1, 3, 5, and 6 are bounded slices that can be reviewed independently after dependency setup lands.

## GSTACK AUTOPLAN REVIEW REPORT - 2026-05-11

Status: `RESOLVED_BY_PLAN_EDITS`. This section records the blockers found by `/autoplan`; the plan body above has been updated to address them before Superpowers execution.

### P0 Blockers

1. Dependency installation order is wrong.
   - Evidence: Task 3 creates `strategyGraphLayout.ts` and imports `elkjs` plus `@xyflow/react`, but Task 4 installs those dependencies later.
   - Impact: `bun run test src/strategyGraphLayout.test.ts` and `bun run typecheck` will fail with missing modules during Task 3.
   - Required fix: Move `bun add @xyflow/react elkjs`, `vite.config.ts` setupFiles, and `setupTests.ts` before any Task 3 imports, or move the whole dependency/setup task before layout implementation.

2. Task 1 updates app call sites with variables introduced only in Task 2.
   - Evidence: Task 1 Step 4 changes calls to `buildRunStory({ session, events: sessionEvents, candidateReviewItems, detailOpenRequests, sourceFilter })`; Task 2 Step 3 introduces those query variables.
   - Impact: A task-by-task worker following Task 1 literally will break `app.tsx` typecheck.
   - Required fix: In Task 1, update only the function signature and direct tests, and use optional omitted inputs in app call sites: `buildRunStory({ session, events: sessionEvents, sourceFilter })`. Task 2 should add `candidateReviewItems` and `detailOpenRequests`.

3. Task 2 contains a component test that depends on Task 4/5 UI that does not exist yet.
   - Evidence: Task 2 Step 1 clicks a graph node and asserts the `节点详情` tab plus detail approval budget text before React Flow and `NodeDetailPanel` are created.
   - Impact: The failing-test checkpoint is not testing Task 2's intended responsibility; it will remain red for unrelated future tasks.
   - Required fix: Task 2 tests should only prove safe candidate/detail data is fetched and passed into `buildRunStory()`, or move the node-click/budget-text assertion to Task 5 after `StrategyGraph` and `NodeDetailPanel` exist.

4. Visual regression plan still relies on deprecated playback UI.
   - Evidence: `apps/web/tests/visual/workbench.visual.spec.ts` still clicks `Start playback`, checks `.graph-node`, `.status-text`, and `.elapsed`; this plan's target UI removes playback and replaces `.graph-node` with React Flow nodes.
   - Impact: Final `bun run test:visual` is likely to fail even if the feature implementation is correct.
   - Required fix: Rewrite visual specs for the current workbench flow before the final gate: open a real or mocked session, assert `strategy-flow`, node selection, right-lower tabs, source filter, candidate queue, and selected-node visual state.

### P1 Issues

1. Candidate evidence to graph-node mapping is too coarse.
   - Evidence: The plan maps `reviewItemId -> graphNodeId`, while current safe data exposes evidence-level fields: `evidenceId`, `sourceRunId`, `sourceKind`, and `evidenceLevel`.
   - Impact: Multi-source candidates can jump to the wrong source node or lose the exact evidence relationship.
   - Required fix: Build an evidence-aware index such as `evidenceId/sourceRunId/evidenceLevel -> graphNodeId`, then let candidate cards link each evidence item to the most specific graph node available.

2. Detail approval inspector does not satisfy the spec's request-id requirement.
   - Evidence: The spec requires Liepin detail approval nodes to show request ids and budget text, but the plan's `NodeDetailPanel` only renders counts and budget text.
   - Impact: Users cannot trace which detail approvals are represented by the graph node.
   - Required fix: Add `detailOpenRequestIds` and either request ids or safe candidate summaries to the detail approval payload/panel, with tests.

3. Requirements node confirmation logic ignores triage status.
   - Evidence: The plan marks any non-empty triage as `confirmed`; the frontend type has `requirementTriage.status`.
   - Impact: Draft user criteria can be displayed as confirmed conditions.
   - Required fix: Only `status === 'approved'` should become `confirmed`; draft non-empty criteria should show as draft/pending or fall back to runtime criteria with an explicit state.

4. Raw warning display remains too permissive.
   - Evidence: `displaySafeWarning()` redacts some keywords but still falls back to arbitrary warning text.
   - Impact: Provider/raw/backend strings may leak into the UI despite the safe-data requirement.
   - Required fix: Use an allowlist keyed by `warningCode`; unknown warnings should display a generic safe message.

### P2 Issues

1. Undefined test helpers make the plan non-executable as written.
   - Evidence: Task 5/6 reference `renderWorkbenchWithRound`, `renderWorkbenchWithMultiSourceGraph`, and `renderWorkbenchWithCandidate`, but those helpers do not exist in the current test file.
   - Required fix: Define those helpers explicitly in the plan, or inline the mock route setup in each test.

2. The plan duplicates existing test helpers.
   - Evidence: `app.test.tsx` already has `candidateReviewItem()` and `detailOpenRequest()` helpers; Task 2 asks to add helpers with the same names.
   - Required fix: Reuse or extend the existing helpers rather than redeclaring them.

3. Medium-width responsive layout can hide the new node detail tabs.
   - Evidence: Existing CSS hides `.right-rail` at `max-width: 1180px` and only restores layout at `max-width: 860px`.
   - Required fix: Add a responsive task/test for 1024px width so node details remain reachable, either below the strategy panel or in a drawer.

4. Manual verification depends on a local session id.
   - Evidence: The previous manual verification path named a machine-local session id instead of a reproducible fixture.
   - Required fix: Replace this with a seedable fixture/session creation flow or a mocked Playwright visual route.

### Product And Design Verdict

The chosen architecture remains the right direction: `buildRunStory()` owns business graph derivation, ELK owns layout, React Flow owns graph interaction, and backend state remains authoritative. The plan body above has been repaired rather than abandoned.

### Applied Plan Edits Before Execution

1. Added early Task 0 to install graph dependencies, configure Vitest setup, and add React Flow DOM mocks.
2. Split Task 1 so it changes pure model/types/tests first and does not depend on lifted app queries.
3. Moved node-click/inspector assertions to tasks where `StrategyGraph` and `NodeDetailPanel` exist.
4. Replaced review-item-only candidate linking with evidence-aware graph-node linking.
5. Added request ids/safe summaries to detail approval node details.
6. Fixed triage confirmed/draft/runtime semantics.
7. Rewrote the Playwright visual direction away from playback selectors and added 1024px reachability coverage.
