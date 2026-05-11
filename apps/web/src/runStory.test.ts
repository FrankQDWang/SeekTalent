import { describe, expect, it } from 'vitest';

import { buildRunStory } from './runStory';
import type { WorkbenchEvent, WorkbenchRequirementTriage, WorkbenchSession } from './types';

function triage(overrides: Partial<WorkbenchRequirementTriage> = {}): WorkbenchRequirementTriage {
  return {
    sessionId: 'session-1',
    status: 'approved',
    mustHaves: [],
    niceToHaves: [],
    synonyms: [],
    seniorityFilters: [],
    exclusions: [],
    generatedQueryHints: [],
    createdAt: '2026-05-09T00:00:00Z',
    updatedAt: '2026-05-09T00:00:00Z',
    approvedAt: '2026-05-09T00:00:00Z',
    ...overrides,
  };
}

function session(overrides: Partial<WorkbenchSession> = {}): WorkbenchSession {
  return {
    sessionId: 'session-1',
    workspaceId: 'default',
    ownerUserId: 'user-1',
    jobTitle: 'Streaming Data Engineer',
    jdText: 'Build streaming data systems.',
    notes: '',
    status: 'draft',
    requirementTriage: triage(),
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
      {
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        status: 'completed',
        authState: 'not_required',
        cardsScannedCount: 30,
        uniqueCandidatesCount: 5,
        detailOpenUsedCount: 1,
        detailOpenBlockedCount: 1,
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
      {
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        label: 'Liepin',
        status: 'completed',
        authState: 'not_required',
        cardsScannedCount: 30,
        uniqueCandidatesCount: 5,
        detailOpenUsedCount: 1,
        detailOpenBlockedCount: 1,
        warningCode: null,
        warningMessage: null,
        connectionStatus: 'connected',
      },
    ],
    ...overrides,
  };
}

function event(overrides: Partial<WorkbenchEvent>): WorkbenchEvent {
  return {
    globalSeq: overrides.globalSeq ?? 1,
    sessionSeq: overrides.sessionSeq ?? overrides.globalSeq ?? 1,
    sessionId: 'session-1',
    sourceRunId: overrides.sourceRunId ?? 'src-cts',
    sourceKind: overrides.sourceKind ?? 'cts',
    eventName: overrides.eventName ?? 'source_run_started',
    payload: overrides.payload ?? {},
    createdAt: overrides.createdAt ?? `2026-05-09T00:00:${String(overrides.globalSeq ?? 1).padStart(2, '0')}Z`,
  };
}

const events: WorkbenchEvent[] = [
  event({
    globalSeq: 1,
    sourceKind: 'cts',
    sourceRunId: 'src-cts',
    eventName: 'runtime_requirements_completed',
    payload: {
      payload: {
        must_have_capabilities: ['Flink CDC'],
        preferred_capabilities: ['data platform'],
        search_terms: ['streaming data'],
      },
    },
  }),
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
      },
    },
  }),
  event({
    globalSeq: 3,
    sourceKind: 'cts',
    sourceRunId: 'src-cts',
    eventName: 'candidate_review_item_upserted',
    payload: { reviewItemId: 'review-cts-1', score: 80, sourceKind: 'cts' },
  }),
  event({
    globalSeq: 4,
    sourceKind: 'liepin',
    sourceRunId: 'src-liepin',
    eventName: 'source_run_started',
    payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' },
  }),
  event({
    globalSeq: 5,
    sourceKind: 'liepin',
    sourceRunId: 'src-liepin',
    eventName: 'liepin_card_search_completed',
    payload: { cardsScannedCount: 30, uniqueCandidatesCount: 5 },
  }),
  event({
    globalSeq: 6,
    sourceKind: 'liepin',
    sourceRunId: 'src-liepin',
    eventName: 'candidate_review_item_upserted',
    payload: { reviewItemId: 'review-liepin-1', autoDetailScore: 91, sourceKind: 'liepin' },
  }),
  event({
    globalSeq: 7,
    sourceKind: 'liepin',
    sourceRunId: 'src-liepin',
    eventName: 'liepin_detail_open_auto_recommended',
    payload: { reviewItemId: 'review-liepin-1' },
  }),
];

describe('buildRunStory', () => {
  it('builds separate CTS and Liepin lanes in the all-sources story', () => {
    const story = buildRunStory(session(), events, { sourceFilter: 'all' });

    expect(story.graphNodes.some((node) => node.lane === 'cts' && node.label.includes('第 1 轮关键词'))).toBe(true);
    expect(story.graphNodes.some((node) => node.lane === 'liepin' && node.label.includes('猎聘简介抓取'))).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === 'CTS 检索')).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === '猎聘简介抓取')).toBe(true);
    expect(story.graphNodes.find((node) => node.id === 'final-shortlist')?.detail).toBe('最高 91 分');
  });

  it('filters graph nodes and business logs by source', () => {
    const ctsStory = buildRunStory(session(), events, { sourceFilter: 'cts' });
    const liepinStory = buildRunStory(session(), events, { sourceFilter: 'liepin' });

    expect(ctsStory.graphNodes.some((node) => node.sourceKind === 'liepin')).toBe(false);
    expect(ctsStory.logEntries.some((entry) => entry.sourceKind === 'liepin')).toBe(false);
    expect(liepinStory.graphNodes.some((node) => node.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.text.includes('详情'))).toBe(true);
  });
});
