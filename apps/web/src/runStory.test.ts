import { describe, expect, it } from 'vitest';

import { buildRunStory } from './runStory';
import type {
  WorkbenchCandidateReviewItem,
  WorkbenchDetailOpenRequest,
  WorkbenchEvent,
  WorkbenchRequirementTriage,
  WorkbenchSession,
} from './types';

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

function candidateReviewItem(overrides: Partial<WorkbenchCandidateReviewItem> = {}): WorkbenchCandidateReviewItem {
  return {
    reviewItemId: 'review-liepin-1',
    sessionId: 'session-1',
    status: 'new',
    note: '',
    displayName: 'Ada Chen',
    title: 'Data Platform Engineer',
    company: 'Example Inc.',
    location: 'Shanghai',
    summary: 'Built Kafka and Flink data platforms.',
    aggregateScore: 93,
    fitBucket: 'fit',
    sourceBadges: ['Liepin'],
    evidenceLevel: 'detail',
    matchedMustHaves: ['Flink CDC'],
    matchedPreferences: ['data platform'],
    missingRisks: [],
    strengths: ['streaming systems'],
    weaknesses: [],
    evidence: [
      {
        evidenceId: 'evidence-liepin-1',
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        evidenceLevel: 'detail',
        score: 93,
        fitBucket: 'fit',
        matchedMustHaves: ['Flink CDC'],
        matchedPreferences: ['data platform'],
        missingRisks: [],
        strengths: ['streaming systems'],
        weaknesses: [],
        createdAt: '2026-05-09T00:00:06Z',
      },
    ],
    createdAt: '2026-05-09T00:00:06Z',
    updatedAt: '2026-05-09T00:00:06Z',
    ...overrides,
  };
}

function detailOpenRequest(overrides: Partial<WorkbenchDetailOpenRequest> = {}): WorkbenchDetailOpenRequest {
  return {
    requestId: 'detail-request-1',
    sessionId: 'session-1',
    reviewItemId: 'review-liepin-1',
    status: 'approved',
    detailOpenMode: 'human_confirm',
    decisionNote: null,
    candidate: {
      reviewItemId: 'review-liepin-1',
      displayName: 'Ada Chen',
      title: 'Data Platform Engineer',
      company: 'Example Inc.',
      location: 'Shanghai',
      summary: 'Built Kafka and Flink data platforms.',
      aggregateScore: 93,
      evidenceLevel: 'detail',
      sourceBadges: ['Liepin'],
      matchedMustHaves: ['Flink CDC'],
      matchedPreferences: ['data platform'],
      missingRisks: [],
    },
    blockedReason: null,
    ledger: {
      ledgerId: 'ledger-1',
      status: 'leased',
      budgetDay: '2026-05-09',
      leaseExpiresAt: null,
    },
    providerAction: null,
    createdAt: '2026-05-09T00:00:07Z',
    updatedAt: '2026-05-09T00:00:07Z',
    ...overrides,
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
    const story = buildRunStory({ session: session(), events, sourceFilter: 'all' });

    expect(story.graphNodes.some((node) => node.lane === 'cts' && node.label.includes('第 1 轮关键词'))).toBe(true);
    expect(story.graphNodes.some((node) => node.lane === 'liepin' && node.label.includes('猎聘简介抓取'))).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === 'CTS 检索')).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === '猎聘简介抓取')).toBe(true);
    expect(story.graphNodes.find((node) => node.id === 'final-shortlist')?.detail).toBe('最高 91 分');
  });

  it('filters graph nodes and business logs by source', () => {
    const ctsStory = buildRunStory({ session: session(), events, sourceFilter: 'cts' });
    const liepinStory = buildRunStory({ session: session(), events, sourceFilter: 'liepin' });

    expect(ctsStory.graphNodes.some((node) => node.sourceKind === 'liepin')).toBe(false);
    expect(ctsStory.logEntries.some((entry) => entry.sourceKind === 'liepin')).toBe(false);
    expect(liepinStory.graphNodes.some((node) => node.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.text.includes('详情'))).toBe(true);
  });

  it('marks approved triage requirements as confirmed detail payload', () => {
    const story = buildRunStory({
      session: session({
        requirementTriage: triage({
          status: 'approved',
          mustHaves: ['Flink CDC'],
          approvedAt: '2026-05-09T00:00:00Z',
        }),
      }),
      events,
      sourceFilter: 'all',
    });

    const requirements = story.graphNodes.find((node) => node.id === 'requirements');

    expect(requirements?.detailKind).toBe('requirements');
    expect(requirements?.detailPayload).toMatchObject({
      kind: 'requirements',
      triageStatus: 'confirmed',
      criteria: expect.objectContaining({ mustHaves: ['Flink CDC'] }),
    });
  });

  it('marks draft triage requirements as draft, not confirmed', () => {
    const story = buildRunStory({
      session: session({
        requirementTriage: triage({
          status: 'draft',
          mustHaves: ['Kafka'],
          approvedAt: null,
        }),
      }),
      events: [],
      sourceFilter: 'all',
    });

    const requirements = story.graphNodes.find((node) => node.id === 'requirements');

    expect(requirements?.detailPayload).toMatchObject({
      kind: 'requirements',
      triageStatus: 'draft',
      criteria: expect.objectContaining({ mustHaves: ['Kafka'] }),
    });
    expect(requirements?.detailPayload).not.toMatchObject({ triageStatus: 'confirmed' });
  });

  it('includes reflection summary, rationale, and next direction on CTS reflection nodes', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 1,
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          eventName: 'runtime_round_completed',
          payload: {
            round_no: 1,
            payload: {
              executedQueries: [{ queryTerms: ['Flink CDC', 'Kafka'] }],
              rawCandidateCount: 4,
              uniqueNewCount: 2,
              newlyScoredCount: 2,
              fitCount: 1,
              notFitCount: 1,
              reflectionSummary: 'Kafka narrows too much.',
              reflectionRationale: 'Strong Flink candidates omit Kafka.',
              nextDirection: 'Try CDC and realtime ETL terms.',
            },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    const reflection = story.graphNodes.find((node) => node.id === 'cts-round-1-reflect');

    expect(reflection?.detailPayload).toMatchObject({
      kind: 'reflection',
      summary: 'Kafka narrows too much.',
      rationale: 'Strong Flink candidates omit Kafka.',
      nextDirection: 'Try CDC and realtime ETL terms.',
    });
  });

  it('populates Liepin candidate and detail-open metadata from safe API inputs', () => {
    const story = buildRunStory({
      session: session(),
      events,
      candidateReviewItems: [candidateReviewItem()],
      detailOpenRequests: [detailOpenRequest()],
      sourceFilter: 'liepin',
    });

    const candidates = story.graphNodes.find((node) => node.id === 'liepin-card-candidates');
    const detailApproval = story.graphNodes.find((node) => node.id === 'liepin-detail-approval');

    expect(candidates?.candidateReviewItemIds).toEqual(['review-liepin-1']);
    expect(candidates?.candidateEvidenceRefs).toEqual([
      {
        evidenceId: 'evidence-liepin-1',
        reviewItemId: 'review-liepin-1',
        sourceRunId: 'src-liepin',
        sourceKind: 'liepin',
        evidenceLevel: 'detail',
      },
    ]);
    expect(candidates?.detailPayload).toMatchObject({
      kind: 'liepinCardCandidates',
      candidateReviewItemIds: ['review-liepin-1'],
      bestScore: 93,
    });
    expect(detailApproval?.detailOpenRequestIds).toEqual(['detail-request-1']);
    expect(detailApproval?.detailPayload).toMatchObject({
      kind: 'liepinDetailApproval',
      requestIds: ['detail-request-1'],
      requestSummaries: ['Ada Chen · approved · leased'],
      budgetText: 'approved · leased',
    });
  });

  it('builds Liepin detail approval from API-only detail requests without detail events', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 4,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'source_run_started',
          payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' },
        }),
      ],
      candidateReviewItems: [candidateReviewItem()],
      detailOpenRequests: [detailOpenRequest()],
      sourceFilter: 'liepin',
    });

    const detailApproval = story.graphNodes.find((node) => node.id === 'liepin-detail-approval');
    const detailLog = story.logEntries.find((entry) => entry.id === 'liepin-detail-log');

    expect(detailApproval?.detailOpenRequestIds).toEqual(['detail-request-1']);
    expect(detailLog).toMatchObject({
      at: 4,
      relatedNodeId: 'liepin-detail-approval',
    });
  });

  it('scores Liepin candidates when Liepin evidence is not the first evidence item', () => {
    const mixedSourceCandidate = candidateReviewItem({
      aggregateScore: null,
      evidence: [
        {
          evidenceId: 'evidence-cts-1',
          sourceRunId: 'src-cts',
          sourceKind: 'cts',
          evidenceLevel: 'card',
          score: 51,
          fitBucket: 'maybe',
          matchedMustHaves: [],
          matchedPreferences: [],
          missingRisks: [],
          strengths: [],
          weaknesses: [],
          createdAt: '2026-05-09T00:00:05Z',
        },
        {
          evidenceId: 'evidence-liepin-1',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'detail',
          score: 88,
          fitBucket: 'fit',
          matchedMustHaves: ['Flink CDC'],
          matchedPreferences: ['data platform'],
          missingRisks: [],
          strengths: ['streaming systems'],
          weaknesses: [],
          createdAt: '2026-05-09T00:00:06Z',
        },
      ],
    });

    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 4,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'source_run_started',
          payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' },
        }),
      ],
      candidateReviewItems: [mixedSourceCandidate],
      sourceFilter: 'liepin',
    });

    const candidates = story.graphNodes.find((node) => node.id === 'liepin-card-candidates');

    expect(candidates).toMatchObject({
      label: '候选人初筛 · 1 人',
      detail: 'AI 简介判断最高 88 分',
    });
    expect(candidates?.detailPayload).toMatchObject({
      kind: 'liepinCardCandidates',
      candidateReviewItemIds: ['review-liepin-1'],
      bestScore: 88,
    });
  });

  it('uses the strongest Liepin evidence score for Liepin lane scoring', () => {
    const multiEvidenceCandidate = candidateReviewItem({
      aggregateScore: null,
      evidence: [
        {
          evidenceId: 'evidence-liepin-card',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'card',
          score: 60,
          fitBucket: 'maybe',
          matchedMustHaves: [],
          matchedPreferences: [],
          missingRisks: [],
          strengths: [],
          weaknesses: [],
          createdAt: '2026-05-09T00:00:05Z',
        },
        {
          evidenceId: 'evidence-liepin-detail',
          sourceRunId: 'src-liepin',
          sourceKind: 'liepin',
          evidenceLevel: 'detail',
          score: 92,
          fitBucket: 'fit',
          matchedMustHaves: ['Flink CDC'],
          matchedPreferences: ['data platform'],
          missingRisks: [],
          strengths: ['streaming systems'],
          weaknesses: [],
          createdAt: '2026-05-09T00:00:06Z',
        },
      ],
    });

    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 4,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'source_run_started',
          payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' },
        }),
      ],
      candidateReviewItems: [multiEvidenceCandidate],
      sourceFilter: 'liepin',
    });

    const candidates = story.graphNodes.find((node) => node.id === 'liepin-card-candidates');

    expect(candidates?.detail).toBe('AI 简介判断最高 92 分');
    expect(candidates?.detailPayload).toMatchObject({
      kind: 'liepinCardCandidates',
      bestScore: 92,
    });
  });

  it('keeps Liepin detail requests when candidate queue data is missing', () => {
    const liepinStory = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 4,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'source_run_started',
          payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' },
        }),
      ],
      candidateReviewItems: [],
      detailOpenRequests: [detailOpenRequest()],
      sourceFilter: 'liepin',
    });
    const ctsStory = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 4,
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          eventName: 'source_run_started',
          payload: { sourceRunId: 'src-cts', sourceKind: 'cts' },
        }),
      ],
      candidateReviewItems: [],
      detailOpenRequests: [detailOpenRequest()],
      sourceFilter: 'cts',
    });

    expect(liepinStory.graphNodes.find((node) => node.id === 'liepin-detail-approval')).toMatchObject({
      detailOpenRequestIds: ['detail-request-1'],
    });
    expect(ctsStory.graphNodes.some((node) => node.detailOpenRequestIds?.includes('detail-request-1'))).toBe(false);
  });
});
