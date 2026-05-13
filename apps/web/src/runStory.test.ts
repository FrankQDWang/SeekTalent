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
    sourceRunId: overrides.sourceRunId === undefined ? 'src-cts' : overrides.sourceRunId,
    sourceKind: overrides.sourceKind === undefined ? 'cts' : overrides.sourceKind,
    eventName: overrides.eventName ?? 'source_run_started',
    payload: overrides.payload ?? {},
    createdAt: overrides.createdAt ?? `2026-05-09T00:00:${String(overrides.globalSeq ?? 1).padStart(2, '0')}Z`,
  };
}

function noteEvent(text: string, overrides: Partial<WorkbenchEvent> = {}): WorkbenchEvent {
  const globalSeq = overrides.globalSeq ?? 50;
  return event({
    ...overrides,
    globalSeq,
    eventName: 'workbench_note_created',
    payload: {
      text,
      eventSeq: globalSeq,
      ...(overrides.payload ?? {}),
    },
  });
}

function candidateReviewItem(overrides: Partial<WorkbenchCandidateReviewItem> = {}): WorkbenchCandidateReviewItem {
  return {
    reviewItemId: 'review-liepin-1',
    sessionId: 'session-1',
    graphCandidateId: null,
    canExpandResume: false,
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
  it('shows a requirement node while requirement extraction is still running', () => {
    const story = buildRunStory({
      session: session({
        requirementTriage: triage({
          status: 'draft',
          mustHaves: [],
          niceToHaves: [],
          synonyms: [],
          seniorityFilters: [],
          exclusions: [],
          generatedQueryHints: [],
          approvedAt: null,
        }),
      }),
      events: [
        event({
          globalSeq: 2,
          sourceKind: null,
          sourceRunId: null,
          eventName: 'runtime_requirements_started',
          payload: { message: '正在分析岗位标题、JD 和 notes。', roundNo: null, stage: 'requirements' },
        }),
      ],
    });

    expect(story.graphNodes.find((node) => node.id === 'requirements')).toMatchObject({
      label: '需求拆解',
      detail: '正在拆解岗位需求',
    });
    expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'job', to: 'requirements' }));
  });

  it('builds separate CTS and Liepin lanes in the all-sources story', () => {
    const story = buildRunStory({ session: session(), events, sourceFilter: 'all' });

    expect(story.graphNodes.some((node) => node.lane === 'cts' && node.label.includes('第 1 轮关键词'))).toBe(true);
    expect(story.graphNodes.some((node) => node.lane === 'liepin' && node.label.includes('猎聘简介抓取'))).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === 'CTS 检索')).toBe(true);
    expect(story.graphEdges.some((edge) => edge.label === '猎聘简介抓取')).toBe(true);
    expect(story.graphNodes.find((node) => node.id === 'final-shortlist')?.detail).toBe('最高 91 分');
  });

  it('projects the finalizer report into the final shortlist node', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        ...events,
        event({
          globalSeq: 8,
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          eventName: 'runtime_finalizer_completed',
          payload: {
            message: '本次短名单共 2 位候选人，优先推荐实时数据平台经验最完整的人选。',
            payload: { stop_reason: 'max_rounds' },
          },
        }),
      ],
      sourceFilter: 'all',
    });

    expect(story.graphNodes.find((node) => node.id === 'final-shortlist')?.detailPayload).toMatchObject({
      kind: 'aggregation',
      finalReport: '本次短名单共 2 位候选人，优先推荐实时数据平台经验最完整的人选。',
      stopReason: 'max_rounds',
    });
  });

  it('keeps selected queued or blocked sources visible in the all-sources graph', () => {
    const story = buildRunStory({
      session: session({
        requirementTriage: triage({ mustHaves: ['Flink CDC'] }),
        sourceCards: [
          { ...session().sourceCards[0], status: 'queued', cardsScannedCount: 0, uniqueCandidatesCount: 0 },
          { ...session().sourceCards[1], status: 'blocked', cardsScannedCount: 0, uniqueCandidatesCount: 0 },
        ],
      }),
      events: [],
      sourceFilter: 'all',
    });

    expect(story.graphNodes.some((node) => node.id === 'cts-source-start')).toBe(true);
    expect(story.graphNodes.some((node) => node.id === 'liepin-source-start')).toBe(true);
  });

  it('filters graph nodes and workbench notes by source', () => {
    const noteEvents = [
      ...events,
      noteEvent('CTS note', { globalSeq: 50, sourceKind: 'cts', sourceRunId: 'src-cts' }),
      noteEvent('Liepin detail note', { globalSeq: 51, sourceKind: 'liepin', sourceRunId: 'src-liepin' }),
    ];
    const ctsStory = buildRunStory({ session: session(), events: noteEvents, sourceFilter: 'cts' });
    const liepinStory = buildRunStory({ session: session(), events: noteEvents, sourceFilter: 'liepin' });

    expect(ctsStory.graphNodes.some((node) => node.sourceKind === 'liepin')).toBe(false);
    expect(ctsStory.logEntries.some((entry) => entry.sourceKind === 'liepin')).toBe(false);
    expect(liepinStory.graphNodes.some((node) => node.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.sourceKind === 'cts')).toBe(false);
    expect(liepinStory.logEntries.some((entry) => entry.text.includes('detail'))).toBe(true);
  });

  it('writes running notes only from workbench_note_created events', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        ...events,
        noteEvent('第 1 轮检索完成，准备复盘。', { globalSeq: 50, payload: { eventSeq: 12 } }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphNodes.some((node) => node.id === 'cts-round-1-reflect')).toBe(true);
    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({
      id: 'workbench-note-12',
      at: 12,
      text: '第 1 轮检索完成，准备复盘。',
      relatedNodeId: undefined,
    });
  });

  it('keeps running notes non-empty before the first note writer event', () => {
    const currentSession = session({
      requirementTriage: triage({
        status: 'approved',
        mustHaves: ['Flink CDC'],
        approvedAt: '2026-05-09T00:00:00Z',
      }),
      sourceRuns: session().sourceRuns.map((run) => ({ ...run, status: 'queued' })),
      sourceCards: session().sourceCards.map((card) => ({ ...card, status: 'queued' })),
    });

    const story = buildRunStory({
      session: currentSession,
      events: [event({ eventName: 'source_run_queued', sourceKind: 'cts', sourceRunId: 'src-cts' })],
      sourceFilter: 'all',
    });

    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({
      id: 'initial-business-note',
      text: '检索已启动，正在根据已确认标准推进所选渠道。',
      statusHint: 'waiting',
      noteKind: 'waiting',
    });
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
      session: session({ requirementTriage: triage({ mustHaves: ['Flink CDC'] }) }),
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
      session: session({ requirementTriage: triage({ mustHaves: ['Flink CDC'] }) }),
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

    expect(detailApproval?.detailOpenRequestIds).toEqual(['detail-request-1']);
    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({ statusHint: 'waiting', noteKind: 'waiting' });
  });

  it('dedupes Liepin detail approval counts between events and current requests', () => {
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
        event({
          globalSeq: 5,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'liepin_detail_open_requested',
          payload: { requestId: 'detail-request-1', reviewItemId: 'review-liepin-1' },
        }),
        event({
          globalSeq: 6,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'liepin_detail_open_leased',
          payload: { requestId: 'detail-request-1', reviewItemId: 'review-liepin-1' },
        }),
      ],
      candidateReviewItems: [candidateReviewItem()],
      detailOpenRequests: [detailOpenRequest()],
      sourceFilter: 'liepin',
    });

    const detailApproval = story.graphNodes.find((node) => node.id === 'liepin-detail-approval');

    expect(detailApproval?.kind).toBe('详情审批');
    expect(detailApproval?.label).toBe('详情审批 · 1 个');
    expect(detailApproval?.detail).toBe('已预留 1 · 阻塞 0');
    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({ statusHint: 'waiting', noteKind: 'waiting' });
  });

  it('dedupes event-only Liepin detail approval fallback counts by request id', () => {
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
        event({
          globalSeq: 5,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'liepin_detail_open_requested',
          payload: { requestId: 'detail-request-event-1', reviewItemId: 'review-liepin-1' },
        }),
        event({
          globalSeq: 6,
          sourceKind: 'liepin',
          sourceRunId: 'src-liepin',
          eventName: 'liepin_detail_open_leased',
          payload: { requestId: 'detail-request-event-1', reviewItemId: 'review-liepin-1' },
        }),
      ],
      candidateReviewItems: [candidateReviewItem()],
      detailOpenRequests: [],
      sourceFilter: 'liepin',
    });

    const detailApproval = story.graphNodes.find((node) => node.id === 'liepin-detail-approval');

    expect(detailApproval?.label).toBe('详情审批 · 1 个');
    expect(detailApproval?.detail).toBe('已预留 1 · 阻塞 0');
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

  it('keeps individual candidates out of the strategy graph and links them through aggregate nodes', () => {
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
      candidateReviewItems: [
        candidateReviewItem({ reviewItemId: 'review-liepin-1', displayName: 'Ada Chen' }),
        candidateReviewItem({ reviewItemId: 'review-liepin-2', displayName: 'Ben Lin' }),
      ],
      sourceFilter: 'liepin',
    });

    expect(story.graphNodes.map((node) => node.label)).not.toContain('Ada Chen');
    expect(story.graphNodes.map((node) => node.label)).not.toContain('Ben Lin');
    expect(story.graphNodes.find((node) => node.id === 'liepin-card-candidates')).toMatchObject({
      label: '候选人初筛 · 2 人',
      candidateReviewItemIds: ['review-liepin-1', 'review-liepin-2'],
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

  it('builds CTS rounds from split runtime search, scoring, reflection, requirements, and completion events', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 1,
          eventName: 'runtime_requirements_completed',
          payload: {
            type: 'requirements_completed',
            payload: {
              must_have_capabilities: ['Flink CDC'],
              preferred_capabilities: ['streaming platform'],
              search_terms: ['streaming data'],
            },
          },
        }),
        event({
          globalSeq: 4,
          eventName: 'runtime_scoring_completed',
          payload: {
            type: 'scoring_completed',
            roundNo: 1,
            payload: {
              stage: 'scoring',
              scored_count: 9,
              fit_count: 2,
              not_fit_count: 7,
            },
          },
        }),
        event({
          globalSeq: 2,
          eventName: 'runtime_search_completed',
          payload: {
            type: 'search_completed',
            roundNo: 1,
            payload: {
              stage: 'search',
              query_terms: ['Flink CDC', 'Kafka'],
              executed_queries: [
                {
                  query_role: 'exploit',
                  lane_type: 'exploit',
                  query_terms: ['Flink CDC', 'Kafka'],
                  keyword_query: '"Flink CDC" Kafka',
                  query_instance_id: 'query-1',
                  query_fingerprint: 'fingerprint-1',
                },
                {
                  query_role: 'explore',
                  lane_type: 'generic_explore',
                  query_terms: ['streaming ETL'],
                  keyword_query: '"streaming ETL"',
                  query_instance_id: 'query-2',
                  query_fingerprint: 'fingerprint-2',
                },
              ],
              raw_candidate_count: 14,
              unique_new_count: 9,
              recall_counts: { exploit: 10, generic_explore: 4 },
            },
          },
        }),
        event({
          globalSeq: 3,
          eventName: 'runtime_round_completed',
          payload: {
            type: 'round_completed',
            roundNo: 1,
            payload: {
              reflection_summary: 'Kafka narrows the pool.',
              reflection_rationale: 'Strong Flink candidates may not mention Kafka.',
              next_direction: 'Try CDC and realtime ETL terms.',
            },
          },
        }),
        event({
          globalSeq: 5,
          eventName: 'runtime_run_completed',
          payload: { type: 'run_completed', payload: { rounds_executed: 1 } },
        }),
      ],
      sourceFilter: 'cts',
    });

    const query = story.graphNodes.find((node) => node.id === 'cts-round-1-query');
    const result = story.graphNodes.find((node) => node.id === 'cts-round-1-result');
    const score = story.graphNodes.find((node) => node.id === 'cts-round-1-score');
    const reflection = story.graphNodes.find((node) => node.id === 'cts-round-1-reflect');

    expect(story.criteria).toMatchObject({
      mustHaves: ['Flink CDC'],
      niceToHaves: ['streaming platform'],
      generatedQueryHints: ['streaming data'],
    });
    expect(query?.detail).toBe('Flink CDC + Kafka / streaming ETL');
    expect(query?.detailPayload).toMatchObject({
      kind: 'ctsRoundQuery',
      roundNo: 1,
      queryTerms: ['Flink CDC', 'Kafka', 'streaming ETL'],
      executedQueries: [
        {
          query_role: 'exploit',
          lane_type: 'exploit',
          query_instance_id: 'query-1',
          query_fingerprint: 'fingerprint-1',
        },
        {
          query_role: 'explore',
          lane_type: 'generic_explore',
          query_instance_id: 'query-2',
          query_fingerprint: 'fingerprint-2',
        },
      ],
    });
    expect(result?.label).toBe('搜到 14 人 · 新增 9 人');
    expect(result?.detailPayload).toMatchObject({
      kind: 'ctsRoundResults',
      rawCandidateCount: 14,
      uniqueNewCount: 9,
      recallCounts: { exploit: 10, generic_explore: 4 },
    });
    expect(score).toMatchObject({
      label: '评分：fit 2 / not_fit 7',
      detail: '9 人进入评分',
    });
    expect(score?.detailPayload).toMatchObject({
      kind: 'ctsRoundScoring',
      scoredCount: 9,
      fitCount: 2,
      notFitCount: 7,
    });
    expect(reflection?.detailPayload).toMatchObject({
      kind: 'reflection',
      summary: 'Kafka narrows the pool.',
      rationale: 'Strong Flink candidates may not mention Kafka.',
      nextDirection: 'Try CDC and realtime ETL terms.',
    });
    expect(story.completionText).toBe('检索完成 · 候选人进入短名单');
  });

  it('shows CTS round progress from started runtime events before completion events arrive', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 1,
          eventName: 'runtime_controller_started',
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          payload: {
            type: 'controller_started',
            roundNo: 1,
            payload: { stage: 'controller' },
          },
        }),
        event({
          globalSeq: 2,
          eventName: 'runtime_search_started',
          sourceKind: 'cts',
          sourceRunId: 'src-cts',
          payload: {
            type: 'search_started',
            roundNo: 1,
            payload: {
              stage: 'search',
              keyword_query: '数据开发 ETL',
              query_terms: ['数据开发', 'ETL'],
              planned_queries: [
                {
                  query_role: 'exploit',
                  lane_type: 'exploit',
                  query_terms: ['数据开发', 'ETL'],
                  keyword_query: '数据开发 ETL',
                  query_instance_id: 'query-1',
                  query_fingerprint: 'fingerprint-1',
                },
              ],
            },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphNodes.find((node) => node.id === 'cts-round-1-query')).toMatchObject({
      kind: '检索',
      label: '第 1 轮检索中',
      detail: '数据开发 + ETL',
    });
    expect(story.graphNodes.some((node) => node.id === 'cts-round-1-result')).toBe(false);
    expect(story.graphNodes.some((node) => node.id === 'cts-round-1-score')).toBe(false);
  });

  it('dedupes duplicate split events and legacy composite round events by source run and round number', () => {
    const splitSearch = event({
      globalSeq: 3,
      eventName: 'runtime_search_completed',
      payload: {
        type: 'search_completed',
        roundNo: 1,
        payload: {
          executed_queries: [{ query_role: 'exploit', lane_type: 'exploit', query_terms: ['Flink CDC'] }],
          raw_candidate_count: 8,
          unique_new_count: 5,
        },
      },
    });
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 2,
          eventName: 'runtime_round_completed',
          payload: {
            type: 'round_completed',
            roundNo: 1,
            payload: {
              executed_queries: [{ query_role: 'exploit', lane_type: 'exploit', query_terms: ['Flink CDC'] }],
              raw_candidate_count: 8,
              unique_new_count: 5,
              newly_scored_count: 5,
              fit_count: 1,
              not_fit_count: 4,
              reflection_summary: 'Keep the main lane.',
            },
          },
        }),
        splitSearch,
        { ...splitSearch, globalSeq: 4 },
        event({
          globalSeq: 5,
          eventName: 'runtime_scoring_completed',
          payload: {
            type: 'scoring_completed',
            roundNo: 1,
            payload: { newly_scored_count: 5, fit_count: 1, not_fit_count: 4 },
          },
        }),
        event({
          globalSeq: 6,
          eventName: 'runtime_scoring_completed',
          payload: {
            type: 'scoring_completed',
            roundNo: 1,
            payload: { newly_scored_count: 5, fit_count: 1, not_fit_count: 4 },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphNodes.filter((node) => node.id === 'cts-round-1-query')).toHaveLength(1);
    expect(story.graphNodes.filter((node) => node.id === 'cts-round-1-result')).toHaveLength(1);
    expect(story.graphNodes.filter((node) => node.id === 'cts-round-1-score')).toHaveLength(1);
    expect(story.graphNodes.filter((node) => node.id === 'cts-round-1-reflect')).toHaveLength(1);
    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({ statusHint: 'completed' });
    expect(story.graphNodes.find((node) => node.id === 'cts-round-1-score')?.label).toBe('评分：fit 1 / not_fit 4');
  });

  it('connects later CTS rounds to both requirements and the previous reflection', () => {
    const story = buildRunStory({
      session: session({ requirementTriage: triage({ mustHaves: ['Flink CDC'] }) }),
      events: [
        event({
          globalSeq: 2,
          eventName: 'runtime_round_completed',
          payload: {
            type: 'round_completed',
            roundNo: 1,
            payload: {
              executed_queries: [{ query_role: 'exploit', lane_type: 'exploit', query_terms: ['Kafka'] }],
              raw_candidate_count: 8,
              unique_new_count: 5,
              newly_scored_count: 5,
              fit_count: 1,
              not_fit_count: 4,
              reflection_summary: 'Kafka-only is too narrow.',
            },
          },
        }),
        event({
          globalSeq: 3,
          eventName: 'runtime_round_completed',
          payload: {
            type: 'round_completed',
            roundNo: 2,
            payload: {
              executed_queries: [{ query_role: 'explore', lane_type: 'generic_explore', query_terms: ['Flink CDC'] }],
              raw_candidate_count: 12,
              unique_new_count: 7,
              newly_scored_count: 7,
              fit_count: 2,
              not_fit_count: 5,
            },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphEdges).toContainEqual(
      expect.objectContaining({ from: 'requirements', to: 'cts-round-2-query', label: '需求约束' }),
    );
    expect(story.graphEdges).toContainEqual(
      expect.objectContaining({ from: 'cts-round-1-reflect', to: 'cts-round-2-query', label: '反思迭代' }),
    );
  });

  it('keeps search and result nodes when split runtime scoring has not arrived yet', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 2,
          eventName: 'runtime_search_completed',
          payload: {
            type: 'search_completed',
            roundNo: 1,
            payload: {
              executed_queries: [{ query_role: 'exploit', lane_type: 'exploit', query_terms: ['Flink CDC'] }],
              raw_candidate_count: 6,
              unique_new_count: 3,
            },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphNodes.find((node) => node.id === 'cts-round-1-query')?.detail).toBe('Flink CDC');
    expect(story.graphNodes.find((node) => node.id === 'cts-round-1-result')?.label).toBe('搜到 6 人 · 新增 3 人');
    expect(story.graphNodes.find((node) => node.id === 'cts-round-1-score')).toBeUndefined();
  });

  it('does not show raw runtime events as running notes', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        event({
          globalSeq: 2,
          eventName: 'runtime_search_completed',
          payload: {
            type: 'search_completed',
            roundNo: 1,
            payload: {
              executed_queries: [{ query_role: 'exploit', lane_type: 'exploit', query_terms: ['Flink CDC'] }],
              raw_candidate_count: 6,
              unique_new_count: 3,
            },
          },
        }),
        event({
          globalSeq: 3,
          eventName: 'runtime_internal_debug_completed',
          payload: {
            type: 'internal_debug_completed',
            message: 'runtime_internal_debug_completed should not be shown',
            roundNo: 1,
            payload: { raw_event_name: 'runtime_internal_debug_completed' },
          },
        }),
      ],
      sourceFilter: 'cts',
    });

    expect(story.graphNodes.some((node) => node.id === 'cts-round-1-result')).toBe(true);
    expect(story.logEntries.map((entry) => entry.text).join('\n')).not.toContain('runtime_internal_debug_completed');
  });

  it('dedupes workbench notes by payload sequence and keeps waiting metadata', () => {
    const story = buildRunStory({
      session: session(),
      events: [
        noteEvent('正在等待候选人详情。', {
          globalSeq: 20,
          payload: { eventSeq: 9, noteKind: 'waiting', statusHint: 'waiting' },
        }),
        noteEvent('duplicate should not render', {
          globalSeq: 21,
          payload: { eventSeq: 9 },
        }),
      ],
      sourceFilter: 'all',
    });

    expect(story.logEntries).toHaveLength(1);
    expect(story.logEntries[0]).toMatchObject({
      id: 'workbench-note-9',
      text: '正在等待候选人详情。',
      noteKind: 'waiting',
      statusHint: 'waiting',
      relatedNodeId: undefined,
    });
  });
});
