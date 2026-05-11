import type {
  RecruiterCandidateEvidenceRef,
  RecruiterGraphEdge,
  RecruiterGraphNode,
  RecruiterLogEntry,
} from './recruiterAnimation';
import type {
  SourceKind,
  WorkbenchCandidateReviewItem,
  WorkbenchDetailOpenRequest,
  WorkbenchEvent,
  WorkbenchRequirementTriage,
  WorkbenchRequirementTriageInput,
  WorkbenchSession,
} from './types';

export type SourceFilter = SourceKind | 'all';

export type RunStory = {
  criteria: WorkbenchRequirementTriageInput;
  graphNodes: RecruiterGraphNode[];
  graphEdges: RecruiterGraphEdge[];
  logEntries: RecruiterLogEntry[];
  nodeTotal: number;
  completionText: string | null;
};

export type BuildRunStoryInput = {
  session: WorkbenchSession;
  events: WorkbenchEvent[];
  candidateReviewItems?: WorkbenchCandidateReviewItem[];
  detailOpenRequests?: WorkbenchDetailOpenRequest[];
  sourceFilter?: SourceFilter;
};

type RuntimeEventData = {
  event: WorkbenchEvent;
  payload: Record<string, unknown>;
  roundNo: number | null;
  message: string;
};

type RoundSummary = {
  eventSeq: number;
  eventId: string;
  sourceRunId: string | null;
  roundNo: number;
  queryTerms: string[];
  queryLabel: string;
  rawCandidateCount: number;
  uniqueNewCount: number;
  newlyScoredCount: number;
  fitCount: number;
  notFitCount: number;
  reflectionSummary: string;
  reflectionRationale: string;
  nextDirection: string;
};

type CandidateScore = {
  reviewItemId: string;
  score: number;
  sourceKind: SourceKind | null;
  eventSeq: number;
};

const emptyCriteria: WorkbenchRequirementTriageInput = {
  mustHaves: [],
  niceToHaves: [],
  synonyms: [],
  seniorityFilters: [],
  exclusions: [],
  generatedQueryHints: [],
};

const sourceLabels: Record<SourceKind, string> = {
  cts: 'CTS',
  liepin: 'Liepin',
};

export function buildRunStory(input: BuildRunStoryInput): RunStory {
  const {
    session,
    events,
    candidateReviewItems = [],
    detailOpenRequests = [],
    sourceFilter = 'all',
  } = input;
  const scopedEvents = scopeEvents(events, sourceFilter);
  const allRuntimeEvents = events
    .filter((event) => event.eventName.startsWith('runtime_'))
    .map(runtimeEventData)
    .filter(Boolean) as RuntimeEventData[];
  const requirements = allRuntimeEvents.find((item) => item.event.eventName === 'runtime_requirements_completed');
  const runtimeCriteria = criteriaFromRequirements(requirements);
  const triageCriteria = criteriaFromTriage(session.requirementTriage);
  const triageHasInput = hasTriageInput(triageCriteria);
  const runtimeHasInput = hasTriageInput(runtimeCriteria);
  const criteria = triageHasInput ? triageCriteria : runtimeCriteria;
  const sourceKinds = selectedSourceKinds(session, scopedEvents, sourceFilter);
  const scopedCandidateReviewItems =
    sourceFilter === 'all'
      ? candidateReviewItems
      : scopeCandidateReviewItems(candidateReviewItems, sourceFilter);
  const candidateScores = candidateScoresFromInputs(scopedEvents, scopedCandidateReviewItems);
  const hasSourceEvents = scopedEvents.some((event) => event.sourceKind !== null);
  const hasCompletion = scopedEvents.some((event) =>
    event.eventName === 'source_run_completed' || event.eventName === 'runtime_run_completed',
  );

  if (!requirements && !triageHasInput && !hasSourceEvents && candidateScores.length === 0) {
    return { criteria: emptyCriteria, graphNodes: [], graphEdges: [], logEntries: [], nodeTotal: 27, completionText: null };
  }

  const graphNodes: RecruiterGraphNode[] = [
    {
      id: 'job',
      at: 0,
      kind: '岗位',
      label: `岗位需求 / ${session.jobTitle}`,
      detail: session.sourceCards.length > 1 ? '多源检索 session' : '单源检索 session',
      x: 10,
      y: 50,
      tone: 'neutral',
      sourceKind: 'all',
      sourceLabel: 'All sources',
      lane: 'shared',
      detailKind: 'job',
      detailPayload: {
        kind: 'job',
        sessionId: session.sessionId,
        jobTitle: session.jobTitle,
        jdText: session.jdText,
        notes: session.notes,
        sourceKinds: session.sourceCards.map((card) => card.sourceKind),
      },
      eventIds: [],
      sourceRunId: null,
      candidateReviewItemIds: [],
      candidateEvidenceRefs: [],
      detailOpenRequestIds: [],
    },
  ];
  const graphEdges: RecruiterGraphEdge[] = [];
  const logEntries: RecruiterLogEntry[] = [];

  if (requirements || triageHasInput || runtimeHasInput) {
    const triageStatus =
      triageHasInput && session.requirementTriage.status === 'approved'
        ? 'confirmed'
        : triageHasInput
          ? 'draft'
          : 'runtime';
    graphNodes.push({
      id: 'requirements',
      at: 1,
      kind: '拆解',
      label: '需求拆解',
      detail: firstNonEmpty([criteria.mustHaves, criteria.niceToHaves]) || '已解析岗位约束',
      x: 24,
      y: 50,
      tone: 'blue',
      sourceKind: 'all',
      sourceLabel: 'All sources',
      lane: 'shared',
      detailKind: 'requirements',
      detailPayload: {
        kind: 'requirements',
        triageStatus,
        criteria,
        runtimeCriteria,
        approvedAt: session.requirementTriage.approvedAt,
      },
      eventIds: requirements ? [eventId(requirements.event)] : [],
      sourceRunId: null,
      candidateReviewItemIds: [],
      candidateEvidenceRefs: [],
      detailOpenRequestIds: [],
    });
    graphEdges.push({ from: 'job', to: 'requirements', tone: 'blue', label: '提取约束' });
    logEntries.push({
      id: 'requirements',
      at: requirements?.event.globalSeq ?? 1,
      tag: 'THINK',
      text: `解析岗位需求：${listText(criteria.mustHaves.slice(0, 3)) || session.jobTitle}`,
      sourceKind: 'all',
      sourceLabel: 'All sources',
      lane: 'shared',
      relatedNodeId: 'requirements',
    });
  }

  const anchor = graphNodes.some((node) => node.id === 'requirements') ? 'requirements' : 'job';
  const allMode = sourceFilter === 'all' && sourceKinds.length > 1;
  const sourceTerminalNodes: string[] = [];

  for (const sourceKind of sourceKinds) {
    const sourceEvents = scopedEvents.filter((event) => event.sourceKind === sourceKind);
    if (sourceKind === 'cts') {
      const terminalNode = appendCtsLane({
        allMode,
        anchor,
        events: sourceEvents,
        graphEdges,
        graphNodes,
        logEntries,
        runtimeEvents: allRuntimeEvents.filter((item) => item.event.sourceKind === 'cts'),
        session,
      });
      if (terminalNode) {
        sourceTerminalNodes.push(terminalNode);
      }
      continue;
    }
    const terminalNode = appendLiepinLane({
      allMode,
      anchor,
      events: sourceEvents,
      graphEdges,
      graphNodes,
      logEntries,
      candidateReviewItems: scopeCandidateReviewItems(candidateReviewItems, sourceKind),
      detailOpenRequests,
      session,
    });
    if (terminalNode) {
      sourceTerminalNodes.push(terminalNode);
    }
  }

  const finalNodeId = appendFinalNode({
    candidateScores,
    graphEdges,
    graphNodes,
    logEntries,
    sourceTerminalNodes,
    fallbackAnchor: anchor,
    hasCompletion,
  });

  const sortedLogs = logEntries.sort((left, right) => left.at - right.at || left.id.localeCompare(right.id));
  return {
    criteria,
    graphNodes,
    graphEdges,
    logEntries: sortedLogs,
    nodeTotal: Math.max(27, graphNodes.length),
    completionText: finalNodeId && (hasCompletion || candidateScores.length > 0) ? '检索完成 · 候选人进入短名单' : null,
  };
}

export function displayTriageFromStory(
  triage: WorkbenchRequirementTriage,
  criteria: WorkbenchRequirementTriageInput,
): WorkbenchRequirementTriage {
  return {
    ...triage,
    mustHaves: chooseVisibleList(triage.mustHaves, criteria.mustHaves),
    niceToHaves: chooseVisibleList(triage.niceToHaves, criteria.niceToHaves),
    synonyms: chooseVisibleList(triage.synonyms, criteria.synonyms),
    seniorityFilters: chooseVisibleList(triage.seniorityFilters, criteria.seniorityFilters),
    exclusions: chooseVisibleList(triage.exclusions, criteria.exclusions),
    generatedQueryHints: chooseVisibleList(triage.generatedQueryHints, criteria.generatedQueryHints),
  };
}

function appendCtsLane({
  allMode,
  anchor,
  events,
  graphEdges,
  graphNodes,
  logEntries,
  runtimeEvents,
  session,
}: {
  allMode: boolean;
  anchor: string;
  events: WorkbenchEvent[];
  graphEdges: RecruiterGraphEdge[];
  graphNodes: RecruiterGraphNode[];
  logEntries: RecruiterLogEntry[];
  runtimeEvents: RuntimeEventData[];
  session: WorkbenchSession;
}): string | null {
  const sourceKind: SourceKind = 'cts';
  const sourceLabel = sourceLabels[sourceKind];
  const baseY = laneBaseY(sourceKind, allMode);
  const sourceCard = session.sourceCards.find((card) => card.sourceKind === sourceKind);
  const rounds = roundSummaries(runtimeEvents);
  const started = firstEvent(events, ['source_run_started', 'source_run_queued', 'runtime_run_started']);
  if (events.length === 0 && rounds.length === 0 && !sourceCard) {
    return null;
  }

  const startId = 'cts-source-start';
  graphNodes.push({
    id: startId,
    at: graphNodes.length,
    kind: '检索',
    label: `${sourceLabel} 队列`,
    detail: sourceCard ? sourceCardDetail(sourceCard.cardsScannedCount, sourceCard.uniqueCandidatesCount) : '等待 CTS 检索',
    x: 34,
    y: baseY,
    tone: 'teal',
    sourceKind,
    sourceLabel,
    lane: sourceKind,
    detailKind: 'sourceQueue',
    detailPayload: sourceQueuePayload(sourceCard, sourceKind, started?.sourceRunId ?? sourceCard?.sourceRunId ?? null),
    eventIds: started ? [eventId(started)] : [],
    sourceRunId: started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
    candidateReviewItemIds: [],
    candidateEvidenceRefs: [],
    detailOpenRequestIds: [],
  });
  graphEdges.push({ from: anchor, to: startId, tone: 'teal', label: '进入 CTS 队列' });
  logEntries.push({
    id: 'cts-source-start-log',
    at: started?.globalSeq ?? 2,
    tag: 'PLAN',
    text: 'CTS 进入本地简历库检索队列',
    sourceKind,
    sourceLabel,
    lane: sourceKind,
    relatedNodeId: startId,
  });

  let lastNode = startId;
  for (const [index, round] of rounds.entries()) {
    const x = Math.min(44 + index * 9, 86);
    const positions = roundYPositions(sourceKind, allMode);
    const queryId = `cts-round-${String(round.roundNo)}-query`;
    const resultId = `cts-round-${String(round.roundNo)}-result`;
    const scoreId = `cts-round-${String(round.roundNo)}-score`;
    const reflectId = `cts-round-${String(round.roundNo)}-reflect`;
    graphNodes.push(
      sourceNode({
        id: queryId,
        kind: '检索',
        label: `第 ${String(round.roundNo)} 轮关键词`,
        detail: round.queryLabel || '等待关键词',
        x,
        y: positions.query,
        tone: 'teal',
        sourceKind,
        sourceLabel,
        detailKind: 'ctsRoundQuery',
        detailPayload: {
          kind: 'ctsRoundQuery',
          roundNo: round.roundNo,
          queryTerms: round.queryTerms,
          queryLabel: round.queryLabel,
        },
        eventIds: [round.eventId],
        sourceRunId: round.sourceRunId,
        candidateReviewItemIds: [],
        candidateEvidenceRefs: [],
        detailOpenRequestIds: [],
      }),
      sourceNode({
        id: resultId,
        kind: '命中',
        label: `搜到 ${String(round.rawCandidateCount)} 人 · 新增 ${String(round.uniqueNewCount)} 人`,
        detail: round.queryLabel || '检索结果',
        x,
        y: positions.result,
        tone: round.uniqueNewCount > 0 ? 'green' : 'rose',
        sourceKind,
        sourceLabel,
        detailKind: 'ctsRoundResults',
        detailPayload: {
          kind: 'ctsRoundResults',
          roundNo: round.roundNo,
          rawCandidateCount: round.rawCandidateCount,
          uniqueNewCount: round.uniqueNewCount,
        },
        eventIds: [round.eventId],
        sourceRunId: round.sourceRunId,
        candidateReviewItemIds: [],
        candidateEvidenceRefs: [],
        detailOpenRequestIds: [],
      }),
      sourceNode({
        id: scoreId,
        kind: '过滤',
        label: `评分：fit ${String(round.fitCount)} / not_fit ${String(round.notFitCount)}`,
        detail: `${String(round.newlyScoredCount)} 人进入评分`,
        x,
        y: positions.score,
        tone: round.fitCount > 0 ? 'green' : 'rose',
        sourceKind,
        sourceLabel,
        detailKind: 'ctsRoundScoring',
        detailPayload: {
          kind: 'ctsRoundScoring',
          roundNo: round.roundNo,
          newlyScoredCount: round.newlyScoredCount,
          fitCount: round.fitCount,
          notFitCount: round.notFitCount,
        },
        eventIds: [round.eventId],
        sourceRunId: round.sourceRunId,
        candidateReviewItemIds: [],
        candidateEvidenceRefs: [],
        detailOpenRequestIds: [],
      }),
      sourceNode({
        id: reflectId,
        kind: '反思',
        label: `第 ${String(round.roundNo)} 轮反思`,
        detail: clip(round.reflectionSummary || '等待下一轮判断', 70),
        x,
        y: positions.reflect,
        tone: 'violet',
        sourceKind,
        sourceLabel,
        detailKind: 'reflection',
        detailPayload: {
          kind: 'reflection',
          roundNo: round.roundNo,
          summary: round.reflectionSummary,
          rationale: round.reflectionRationale,
          nextDirection: round.nextDirection,
        },
        eventIds: [round.eventId],
        sourceRunId: round.sourceRunId,
        candidateReviewItemIds: [],
        candidateEvidenceRefs: [],
        detailOpenRequestIds: [],
      }),
    );
    graphEdges.push(
      { from: lastNode, to: queryId, tone: 'teal', label: index === 0 ? '生成关键词' : '进入下一轮' },
      { from: queryId, to: resultId, tone: 'teal', label: 'CTS 检索' },
      { from: resultId, to: scoreId, tone: 'green', label: '评分' },
      { from: scoreId, to: reflectId, tone: 'violet', label: '复盘' },
    );
    logEntries.push(
      {
        id: `${queryId}-log`,
        at: round.eventSeq,
        tag: 'PLAN',
        text: `第 ${String(round.roundNo)} 轮：${round.queryLabel || '等待关键词'}`,
        sourceKind,
        sourceLabel,
        lane: sourceKind,
        relatedNodeId: queryId,
      },
      {
        id: `${resultId}-log`,
        at: round.eventSeq + 0.1,
        tag: 'SCAN',
        text: `搜到 ${String(round.rawCandidateCount)} 人，新增 ${String(round.uniqueNewCount)} 人`,
        sourceKind,
        sourceLabel,
        lane: sourceKind,
        relatedNodeId: resultId,
      },
      {
        id: `${scoreId}-log`,
        at: round.eventSeq + 0.2,
        tag: 'HIT',
        text: `评分：fit ${String(round.fitCount)} / not_fit ${String(round.notFitCount)}`,
        sourceKind,
        sourceLabel,
        lane: sourceKind,
        relatedNodeId: scoreId,
      },
    );
    if (round.reflectionSummary) {
      logEntries.push({
        id: `${reflectId}-log`,
        at: round.eventSeq + 0.3,
        tag: 'REFLECT',
        text: `反思：${clip(round.reflectionSummary, 120)}`,
        sourceKind,
        sourceLabel,
        lane: sourceKind,
        relatedNodeId: reflectId,
      });
    }
    lastNode = reflectId;
  }

  const completed = firstEvent([...events].reverse(), ['source_run_completed', 'runtime_run_completed']);
  if (completed) {
    const runStartedAt = firstTimestamp(events, ['source_run_started', 'runtime_run_started']);
    const runCompletedAt = firstTimestamp(events, ['source_run_completed', 'runtime_run_completed'], true);
    const durationText =
      runStartedAt && runCompletedAt ? `耗时 ${formatDuration(runCompletedAt.getTime() - runStartedAt.getTime())}` : '';
    const completedRuntime = runtimeEvents.find((item) => item.event.eventName === 'runtime_run_completed');
    const completedRoundCount =
      numberValue(completedRuntime?.payload.rounds_executed) ??
      Math.max(0, ...rounds.map((round) => round.roundNo), 0);
    const completionParts = [durationText, `检索轮次 ${String(completedRoundCount)}`].filter(Boolean);
    logEntries.push({
      id: 'cts-completed-log',
      at: completed.globalSeq,
      tag: 'SYS',
      text: `CTS 检索完成${completionParts.length > 0 ? `：${completionParts.join(' · ')}` : '，候选人进入汇总排序'}`,
      sourceKind,
      sourceLabel,
      lane: sourceKind,
    });
  }
  return lastNode;
}

function appendLiepinLane({
  allMode,
  anchor,
  candidateReviewItems,
  detailOpenRequests,
  events,
  graphEdges,
  graphNodes,
  logEntries,
  session,
}: {
  allMode: boolean;
  anchor: string;
  candidateReviewItems: WorkbenchCandidateReviewItem[];
  detailOpenRequests: WorkbenchDetailOpenRequest[];
  events: WorkbenchEvent[];
  graphEdges: RecruiterGraphEdge[];
  graphNodes: RecruiterGraphNode[];
  logEntries: RecruiterLogEntry[];
  session: WorkbenchSession;
}): string | null {
  const sourceKind: SourceKind = 'liepin';
  const sourceLabel = sourceLabels[sourceKind];
  const baseY = laneBaseY(sourceKind, allMode);
  const sourceCard = session.sourceCards.find((card) => card.sourceKind === sourceKind);
  const visibleReviewItems = scopeCandidateReviewItems(candidateReviewItems, sourceKind);
  const visibleReviewItemIds = new Set(visibleReviewItems.map((item) => item.reviewItemId));
  const visibleDetailRequests = scopeDetailOpenRequests(detailOpenRequests, visibleReviewItemIds);
  const detailFields = detailRequestFields(visibleDetailRequests);
  const candidateEvidenceRefs = evidenceRefsForSource(visibleReviewItems, sourceKind);
  const safeCandidateReviewItemIds = uniqueStrings([
    ...visibleReviewItems.map((item) => item.reviewItemId),
    ...candidateEvidenceRefs.map((ref) => ref.reviewItemId),
  ]);
  if (events.length === 0 && !sourceCard) {
    return null;
  }

  const startId = 'liepin-source-start';
  const started = firstEvent(events, ['source_run_started', 'source_run_queued']);
  graphNodes.push({
    id: startId,
    at: graphNodes.length,
    kind: '检索',
    label: `${sourceLabel} 队列`,
    detail: sourceCard?.connectionStatus === 'connected' ? '账号已连接，串行抓取简介' : '等待猎聘登录',
    x: 34,
    y: baseY,
    tone: sourceCard?.connectionStatus === 'connected' ? 'teal' : 'amber',
    sourceKind,
    sourceLabel,
    lane: sourceKind,
    detailKind: 'sourceQueue',
    detailPayload: sourceQueuePayload(sourceCard, sourceKind, started?.sourceRunId ?? sourceCard?.sourceRunId ?? null),
    eventIds: started ? [eventId(started)] : [],
    sourceRunId: started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
    candidateReviewItemIds: safeCandidateReviewItemIds,
    candidateEvidenceRefs,
    detailOpenRequestIds: detailFields.detailOpenRequestIds,
  });
  graphEdges.push({ from: anchor, to: startId, tone: 'teal', label: '进入猎聘队列' });
  logEntries.push({
    id: 'liepin-source-start-log',
    at: started?.globalSeq ?? 2,
    tag: 'PLAN',
    text: '猎聘进入串行简介抓取队列',
    sourceKind,
    sourceLabel,
    lane: sourceKind,
    relatedNodeId: startId,
  });

  let lastNode = startId;
  const searchCompleted = firstEvent(events, ['liepin_card_search_completed']);
  if (searchCompleted) {
    const scanned = numberValue(searchCompleted.payload.cardsScannedCount) ?? sourceCard?.cardsScannedCount ?? 0;
    const unique = numberValue(searchCompleted.payload.uniqueCandidatesCount) ?? sourceCard?.uniqueCandidatesCount ?? 0;
    const searchId = 'liepin-card-search';
    graphNodes.push(
      sourceNode({
        id: searchId,
        kind: '检索',
        label: `猎聘简介抓取 · ${String(scanned)} 张`,
        detail: `简介合格候选人 ${String(unique)} 人`,
        x: 52,
        y: baseY - (allMode ? 8 : 11),
        tone: 'teal',
        sourceKind,
        sourceLabel,
        detailKind: 'liepinCardSearch',
        detailPayload: {
          kind: 'liepinCardSearch',
          cardsScannedCount: scanned,
          uniqueCandidatesCount: unique,
          ...detailFields,
        },
        eventIds: [eventId(searchCompleted)],
        sourceRunId: searchCompleted.sourceRunId,
        candidateReviewItemIds: safeCandidateReviewItemIds,
        candidateEvidenceRefs,
        detailOpenRequestIds: detailFields.detailOpenRequestIds,
      }),
    );
    graphEdges.push({ from: lastNode, to: searchId, tone: 'teal', label: '猎聘简介抓取' });
    logEntries.push({
      id: 'liepin-card-search-log',
      at: searchCompleted.globalSeq,
      tag: 'SCAN',
      text: `抓取简介 ${String(scanned)} 张，命中 ${String(unique)} 位候选人`,
      sourceKind,
      sourceLabel,
      lane: sourceKind,
      relatedNodeId: searchId,
    });
    lastNode = searchId;
  }

  const liepinScores = candidateScoresFromInputs(events, visibleReviewItems, sourceKind).filter(
    (candidate) => candidate.sourceKind === sourceKind,
  );
  if (liepinScores.length > 0) {
    const highScore = bestScore(liepinScores);
    const candidateId = 'liepin-card-candidates';
    const candidateReviewItemIds = uniqueStrings([
      ...safeCandidateReviewItemIds,
      ...liepinScores.map((candidate) => candidate.reviewItemId),
    ]);
    graphNodes.push(
      sourceNode({
        id: candidateId,
        kind: '命中',
        label: `候选人初筛 · ${String(liepinScores.length)} 人`,
        detail: highScore !== null ? `AI 简介判断最高 ${String(highScore)} 分` : 'AI 简介判断',
        x: 66,
        y: baseY,
        tone: 'green',
        sourceKind,
        sourceLabel,
        detailKind: 'liepinCardCandidates',
        detailPayload: {
          kind: 'liepinCardCandidates',
          candidateReviewItemIds,
          candidateEvidenceRefs,
          bestScore: highScore,
          ...detailFields,
        },
        eventIds: events.filter((event) => event.eventName === 'candidate_review_item_upserted').map(eventId),
        sourceRunId: events.find((event) => event.eventName === 'candidate_review_item_upserted')?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
        candidateReviewItemIds,
        candidateEvidenceRefs,
        detailOpenRequestIds: detailFields.detailOpenRequestIds,
      }),
    );
    graphEdges.push({ from: lastNode, to: candidateId, tone: 'green', label: 'AI 判断' });
    logEntries.push({
      id: 'liepin-candidates-log',
      at: liepinScores[0].eventSeq,
      tag: 'HIT',
      text: `简介初筛 ${String(liepinScores.length)} 人${highScore !== null ? `，最高 ${String(highScore)} 分` : ''}`,
      sourceKind,
      sourceLabel,
      lane: sourceKind,
      relatedNodeId: candidateId,
    });
    lastNode = candidateId;
  }

  const detailEvents = events.filter((event) =>
    [
      'liepin_detail_open_auto_recommended',
      'liepin_detail_open_requested',
      'liepin_detail_open_leased',
      'liepin_detail_open_blocked',
    ].includes(event.eventName),
  );
  if (detailEvents.length > 0 || visibleDetailRequests.length > 0) {
    const leasedCount = detailEvents.filter((event) => event.eventName === 'liepin_detail_open_leased').length;
    const blockedCount = detailEvents.filter((event) => event.eventName === 'liepin_detail_open_blocked').length;
    const approvedOrLeasedCount =
      visibleDetailRequests.filter((request) => request.status === 'approved' || request.ledger?.status === 'leased').length +
      leasedCount;
    const blockedOrRejectedCount =
      visibleDetailRequests.filter((request) => request.status === 'rejected' || request.status === 'blocked').length +
      blockedCount;
    const detailId = 'liepin-detail-approval';
    graphNodes.push(
      sourceNode({
        id: detailId,
        kind: '灵光',
        label: `详情审批 · ${String(Math.max(detailEvents.length, visibleDetailRequests.length))} 个`,
        detail: `已预留 ${String(approvedOrLeasedCount)} · 阻塞 ${String(blockedOrRejectedCount)}`,
        x: 80,
        y: baseY + (allMode ? 8 : 11),
        tone: blockedOrRejectedCount > 0 ? 'amber' : 'violet',
        sourceKind,
        sourceLabel,
        detailKind: 'liepinDetailApproval',
        detailPayload: {
          kind: 'liepinDetailApproval',
          ...detailFields,
        },
        eventIds: detailEvents.map(eventId),
        sourceRunId: detailEvents[0]?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
        candidateReviewItemIds: safeCandidateReviewItemIds,
        candidateEvidenceRefs,
        detailOpenRequestIds: detailFields.detailOpenRequestIds,
      }),
    );
    graphEdges.push({ from: lastNode, to: detailId, tone: 'violet', label: '详情队列' });
    const detailLogAt = detailEvents[0]?.globalSeq ?? started?.globalSeq ?? graphNodes.length;
    logEntries.push({
      id: 'liepin-detail-log',
      at: Number.isFinite(detailLogAt) ? detailLogAt : graphNodes.length,
      tag: 'AHA',
      text: `详情审批队列 ${String(Math.max(detailEvents.length, visibleDetailRequests.length))} 个，已预留 ${String(approvedOrLeasedCount)} 个`,
      sourceKind,
      sourceLabel,
      lane: sourceKind,
      relatedNodeId: detailId,
    });
    lastNode = detailId;
  }

  const completed = firstEvent([...events].reverse(), ['source_run_completed']);
  if (completed) {
    logEntries.push({
      id: 'liepin-completed-log',
      at: completed.globalSeq,
      tag: 'SYS',
      text: '猎聘简介抓取完成，等待详情审批或聚合排序',
      sourceKind,
      sourceLabel,
      lane: sourceKind,
    });
  }
  return lastNode;
}

function appendFinalNode({
  candidateScores,
  fallbackAnchor,
  graphEdges,
  graphNodes,
  hasCompletion,
  logEntries,
  sourceTerminalNodes,
}: {
  candidateScores: CandidateScore[];
  fallbackAnchor: string;
  graphEdges: RecruiterGraphEdge[];
  graphNodes: RecruiterGraphNode[];
  hasCompletion: boolean;
  logEntries: RecruiterLogEntry[];
  sourceTerminalNodes: string[];
}): string | null {
  if (candidateScores.length === 0 && !hasCompletion) {
    return null;
  }
  const finalId = 'final-shortlist';
  const highScore = bestScore(candidateScores);
  graphNodes.push({
    id: finalId,
    at: graphNodes.length,
    kind: '排序',
    label: candidateScores.length > 0 ? `最终短名单 · ${String(candidateScores.length)} 人` : '最终短名单',
    detail: highScore !== null ? `最高 ${String(highScore)} 分` : '检索完成',
    x: 94,
    y: 50,
    tone: 'green',
    sourceKind: 'all',
    sourceLabel: 'All sources',
    lane: 'shared',
    detailKind: 'aggregation',
    detailPayload: {
      kind: 'aggregation',
      candidateCount: candidateScores.length,
      bestScore: highScore,
    },
    eventIds: [],
    sourceRunId: null,
    candidateReviewItemIds: candidateScores.map((candidate) => candidate.reviewItemId),
    candidateEvidenceRefs: [],
    detailOpenRequestIds: [],
  });
  for (const sourceNodeId of sourceTerminalNodes.length > 0 ? sourceTerminalNodes : [fallbackAnchor]) {
    graphEdges.push({ from: sourceNodeId, to: finalId, tone: 'green', label: '聚合排序' });
  }
  if (candidateScores.length > 0) {
    logEntries.push({
      id: 'final-shortlist-log',
      at: Math.max(...candidateScores.map((candidate) => candidate.eventSeq)) + 0.5,
      tag: 'SYS',
      text: `最终短名单 ${String(candidateScores.length)} 人${highScore !== null ? `，最高 ${String(highScore)} 分` : ''}`,
      sourceKind: 'all',
      sourceLabel: 'All sources',
      lane: 'shared',
      relatedNodeId: finalId,
    });
  }
  return finalId;
}

function sourceNode(input: Omit<RecruiterGraphNode, 'at' | 'lane' | 'sourceLabel'> & {
  sourceKind: SourceKind;
  sourceLabel: string;
}): RecruiterGraphNode {
  return {
    ...input,
    at: 0,
    lane: input.sourceKind,
    sourceLabel: input.sourceLabel,
  };
}

function scopeEvents(events: WorkbenchEvent[], sourceFilter: SourceFilter): WorkbenchEvent[] {
  if (sourceFilter === 'all') {
    return events;
  }
  return events.filter((event) => event.sourceKind === sourceFilter);
}

function selectedSourceKinds(
  session: WorkbenchSession,
  scopedEvents: WorkbenchEvent[],
  sourceFilter: SourceFilter,
): SourceKind[] {
  if (sourceFilter !== 'all') {
    return [sourceFilter];
  }
  const activeKinds = new Set<SourceKind>();
  for (const event of scopedEvents) {
    if (event.sourceKind) {
      activeKinds.add(event.sourceKind);
    }
  }
  for (const card of session.sourceCards) {
    if (
      card.status === 'running' ||
      card.status === 'completed' ||
      card.status === 'failed' ||
      card.cardsScannedCount > 0 ||
      card.uniqueCandidatesCount > 0
    ) {
      activeKinds.add(card.sourceKind);
    }
  }
  return session.sourceCards
    .map((card) => card.sourceKind)
    .filter((sourceKind, index, values) => values.indexOf(sourceKind) === index && activeKinds.has(sourceKind));
}

function laneBaseY(sourceKind: SourceKind, allMode: boolean): number {
  if (!allMode) {
    return 50;
  }
  return sourceKind === 'cts' ? 30 : 70;
}

function roundYPositions(sourceKind: SourceKind, allMode: boolean) {
  if (!allMode) {
    return { query: 22, result: 40, score: 60, reflect: 78 };
  }
  const base = laneBaseY(sourceKind, allMode);
  return {
    query: base - 13,
    result: base - 4,
    score: base + 5,
    reflect: base + 14,
  };
}

function sourceCardDetail(cardsScannedCount: number, uniqueCandidatesCount: number): string {
  if (cardsScannedCount > 0 || uniqueCandidatesCount > 0) {
    return `扫描 ${String(cardsScannedCount)} · 命中 ${String(uniqueCandidatesCount)}`;
  }
  return '本地库 · 可批量检索';
}

function sourceQueuePayload(
  sourceCard: WorkbenchSession['sourceCards'][number] | undefined,
  sourceKind: SourceKind,
  sourceRunId: string | null,
): RecruiterGraphNode['detailPayload'] {
  const warningCode = sourceCard?.warningCode ?? sourceCard?.connectionWarningCode ?? null;
  const warningMessage = displaySafeWarning(warningCode, sourceCard?.warningMessage ?? sourceCard?.connectionWarningMessage ?? null);
  return {
    kind: 'sourceQueue',
    sourceKind,
    sourceRunId,
    status: sourceCard?.status ?? null,
    authState: sourceCard?.authState ?? null,
    connectionStatus: sourceCard?.connectionStatus ?? null,
    cardsScannedCount: sourceCard?.cardsScannedCount ?? 0,
    uniqueCandidatesCount: sourceCard?.uniqueCandidatesCount ?? 0,
    detailOpenUsedCount: sourceCard?.detailOpenUsedCount ?? 0,
    detailOpenBlockedCount: sourceCard?.detailOpenBlockedCount ?? 0,
    warningCode,
    warningMessage,
  };
}

function runtimeEventData(event: WorkbenchEvent): RuntimeEventData | null {
  const outer = recordValue(event.payload);
  if (!outer) {
    return null;
  }
  const inner = recordValue(outer.payload) ?? outer;
  return {
    event,
    payload: inner,
    roundNo: numberValue(outer.roundNo) ?? numberValue(outer.round_no) ?? numberValue(inner.round_no) ?? numberValue(inner.roundNo),
    message: stringValue(outer.message) ?? stringValue(inner.message) ?? '',
  };
}

function criteriaFromRequirements(requirements: RuntimeEventData | undefined): WorkbenchRequirementTriageInput {
  if (!requirements) {
    return emptyCriteria;
  }
  const queryHints = [
    ...stringsValue(requirements.payload.search_terms),
    ...stringsValue(requirements.payload.query_terms),
    ...stringsValue(requirements.payload.notes_query_terms),
  ];
  return {
    mustHaves: stringsValue(requirements.payload.must_have_capabilities),
    niceToHaves: stringsValue(requirements.payload.preferred_capabilities),
    synonyms: stringsValue(requirements.payload.synonyms),
    seniorityFilters: stringsValue(requirements.payload.seniority_filters),
    exclusions: stringsValue(requirements.payload.exclusions),
    generatedQueryHints: uniqueStrings(queryHints),
  };
}

export function criteriaFromTriage(triage: WorkbenchRequirementTriage): WorkbenchRequirementTriageInput {
  return {
    mustHaves: [...triage.mustHaves],
    niceToHaves: [...triage.niceToHaves],
    synonyms: [...triage.synonyms],
    seniorityFilters: [...triage.seniorityFilters],
    exclusions: [...triage.exclusions],
    generatedQueryHints: [...triage.generatedQueryHints],
  };
}

export function hasTriageInput(triage: WorkbenchRequirementTriageInput): boolean {
  return (
    triage.mustHaves.length > 0 ||
    triage.niceToHaves.length > 0 ||
    triage.synonyms.length > 0 ||
    triage.seniorityFilters.length > 0 ||
    triage.exclusions.length > 0 ||
    triage.generatedQueryHints.length > 0
  );
}

function roundSummaries(events: RuntimeEventData[]): RoundSummary[] {
  return events
    .filter((item) => item.event.eventName === 'runtime_round_completed')
    .map((item) => {
      const roundNo = item.roundNo ?? 0;
      const queryTerms = queryTermsFromPayload(item.payload);
      return {
        eventSeq: item.event.globalSeq,
        eventId: eventId(item.event),
        sourceRunId: item.event.sourceRunId,
        roundNo,
        queryTerms,
        queryLabel: queryLabel(item.payload),
        rawCandidateCount: metricValue(item.payload, 'raw_candidate_count', 'rawCandidateCount') ?? 0,
        uniqueNewCount: metricValue(item.payload, 'unique_new_count', 'uniqueNewCount') ?? 0,
        newlyScoredCount: metricValue(item.payload, 'newly_scored_count', 'newlyScoredCount') ?? 0,
        fitCount: metricValue(item.payload, 'fit_count', 'fitCount') ?? 0,
        notFitCount: metricValue(item.payload, 'not_fit_count', 'notFitCount') ?? 0,
        reflectionSummary:
          stringValue(item.payload.reflection_summary) ??
          stringValue(item.payload.reflectionSummary) ??
          stringValue(item.payload.reflection_rationale) ??
          '',
        reflectionRationale:
          stringValue(item.payload.reflection_rationale) ?? stringValue(item.payload.reflectionRationale) ?? '',
        nextDirection: stringValue(item.payload.next_direction) ?? stringValue(item.payload.nextDirection) ?? '',
      };
    })
    .filter((round) => round.roundNo > 0);
}

function candidateScoresFromInputs(
  events: WorkbenchEvent[],
  candidateReviewItems: WorkbenchCandidateReviewItem[] = [],
  sourceKindFilter?: SourceKind,
): CandidateScore[] {
  const byReviewItemId = new Map<string, CandidateScore>();
  for (const event of events) {
    if (event.eventName !== 'candidate_review_item_upserted') {
      continue;
    }
    const reviewItemId =
      stringValue(event.payload.reviewItemId) ??
      stringValue(event.payload.review_item_id) ??
      stringValue(event.payload.candidateId) ??
      `event-${String(event.globalSeq)}`;
    const score = numberValue(event.payload.score) ?? numberValue(event.payload.autoDetailScore);
    if (score === null) {
      continue;
    }
    byReviewItemId.set(reviewItemId, {
      reviewItemId,
      score,
      sourceKind: event.sourceKind,
      eventSeq: event.globalSeq,
    });
  }
  for (const [index, item] of candidateReviewItems.entries()) {
    const scopedEvidence = sourceKindFilter
      ? item.evidence.filter((evidence) => evidence.sourceKind === sourceKindFilter)
      : item.evidence;
    const score = sourceKindFilter
      ? firstNumber(scopedEvidence.map((evidence) => evidence.score)) ?? item.aggregateScore
      : item.aggregateScore ?? firstNumber(scopedEvidence.map((evidence) => evidence.score));
    if (score === null) {
      continue;
    }
    const sourceKind = sourceKindFilter ?? scopedEvidence.find((evidence) => evidence.sourceKind)?.sourceKind ?? null;
    byReviewItemId.set(item.reviewItemId, {
      reviewItemId: item.reviewItemId,
      score,
      sourceKind,
      eventSeq: byReviewItemId.get(item.reviewItemId)?.eventSeq ?? 100_000 + index,
    });
  }
  return [...byReviewItemId.values()].sort((left, right) => left.eventSeq - right.eventSeq);
}

function queryLabel(payload: Record<string, unknown>): string {
  const queries = Array.isArray(payload.executed_queries)
    ? payload.executed_queries
    : Array.isArray(payload.executedQueries)
      ? payload.executedQueries
      : [];
  const labels = queries
    .map((item) => {
      const query = recordValue(item);
      return query ? listText([...stringsValue(query.query_terms), ...stringsValue(query.queryTerms)]) : '';
    })
    .filter(Boolean);
  if (labels.length > 0) {
    return labels.join(' / ');
  }
  return listText(queryTermsFromPayload(payload));
}

function scopeCandidateReviewItems(
  candidateReviewItems: WorkbenchCandidateReviewItem[],
  sourceKind: SourceKind,
): WorkbenchCandidateReviewItem[] {
  return candidateReviewItems.filter((item) => item.evidence.some((evidence) => evidence.sourceKind === sourceKind));
}

function scopeDetailOpenRequests(
  detailOpenRequests: WorkbenchDetailOpenRequest[],
  visibleReviewItemIds: Set<string>,
): WorkbenchDetailOpenRequest[] {
  return detailOpenRequests.filter((request) => visibleReviewItemIds.has(request.reviewItemId));
}

function detailRequestFields(detailOpenRequests: WorkbenchDetailOpenRequest[]) {
  const requestIds = detailOpenRequests.map((request) => request.requestId);
  return {
    detailOpenRequestIds: requestIds,
    requestIds,
    requestSummaries: detailOpenRequests.map(detailRequestSummary),
    budgetText: detailBudgetText(detailOpenRequests),
  };
}

function detailBudgetText(detailOpenRequests: WorkbenchDetailOpenRequest[]): string | null {
  const labels: string[] = [];
  for (const request of detailOpenRequests) {
    if (
      request.status === 'pending' ||
      request.status === 'approved' ||
      request.status === 'rejected' ||
      request.status === 'bypassed'
    ) {
      labels.push(request.status);
    }
    if (request.ledger?.status === 'leased') {
      labels.push('leased');
    }
  }
  const uniqueLabels = uniqueStrings(labels);
  return uniqueLabels.length > 0 ? uniqueLabels.join(' · ') : null;
}

function displaySafeWarning(warningCode: string | null, warningMessage: string | null): string | null {
  const safeMessages: Record<string, string> = {
    login_required: '需要重新登录后才能继续。',
    budget_blocked: '详情额度不足，请调整预算。',
    connection_expired: '连接已过期，请重新授权。',
  };
  if (warningCode && safeMessages[warningCode]) {
    return safeMessages[warningCode];
  }
  if (warningCode || warningMessage) {
    return '源状态异常，请查看设置。';
  }
  return null;
}

function bestScore(candidateScores: CandidateScore[]): number | null {
  if (candidateScores.length === 0) {
    return null;
  }
  return Math.max(...candidateScores.map((candidate) => candidate.score));
}

function evidenceRefsForSource(
  candidateReviewItems: WorkbenchCandidateReviewItem[],
  sourceKind: SourceKind,
): RecruiterCandidateEvidenceRef[] {
  return candidateReviewItems.flatMap((item) =>
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
  const candidateLabel = request.candidate?.displayName || request.reviewItemId || request.requestId;
  return [candidateLabel, request.status, request.ledger?.status].filter(Boolean).join(' · ');
}

function queryTermsFromPayload(payload: Record<string, unknown>): string[] {
  const executedQueries = Array.isArray(payload.executed_queries)
    ? payload.executed_queries
    : Array.isArray(payload.executedQueries)
      ? payload.executedQueries
      : [];
  const executedTerms = executedQueries.flatMap((item) => {
    const query = recordValue(item);
    return query ? [...stringsValue(query.query_terms), ...stringsValue(query.queryTerms)] : [];
  });
  return uniqueStrings([
    ...executedTerms,
    ...stringsValue(payload.query_terms),
    ...stringsValue(payload.queryTerms),
    ...stringsValue(payload.search_terms),
    ...stringsValue(payload.searchTerms),
  ]);
}

function metricValue(payload: Record<string, unknown>, snakeKey: string, camelKey: string): number | null {
  return numberValue(payload[snakeKey]) ?? numberValue(payload[camelKey]);
}

function firstNumber(values: Array<number | null>): number | null {
  return values.find((value) => value !== null) ?? null;
}

function eventId(event: WorkbenchEvent): string {
  return `seq:${String(event.globalSeq)}`;
}

function firstEvent(events: WorkbenchEvent[], eventNames: string[]): WorkbenchEvent | null {
  for (const event of events) {
    if (eventNames.includes(event.eventName)) {
      return event;
    }
  }
  return null;
}

function firstTimestamp(events: WorkbenchEvent[], eventNames: string[], reverse = false): Date | null {
  const selectedEvents = reverse ? [...events].reverse() : events;
  for (const event of selectedEvents) {
    if (!eventNames.includes(event.eventName)) {
      continue;
    }
    const timestamp = Date.parse(event.createdAt);
    if (!Number.isNaN(timestamp)) {
      return new Date(timestamp);
    }
  }
  return null;
}

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${String(hours)}小时${String(minutes)}分${String(seconds)}秒`;
  }
  if (minutes > 0) {
    return `${String(minutes)}分${String(seconds)}秒`;
  }
  return `${String(seconds)}秒`;
}

function firstNonEmpty(lists: string[][]): string {
  for (const list of lists) {
    const text = listText(list.slice(0, 3));
    if (text) {
      return text;
    }
  }
  return '';
}

function listText(values: string[]): string {
  return values.filter(Boolean).join(' + ');
}

function chooseVisibleList(primary: string[], fallback: string[]): string[] {
  return primary.length > 0 ? primary : fallback;
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function clip(value: string, limit: number): string {
  return value.length > limit ? `${value.slice(0, Math.max(0, limit - 3)).trim()}...` : value;
}

function stringsValue(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => (typeof item === 'string' ? item.trim() : '')).filter(Boolean);
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}
