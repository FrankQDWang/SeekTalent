import type {
	RecruiterCandidateEvidenceRef,
	RecruiterGraphEdge,
	RecruiterGraphNode,
	RecruiterLogEntry,
	SourceKind,
	WorkbenchRequirementTriageInput
} from './recruiterAnimation';
import { sourceReasonLabel } from './sourceDisplay';
import type { components } from '../api/schema';

type WorkbenchCandidateReviewItem = components['schemas']['WorkbenchCandidateReviewItemResponse'];
type WorkbenchDetailOpenRequest = components['schemas']['WorkbenchDetailOpenRequestResponse'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];
type WorkbenchFinalTopCandidate = components['schemas']['WorkbenchFinalTopCandidateResponse'];
type WorkbenchRequirementTriage = components['schemas']['WorkbenchRequirementTriageResponse'];
type WorkbenchRuntimeSourceLaneState =
	components['schemas']['WorkbenchRuntimeSourceLaneStateResponse'];
type WorkbenchRuntimeSourceState = components['schemas']['WorkbenchRuntimeSourceStateResponse'];
type WorkbenchSession = components['schemas']['WorkbenchSessionResponse'];
type WorkbenchNotePayload = {
	text?: string;
	eventSeq?: number;
	event_seq?: number;
	globalSeq?: number;
	global_seq?: number;
	noteKind?: string;
	note_kind?: string;
	statusHint?: string | null;
	status_hint?: string | null;
};

export type SourceFilter = SourceKind | 'all';

export type RunStory = {
	criteria: WorkbenchRequirementTriageInput;
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	logEntries: RunStoryLogEntry[];
	completionText: string | null;
};

export type RunStoryLogEntry = RecruiterLogEntry & {
	noteKind?: string | undefined;
	statusHint?: string | null | undefined;
};

export type BuildRunStoryInput = {
	session: WorkbenchSession;
	events: WorkbenchEvent[];
	candidateReviewItems?: WorkbenchCandidateReviewItem[];
	finalTopCandidates?: WorkbenchFinalTopCandidate[];
	finalTopStatus?: 'loading' | 'success' | 'error';
	detailOpenRequests?: WorkbenchDetailOpenRequest[];
	sourceFilter?: SourceFilter;
};

type RuntimeEventData = {
	event: WorkbenchEvent;
	payload: Record<string, unknown>;
	roundNo: number | null;
	message: string;
};

type RuntimePublicStage =
	| 'round_query'
	| 'source_dispatch'
	| 'source_result'
	| 'merge'
	| 'scoring'
	| 'feedback'
	| 'finalization';

type RuntimeRoundStage = Exclude<RuntimePublicStage, 'finalization'>;

type RuntimeRoundStatus =
	| 'pending'
	| 'running'
	| 'completed'
	| 'partial'
	| 'blocked'
	| 'degraded'
	| 'failed';

type RuntimeRoundGraphEvent = {
	event: WorkbenchEvent;
	runtimeRunId: string;
	eventId: string;
	eventSeq: number;
	stage: RuntimeRoundStage;
	roundNo: number;
	sourceKind: SourceKind | null;
	status: RuntimeRoundStatus;
	counts: Record<string, number>;
	safeReasonCode: string | null;
};

type RoundSummary = {
	eventSeq: number;
	eventIds: string[];
	sourceRunId: string | null;
	roundNo: number;
	queryTerms: string[];
	queryLabel: string;
	executedQueries: ExecutedQuerySummary[];
	rawCandidateCount: number;
	uniqueNewCount: number;
	recallCounts: Record<string, unknown> | null;
	newlyScoredCount: number;
	scoredCount: number;
	fitCount: number;
	notFitCount: number;
	reflectionSummary: string;
	reflectionRationale: string;
	nextDirection: string;
	hasControllerStarted: boolean;
	hasControllerCompleted: boolean;
	hasSearchStarted: boolean;
	hasSearchCompleted: boolean;
	hasScoringStarted: boolean;
	hasScoringCompleted: boolean;
	hasReflectionStarted: boolean;
	hasReflectionCompleted: boolean;
};

type ExecutedQuerySummary = {
	query_role: string | null;
	lane_type: string | null;
	query_terms: string[];
	keyword_query: string | null;
	query_instance_id: string | null;
	query_fingerprint: string | null;
};

type CandidateScore = {
	reviewItemId: string;
	score: number;
	sourceKind: SourceKind | null;
	eventSeq: number;
};

type FinalReport = {
	report: string | null;
	stopReason: string | null;
	eventId: string | null;
};

const emptyCriteria: WorkbenchRequirementTriageInput = {
	mustHaves: [],
	niceToHaves: [],
	synonyms: [],
	seniorityFilters: [],
	exclusions: [],
	generatedQueryHints: []
};

const sourceLabels: Record<SourceKind, string> = {
	cts: 'CTS',
	liepin: '猎聘'
};

export function buildRunStory(input: BuildRunStoryInput): RunStory {
	const {
		session,
		events,
		candidateReviewItems = [],
		finalTopCandidates = [],
		finalTopStatus = 'success',
		detailOpenRequests = [],
		sourceFilter = 'all'
	} = input;
	const scopedEvents = scopeEvents(events, sourceFilter);
	const noteLogEntries = workbenchNoteLogEntries(scopedEvents);
	const allRuntimeEvents = events
		.filter((event) => event.eventName.startsWith('runtime_'))
		.map(runtimeEventData)
		.filter(Boolean) as RuntimeEventData[];
	const requirementsStarted = allRuntimeEvents.find(
		(item) =>
			item.event.sourceKind === null &&
			(item.event.eventName === 'runtime_run_started' ||
				item.event.eventName === 'runtime_requirements_started')
	);
	const requirements = allRuntimeEvents.find(
		(item) => item.event.eventName === 'runtime_requirements_completed'
	);
	const runtimeCriteria = criteriaFromRequirements(requirements);
	const triageCriteria = criteriaFromTriage(session.requirementTriage);
	const triageHasInput = hasTriageInput(triageCriteria);
	const runtimeHasInput = hasTriageInput(runtimeCriteria);
	const criteria = mergeCriteriaInput(triageCriteria, runtimeCriteria);
	const visibleLogEntries =
		noteLogEntries.length > 0
			? noteLogEntries
			: initialBusinessLogEntries(session, criteria, scopedEvents);
	const sourceKinds = selectedSourceKinds(session, scopedEvents, sourceFilter);
	const scopedCandidateReviewItems =
		sourceFilter === 'all'
			? candidateReviewItems
			: scopeCandidateReviewItems(candidateReviewItems, sourceFilter);
	const candidateScores = candidateScoresFromInputs(scopedEvents, scopedCandidateReviewItems);
	const finalCandidateScores =
		input.finalTopCandidates === undefined
			? candidateScores
			: candidateScoresFromFinalTopCandidates(finalTopCandidates);
	const finalReport = finalReportFromEvents(allRuntimeEvents);
	const projectedRuntimeSourceState = runtimeSourceStateForFilter(
		session.runtimeSourceState ?? null,
		sourceFilter
	);
	const runtimeSourceState = runtimeSourceStateHasActivity(projectedRuntimeSourceState)
		? projectedRuntimeSourceState
		: null;
	const hasRuntimeFinalization = runtimeSourceState
		? runtimeSourceState.coverageStatus !== 'pending' ||
			runtimeSourceState.finalizationRevision != null ||
			runtimeSourceState.finalizationReasonCode != null ||
			runtimeSourceState.canonicalResumeSelectedCount > 0
		: false;
	const hasSourceEvents = scopedEvents.some((event) => event.sourceKind !== null);
	const hasCompletion = scopedEvents.some(
		(event) =>
			event.eventName === 'source_run_completed' || event.eventName === 'runtime_run_completed'
	);

	if (
		!requirementsStarted &&
		!requirements &&
		!triageHasInput &&
		!hasSourceEvents &&
		candidateScores.length === 0 &&
		finalCandidateScores.length === 0 &&
		!runtimeSourceState
	) {
		return {
			criteria: emptyCriteria,
			graphNodes: [],
			graphEdges: [],
			logEntries: visibleLogEntries,
			completionText: null
		};
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
				sourceKinds: session.sourceCards.map((card) => card.sourceKind)
			},
			eventIds: [],
			sourceRunId: null,
			candidateReviewItemIds: [],
			candidateEvidenceRefs: [],
			detailOpenRequestIds: []
		}
	];
	const graphEdges: RecruiterGraphEdge[] = [];
	const logEntries: RecruiterLogEntry[] = [];

	if (requirementsStarted || requirements || triageHasInput || runtimeHasInput) {
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
			detail:
				firstNonEmpty([criteria.mustHaves, criteria.niceToHaves]) ||
				(requirements ? '已解析岗位约束' : '正在拆解岗位需求'),
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
				approvedAt: session.requirementTriage.approvedAt ?? null
			},
			eventIds: [requirementsStarted, requirements].flatMap((item) =>
				item ? [eventId(item.event)] : []
			),
			sourceRunId: null,
			candidateReviewItemIds: [],
			candidateEvidenceRefs: [],
			detailOpenRequestIds: []
		});
		graphEdges.push({ from: 'job', to: 'requirements', tone: 'blue', label: '提取约束' });
		logEntries.push({
			id: 'requirements',
			at: requirements?.event.globalSeq ?? requirementsStarted?.event.globalSeq ?? 1,
			tag: 'THINK',
			text: requirements
				? `解析岗位需求：${listText(criteria.mustHaves.slice(0, 3)) || session.jobTitle}`
				: '正在拆解岗位需求，准备生成可确认的检索标准。',
			sourceKind: 'all',
			sourceLabel: 'All sources',
			lane: 'shared',
			relatedNodeId: 'requirements'
		});
	}

	const anchor = graphNodes.some((node) => node.id === 'requirements') ? 'requirements' : 'job';
	const allMode = sourceFilter === 'all' && sourceKinds.length > 1;
	const hasRetrievalActivity =
		hasSourceEvents ||
		Boolean(runtimeSourceState) ||
		candidateScores.length > 0 ||
		finalCandidateScores.length > 0 ||
		hasCompletion;
	if (!hasRetrievalActivity) {
		return {
			criteria,
			graphNodes,
			graphEdges,
			logEntries: visibleLogEntries,
			completionText: null
		};
	}
	const sourceAnchor = appendSourcePlanNode({
		anchor,
		graphEdges,
		graphNodes,
		runtimeSourceState,
		sourceKinds
	});
	const sourceTerminalNodes: string[] = [];
	const roundEvents = runtimeRoundGraphEvents(scopedEvents);

	if (roundEvents.length > 0) {
		const roundTerminalNode = appendRuntimeRoundModules({
			graphEdges,
			graphNodes,
			roundEvents,
			sourceKinds,
			startNodeId: sourceAnchor
		});
		if (roundTerminalNode) {
			sourceTerminalNodes.push(roundTerminalNode);
		}
	} else {
		for (const sourceKind of sourceKinds) {
			const sourceEvents = scopedEvents.filter((event) => event.sourceKind === sourceKind);
			if (sourceKind === 'cts') {
				const terminalNode = appendCtsLane({
					allMode,
					anchor: sourceAnchor,
					events: sourceEvents,
					graphEdges,
					graphNodes,
					logEntries,
					runtimeEvents: allRuntimeEvents.filter((item) => item.event.sourceKind === 'cts'),
					session
				});
				if (terminalNode) {
					sourceTerminalNodes.push(terminalNode);
				}
				continue;
			}
			const terminalNode = appendLiepinLane({
				allMode,
				anchor: sourceAnchor,
				events: sourceEvents,
				graphEdges,
				graphNodes,
				logEntries,
				candidateReviewItems: scopeCandidateReviewItems(candidateReviewItems, sourceKind),
				detailOpenRequests,
				session
			});
			if (terminalNode) {
				sourceTerminalNodes.push(terminalNode);
			}
		}
	}

	const canShowFinalNode =
		finalCandidateScores.length > 0 ||
		hasCompletion ||
		finalReport.eventId !== null ||
		hasRuntimeFinalization;
	const finalNodeId = canShowFinalNode
		? appendFinalNode({
				candidateScores: finalCandidateScores,
				finalTopStatus,
				graphEdges,
				graphNodes,
				logEntries,
				sourceTerminalNodes,
				fallbackAnchor: anchor,
				finalReport,
				hasCompletion,
				runtimeSourceState,
				hasRuntimeRoundGraph: roundEvents.length > 0
			})
		: null;

	return {
		criteria,
		graphNodes,
		graphEdges,
		logEntries: visibleLogEntries,
		completionText:
			finalNodeId && (hasCompletion || finalCandidateScores.length > 0)
				? '检索完成 · 候选人进入短名单'
				: null
	};
}

function initialBusinessLogEntries(
	session: WorkbenchSession,
	criteria: WorkbenchRequirementTriageInput,
	events: WorkbenchEvent[]
): RunStoryLogEntry[] {
	const statuses = new Set(session.sourceRuns.map((run) => run.status));
	const hasQueuedOrRunning =
		statuses.has('running') ||
		events.some(
			(event) => event.eventName === 'source_run_queued' || event.eventName === 'source_run_started'
		);
	const hasCompleted =
		session.sourceRuns.length > 0 && session.sourceRuns.every((run) => run.status === 'completed');
	const hasFailed = statuses.has('failed');
	const criteriaReady = hasTriageInput(criteria);
	let text: string;
	let statusHint = 'new_progress';

	if (hasQueuedOrRunning) {
		text = criteriaReady
			? '检索已启动，正在根据已确认标准推进所选渠道。'
			: 'Agent 已启动，正在拆解岗位需求并准备检索标准。';
		statusHint = 'waiting';
	} else if (hasFailed) {
		text = '检索遇到问题，请查看检索渠道状态后再继续。';
		statusHint = 'failed';
	} else if (hasCompleted) {
		text = '检索已完成，结果已整理到策略图和节点详情。';
		statusHint = 'completed';
	} else if (criteriaReady && session.requirementTriage.status === 'approved') {
		text = '检索标准已确认，等待启动所选渠道。';
	} else if (criteriaReady) {
		text = '已生成检索标准，等待确认后启动检索。';
	} else {
		text = '已创建岗位会话，等待启动 Agent 拆解检索标准。';
	}

	return [
		{
			id: 'initial-business-note',
			at: 0,
			tag: 'SYS',
			text,
			sourceKind: 'all',
			sourceLabel: 'All sources',
			lane: 'shared',
			relatedNodeId: undefined,
			noteKind: statusHint === 'waiting' ? 'waiting' : 'progress',
			statusHint
		}
	];
}

function workbenchNoteLogEntries(events: WorkbenchEvent[]): RunStoryLogEntry[] {
	const byDisplayKey = new Map<string, RunStoryLogEntry>();
	for (const event of [...events].sort((left, right) => left.globalSeq - right.globalSeq)) {
		if (event.eventName !== 'workbench_note_created') {
			continue;
		}
		const payload = event.payload as WorkbenchNotePayload;
		const text = stringValue(payload.text)?.trim();
		if (!text) {
			continue;
		}
		const sequence =
			numberValue(payload.eventSeq) ??
			numberValue(payload.event_seq) ??
			numberValue(payload.globalSeq) ??
			numberValue(payload.global_seq) ??
			event.globalSeq;
		byDisplayKey.set(workbenchNoteDisplayKey(event, sequence), {
			id: `workbench-note-${String(sequence)}`,
			at: sequence,
			tag: 'SYS',
			text,
			sourceKind: event.sourceKind ?? 'all',
			sourceLabel: event.sourceKind ? sourceLabels[event.sourceKind] : 'All sources',
			lane: event.sourceKind ?? 'shared',
			relatedNodeId: undefined,
			noteKind: stringValue(payload.noteKind) ?? stringValue(payload.note_kind) ?? undefined,
			statusHint: stringValue(payload.statusHint) ?? stringValue(payload.status_hint) ?? null
		});
	}
	return [...byDisplayKey.values()].sort(
		(left, right) => left.at - right.at || left.id.localeCompare(right.id)
	);
}

function workbenchNoteDisplayKey(event: WorkbenchEvent, sequence: number): string {
	const key = event.idempotencyKey;
	if (!key?.startsWith('workbench-note-writer:')) {
		return `seq:${String(sequence)}`;
	}
	const [prefix, sessionId, tickSlot] = key.split(':');
	if (!sessionId || !tickSlot) {
		return `seq:${String(sequence)}`;
	}
	return `${prefix}:${sessionId}:${tickSlot}`;
}

export function displayTriageFromStory(
	triage: WorkbenchRequirementTriage,
	criteria: WorkbenchRequirementTriageInput
): WorkbenchRequirementTriageInput {
	return {
		mustHaves: chooseVisibleList(triage.mustHaves, criteria.mustHaves),
		niceToHaves: chooseVisibleList(triage.niceToHaves, criteria.niceToHaves),
		synonyms: chooseVisibleList(triage.synonyms, criteria.synonyms),
		seniorityFilters: chooseVisibleList(triage.seniorityFilters, criteria.seniorityFilters),
		exclusions: chooseVisibleList(triage.exclusions, criteria.exclusions),
		generatedQueryHints: chooseVisibleList(triage.generatedQueryHints, criteria.generatedQueryHints)
	};
}

function runtimeRoundGraphEvents(events: WorkbenchEvent[]): RuntimeRoundGraphEvent[] {
	return events
		.flatMap((event) => {
			const payload = recordValue(event.payload);
			if (!payload || payload.schemaVersion !== 'runtime_public_event_v1') {
				return [];
			}
			const stage = stringValue(payload.stage);
			if (!stage || !isRuntimeRoundStage(stage)) {
				return [];
			}
			if (payload.roundNo === null || payload.roundNo === undefined) {
				return [];
			}
			const roundNo = Number(payload.roundNo);
			if (!Number.isInteger(roundNo) || roundNo < 1) {
				return [];
			}
			const payloadSourceKind = payload.sourceKind;
			const sourceKind: SourceKind | null =
				payloadSourceKind === 'cts' || payloadSourceKind === 'liepin' ? payloadSourceKind : null;
			const eventSeq = Number(payload.eventSeq);
			return [
				{
					event,
					runtimeRunId: stringValue(payload.runtimeRunId) ?? '',
					eventId: stringValue(payload.eventId) ?? '',
					eventSeq: Number.isFinite(eventSeq) ? eventSeq : event.globalSeq,
					stage,
					roundNo,
					sourceKind,
					status: runtimeRoundStatus(payload.status),
					counts: runtimeRoundCounts(payload.counts),
					safeReasonCode: stringValue(payload.safeReasonCode)
				}
			];
		})
		.sort(
			(left, right) =>
				left.roundNo - right.roundNo ||
				left.eventSeq - right.eventSeq ||
				left.event.globalSeq - right.event.globalSeq
		);
}

function appendRuntimeRoundModules(input: {
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	roundEvents: RuntimeRoundGraphEvent[];
	sourceKinds: SourceKind[];
	startNodeId: string;
}): string | null {
	const rounds = uniqueSortedNumbers(input.roundEvents.map((event) => event.roundNo));
	let previousNodeId = input.startNodeId;

	for (const [roundIndex, roundNo] of rounds.entries()) {
		const events = input.roundEvents.filter((event) => event.roundNo === roundNo);
		const queryId = `round-${String(roundNo)}-query`;
		const scoreId = `round-${String(roundNo)}-score`;
		const feedbackId = `round-${String(roundNo)}-feedback`;
		const activeSources = input.sourceKinds.filter((sourceKind) =>
			events.some((event) => event.sourceKind === sourceKind)
		);

		input.graphNodes.push(
			roundNode(
				queryId,
				roundNo,
				'检索',
				`第 ${String(roundNo)} 轮 · 查询包`,
				'round_query',
				events,
				null,
				20,
				50
			)
		);
		input.graphEdges.push({
			from: previousNodeId,
			to: queryId,
			tone: 'neutral',
			label: roundIndex === 0 ? '开始检索' : '下一轮'
		});

		const sourceNodeIds = activeSources.map((sourceKind, sourceIndex) => {
			const event =
				lastRoundEvent(events, 'source_result', sourceKind) ??
				lastRoundEvent(events, 'source_dispatch', sourceKind);
			const nodeId = `round-${String(roundNo)}-source-${sourceKind}`;
			input.graphNodes.push(
				sourceRoundNode(nodeId, roundNo, sourceKind, event, sourceIndex, activeSources.length)
			);
			input.graphEdges.push({ from: queryId, to: nodeId, tone: 'neutral', label: '执行' });
			return nodeId;
		});

		if (sourceNodeIds.length > 1) {
			const mergeId = `round-${String(roundNo)}-merge`;
			input.graphNodes.push(
				roundNode(
					mergeId,
					roundNo,
					'命中',
					`第 ${String(roundNo)} 轮 · 合并去重`,
					'merge',
					events,
					null,
					60,
					50
				)
			);
			for (const sourceNodeId of sourceNodeIds) {
				input.graphEdges.push({ from: sourceNodeId, to: mergeId, tone: 'blue', label: '证据合并' });
			}
			input.graphEdges.push({ from: mergeId, to: scoreId, tone: 'blue', label: '排序' });
		} else if (sourceNodeIds[0]) {
			input.graphEdges.push({ from: sourceNodeIds[0], to: scoreId, tone: 'blue', label: '排序' });
		} else {
			input.graphEdges.push({ from: queryId, to: scoreId, tone: 'blue', label: '排序' });
		}

		input.graphNodes.push(
			roundNode(
				scoreId,
				roundNo,
				'排序',
				`第 ${String(roundNo)} 轮 · Top Pool`,
				'scoring',
				events,
				null,
				74,
				50
			)
		);
		if (events.some((event) => event.stage === 'feedback')) {
			input.graphNodes.push(
				roundNode(
					feedbackId,
					roundNo,
					'反思',
					`第 ${String(roundNo)} 轮 · 下一轮策略`,
					'feedback',
					events,
					null,
					88,
					50
				)
			);
			input.graphEdges.push({ from: scoreId, to: feedbackId, tone: 'green', label: '反馈' });
			previousNodeId = feedbackId;
		} else {
			previousNodeId = scoreId;
		}
	}

	return rounds.length > 0 ? previousNodeId : null;
}

function roundNode(
	id: string,
	roundNo: number,
	kind: RecruiterGraphNode['kind'],
	label: string,
	stage: RuntimeRoundStage,
	events: RuntimeRoundGraphEvent[],
	sourceKind: SourceKind | null,
	x: number,
	y: number
): RecruiterGraphNode {
	const stageEvent = lastRoundEvent(events, stage, sourceKind);
	const countText =
		stageEvent?.counts.topPoolCount !== undefined
			? `${String(stageEvent.counts.topPoolCount)} 位进入 Top Pool`
			: stageEvent?.counts.mergedIdentities !== undefined
				? `${String(stageEvent.counts.mergedIdentities)} 位身份`
				: stageEvent?.counts.roundIdentities !== undefined
					? `${String(stageEvent.counts.roundIdentities)} 位身份`
					: stageEvent?.counts.roundReturned !== undefined
						? `${String(stageEvent.counts.roundReturned)} 位候选人`
						: `第 ${String(roundNo)} 轮`;
	return {
		id,
		at: roundNo,
		kind,
		label,
		detail: countText,
		x,
		y,
		tone: toneForRuntimeStatus(stageEvent?.status ?? 'pending'),
		sourceKind: sourceKind ?? 'all',
		sourceLabel: sourceKind ? sourceLabels[sourceKind] : '全部来源',
		lane: sourceKind ?? 'shared',
		eventIds: events
			.filter((event) => event.stage === stage && event.sourceKind === sourceKind)
			.map((event) => eventId(event.event)),
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function sourceRoundNode(
	id: string,
	roundNo: number,
	sourceKind: SourceKind,
	event: RuntimeRoundGraphEvent | null | undefined,
	sourceIndex: number,
	sourceCount: number
): RecruiterGraphNode {
	const y = sourceCount === 1 ? 50 : sourceIndex === 0 ? 36 : 64;
	const roundReturned = event?.counts.roundReturned ?? 0;
	const roundIdentities = event?.counts.roundIdentities ?? roundReturned;
	const sourceCumulativeReturned = event?.counts.sourceCumulativeReturned;
	const sourceCumulativeIdentities = event?.counts.sourceCumulativeIdentities;
	return {
		id,
		at: roundNo,
		kind: '检索',
		label: `第 ${String(roundNo)} 轮 · ${sourceLabels[sourceKind]} 检索`,
		detail: event?.safeReasonCode
			? (sourceReasonLabel(event.safeReasonCode) ?? '检索源需要处理。')
			: `${String(event?.counts.roundReturned ?? 0)} 位候选人`,
		x: 42,
		y,
		tone: toneForRuntimeStatus(event?.status ?? 'pending'),
		sourceKind,
		sourceLabel: sourceLabels[sourceKind],
		lane: sourceKind,
		detailKind: sourceKind === 'cts' ? 'ctsRoundResults' : 'liepinCardSearch',
		detailPayload:
			sourceKind === 'cts'
				? {
						kind: 'ctsRoundResults',
						roundNo,
						rawCandidateCount: roundReturned,
						uniqueNewCount: roundIdentities,
						recallCounts: {
							roundReturned,
							roundIdentities,
							sourceCumulativeReturned,
							sourceCumulativeIdentities,
							status: event?.status ?? 'pending',
							reasonCode: event?.safeReasonCode ?? null
						}
					}
				: {
						kind: 'liepinCardSearch',
						cardsScannedCount: roundReturned,
						uniqueCandidatesCount: roundIdentities,
						detailOpenRequestIds: [],
						requestIds: [],
						requestSummaries: [],
						budgetText: null
					},
		eventIds: event ? [eventId(event.event)] : [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function lastRoundEvent(
	events: RuntimeRoundGraphEvent[],
	stage: RuntimeRoundStage,
	sourceKind: SourceKind | null
): RuntimeRoundGraphEvent | null {
	const matching = events.filter(
		(event) => event.stage === stage && event.sourceKind === sourceKind
	);
	return matching[matching.length - 1] ?? null;
}

function isRuntimeRoundStage(stage: string): stage is RuntimeRoundStage {
	return (
		stage === 'round_query' ||
		stage === 'source_dispatch' ||
		stage === 'source_result' ||
		stage === 'merge' ||
		stage === 'scoring' ||
		stage === 'feedback'
	);
}

function runtimeRoundStatus(status: unknown): RuntimeRoundStatus {
	if (
		status === 'pending' ||
		status === 'running' ||
		status === 'completed' ||
		status === 'partial' ||
		status === 'blocked' ||
		status === 'degraded' ||
		status === 'failed'
	) {
		return status;
	}
	return 'pending';
}

function runtimeRoundCounts(counts: unknown): Record<string, number> {
	const record = recordValue(counts);
	if (!record) {
		return {};
	}
	return Object.fromEntries(
		Object.entries(record).flatMap(([key, value]) => {
			const count = numberValue(value);
			return count === null ? [] : [[key, count]];
		})
	);
}

function toneForRuntimeStatus(status: RuntimeRoundStatus): RecruiterGraphNode['tone'] {
	if (status === 'completed') return 'green';
	if (status === 'partial' || status === 'blocked' || status === 'degraded') return 'amber';
	if (status === 'failed') return 'rose';
	if (status === 'running') return 'blue';
	return 'neutral';
}

function mergeCriteriaInput(
	primary: WorkbenchRequirementTriageInput,
	fallback: WorkbenchRequirementTriageInput
): WorkbenchRequirementTriageInput {
	return {
		mustHaves: chooseVisibleList(primary.mustHaves, fallback.mustHaves),
		niceToHaves: chooseVisibleList(primary.niceToHaves, fallback.niceToHaves),
		synonyms: chooseVisibleList(primary.synonyms, fallback.synonyms),
		seniorityFilters: chooseVisibleList(primary.seniorityFilters, fallback.seniorityFilters),
		exclusions: chooseVisibleList(primary.exclusions, fallback.exclusions),
		generatedQueryHints: chooseVisibleList(
			primary.generatedQueryHints,
			fallback.generatedQueryHints
		)
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
	session
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
	const runtimeLaneState = runtimeLaneStateForSource(
		session.runtimeSourceState ?? null,
		sourceKind
	);
	const rounds = roundSummaries(runtimeEvents);
	const started = firstEvent(events, [
		'source_run_started',
		'source_run_queued',
		'runtime_run_started'
	]);
	if (events.length === 0 && rounds.length === 0 && !sourceCard && !runtimeLaneState) {
		return null;
	}

	const startId = 'cts-source-start';
	graphNodes.push({
		id: startId,
		at: graphNodes.length,
		kind: '检索',
		label: `${sourceLabel} 队列`,
		detail:
			sourceCard || runtimeLaneState
				? sourceCardDetail(
						runtimeLaneState?.cardsSeenCount ?? sourceCard?.cardsScannedCount ?? 0,
						runtimeLaneState?.candidatesCount ?? sourceCard?.uniqueCandidatesCount ?? 0
					)
				: '等待 CTS 检索',
		x: 34,
		y: baseY,
		tone: 'teal',
		sourceKind,
		sourceLabel,
		lane: sourceKind,
		detailKind: 'sourceQueue',
		detailPayload: sourceQueuePayload(
			sourceCard,
			sourceKind,
			started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
			runtimeLaneState
		),
		eventIds: started ? [eventId(started)] : [],
		sourceRunId: started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
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
		relatedNodeId: startId
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
				label: ctsRoundQueryLabel(round),
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
					executedQueries: round.executedQueries
				},
				eventIds: round.eventIds,
				sourceRunId: round.sourceRunId,
				candidateReviewItemIds: [],
				candidateEvidenceRefs: [],
				detailOpenRequestIds: []
			})
		);
		if (index === 0) {
			graphEdges.push({ from: anchor, to: queryId, tone: 'teal', label: '生成关键词' });
		} else {
			graphEdges.push(
				{ from: anchor, to: queryId, tone: 'blue', label: '需求约束' },
				{ from: lastNode, to: queryId, tone: 'violet', label: '反思迭代' }
			);
		}
		let roundTerminalNode = queryId;
		if (round.hasSearchCompleted) {
			graphNodes.push(
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
						recallCounts: round.recallCounts
					},
					eventIds: round.eventIds,
					sourceRunId: round.sourceRunId,
					candidateReviewItemIds: [],
					candidateEvidenceRefs: [],
					detailOpenRequestIds: []
				})
			);
			graphEdges.push({ from: queryId, to: resultId, tone: 'teal', label: 'CTS 检索' });
			roundTerminalNode = resultId;
		}
		if (round.hasScoringStarted || round.hasScoringCompleted) {
			graphNodes.push(
				sourceNode({
					id: scoreId,
					kind: '评分',
					label: round.hasScoringCompleted
						? `评分：fit ${String(round.fitCount)} / not_fit ${String(round.notFitCount)}`
						: '评分中',
					detail: `${String(round.newlyScoredCount)} 人进入评分`,
					x,
					y: positions.score,
					tone: round.hasScoringCompleted && round.fitCount <= 0 ? 'rose' : 'green',
					sourceKind,
					sourceLabel,
					detailKind: 'ctsRoundScoring',
					detailPayload: {
						kind: 'ctsRoundScoring',
						roundNo: round.roundNo,
						scoredCount: round.scoredCount,
						newlyScoredCount: round.newlyScoredCount,
						fitCount: round.fitCount,
						notFitCount: round.notFitCount
					},
					eventIds: round.eventIds,
					sourceRunId: round.sourceRunId,
					candidateReviewItemIds: [],
					candidateEvidenceRefs: [],
					detailOpenRequestIds: []
				})
			);
			graphEdges.push({ from: roundTerminalNode, to: scoreId, tone: 'green', label: '评分' });
			roundTerminalNode = scoreId;
		}
		if (round.hasReflectionStarted || round.hasReflectionCompleted) {
			graphNodes.push(
				sourceNode({
					id: reflectId,
					kind: '反思',
					label: round.hasReflectionCompleted ? `第 ${String(round.roundNo)} 轮反思` : '复盘中',
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
						nextDirection: round.nextDirection
					},
					eventIds: round.eventIds,
					sourceRunId: round.sourceRunId,
					candidateReviewItemIds: [],
					candidateEvidenceRefs: [],
					detailOpenRequestIds: []
				})
			);
			graphEdges.push({ from: roundTerminalNode, to: reflectId, tone: 'violet', label: '复盘' });
			roundTerminalNode = reflectId;
		}
		logEntries.push({
			id: `cts-round-${String(round.roundNo)}-business-log`,
			at: round.eventSeq,
			tag: 'SCAN',
			text: ctsRoundBusinessLogText(round),
			sourceKind,
			sourceLabel,
			lane: sourceKind,
			relatedNodeId: roundTerminalNode
		});
		lastNode = roundTerminalNode;
	}

	const completed = firstEvent([...events].reverse(), [
		'source_run_completed',
		'runtime_run_completed'
	]);
	if (completed) {
		const runStartedAt = firstTimestamp(events, ['source_run_started', 'runtime_run_started']);
		const runCompletedAt = firstTimestamp(
			events,
			['source_run_completed', 'runtime_run_completed'],
			true
		);
		const durationText =
			runStartedAt && runCompletedAt
				? `耗时 ${formatDuration(runCompletedAt.getTime() - runStartedAt.getTime())}`
				: '';
		const completedRuntime = runtimeEvents.find(
			(item) => item.event.eventName === 'runtime_run_completed'
		);
		const completedRoundCount =
			numberValue(completedRuntime?.payload.rounds_executed) ??
			Math.max(0, ...rounds.map((round) => round.roundNo), 0);
		const completionParts = [durationText, `检索轮次 ${String(completedRoundCount)}`].filter(
			Boolean
		);
		logEntries.push({
			id: 'cts-completed-log',
			at: completed.globalSeq,
			tag: 'SYS',
			text: `CTS 检索完成${completionParts.length > 0 ? `：${completionParts.join(' · ')}` : '，候选人进入汇总排序'}`,
			sourceKind,
			sourceLabel,
			lane: sourceKind
		});
	}
	return lastNode;
}

function ctsRoundBusinessLogText(round: RoundSummary): string {
	const parts = [
		`第 ${String(round.roundNo)} 轮围绕 ${ctsRoundBusinessQueryText(round)} 检索`,
		`搜到 ${String(round.rawCandidateCount)} 人，新增 ${String(round.uniqueNewCount)} 人`
	];
	if (round.newlyScoredCount > 0 || round.fitCount > 0 || round.notFitCount > 0) {
		parts.push(`评分后 ${String(round.fitCount)} 人匹配、${String(round.notFitCount)} 人不匹配`);
	}
	if (round.reflectionSummary) {
		parts.push(`复盘：${clip(round.reflectionSummary, 120)}`);
	}
	return parts.join('；');
}

function ctsRoundQueryLabel(round: RoundSummary): string {
	if (round.hasSearchCompleted) {
		return `第 ${String(round.roundNo)} 轮关键词`;
	}
	if (round.hasSearchStarted) {
		return `第 ${String(round.roundNo)} 轮检索中`;
	}
	return `正在判断第 ${String(round.roundNo)} 轮策略`;
}

function ctsRoundBusinessQueryText(round: RoundSummary): string {
	const queryTerms = round.queryTerms.slice(0, 4).join(' / ');
	if (queryTerms) {
		return queryTerms;
	}
	if (round.queryLabel) {
		return round.queryLabel.replaceAll(' + ', ' / ');
	}
	return '当前关键词';
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
	session
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
	const runtimeLaneState = runtimeLaneStateForSource(
		session.runtimeSourceState ?? null,
		sourceKind
	);
	const visibleReviewItems = scopeCandidateReviewItems(candidateReviewItems, sourceKind);
	const visibleDetailRequests = scopeDetailOpenRequests(detailOpenRequests);
	const detailFields = detailRequestFields(visibleDetailRequests);
	const candidateEvidenceRefs = evidenceRefsForSource(visibleReviewItems, sourceKind);
	const safeCandidateReviewItemIds = uniqueStrings([
		...visibleReviewItems.map((item) => item.reviewItemId),
		...candidateEvidenceRefs.map((ref) => ref.reviewItemId)
	]);
	if (events.length === 0 && !sourceCard && !runtimeLaneState) {
		return null;
	}

	const startId = 'liepin-source-start';
	const started = firstEvent(events, ['source_run_started', 'source_run_queued']);
	const queueDetail = liepinQueueDetail(sourceCard, runtimeLaneState);
	graphNodes.push({
		id: startId,
		at: graphNodes.length,
		kind: '检索',
		label: `${sourceLabel} 队列`,
		detail: queueDetail,
		x: 34,
		y: baseY,
		tone: queueDetail === '账号已连接，串行抓取简介' ? 'teal' : 'amber',
		sourceKind,
		sourceLabel,
		lane: sourceKind,
		detailKind: 'sourceQueue',
		detailPayload: sourceQueuePayload(
			sourceCard,
			sourceKind,
			started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
			runtimeLaneState
		),
		eventIds: started ? [eventId(started)] : [],
		sourceRunId: started?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
		candidateReviewItemIds: safeCandidateReviewItemIds,
		candidateEvidenceRefs,
		detailOpenRequestIds: detailFields.detailOpenRequestIds
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
		relatedNodeId: startId
	});

	let lastNode = startId;
	const searchCompleted = firstEvent(events, ['liepin_card_search_completed']);
	if (searchCompleted) {
		const scanned =
			numberValue(searchCompleted.payload.cardsScannedCount) ?? sourceCard?.cardsScannedCount ?? 0;
		const unique =
			numberValue(searchCompleted.payload.uniqueCandidatesCount) ??
			sourceCard?.uniqueCandidatesCount ??
			0;
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
					...detailFields
				},
				eventIds: [eventId(searchCompleted)],
				sourceRunId: searchCompleted.sourceRunId ?? null,
				candidateReviewItemIds: safeCandidateReviewItemIds,
				candidateEvidenceRefs,
				detailOpenRequestIds: detailFields.detailOpenRequestIds
			})
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
			relatedNodeId: searchId
		});
		lastNode = searchId;
	}

	const liepinScores = candidateScoresFromInputs(events, visibleReviewItems, sourceKind).filter(
		(candidate) => candidate.sourceKind === sourceKind
	);
	if (liepinScores.length > 0) {
		const highScore = bestScore(liepinScores);
		const candidateId = 'liepin-card-candidates';
		const candidateReviewItemIds = uniqueStrings([
			...safeCandidateReviewItemIds,
			...liepinScores.map((candidate) => candidate.reviewItemId)
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
					...detailFields
				},
				eventIds: events
					.filter((event) => event.eventName === 'candidate_review_item_upserted')
					.map(eventId),
				sourceRunId:
					events.find((event) => event.eventName === 'candidate_review_item_upserted')
						?.sourceRunId ??
					sourceCard?.sourceRunId ??
					null,
				candidateReviewItemIds,
				candidateEvidenceRefs,
				detailOpenRequestIds: detailFields.detailOpenRequestIds
			})
		);
		graphEdges.push({ from: lastNode, to: candidateId, tone: 'green', label: 'AI 判断' });
		logEntries.push({
			id: 'liepin-candidates-log',
			at: liepinScores[0]?.eventSeq ?? graphNodes.length,
			tag: 'HIT',
			text: `简介初筛 ${String(liepinScores.length)} 人${highScore !== null ? `，最高 ${String(highScore)} 分` : ''}`,
			sourceKind,
			sourceLabel,
			lane: sourceKind,
			relatedNodeId: candidateId
		});
		lastNode = candidateId;
	}

	const detailEvents = events.filter((event) =>
		[
			'liepin_detail_open_auto_recommended',
			'liepin_detail_open_requested',
			'liepin_detail_open_leased',
			'liepin_detail_open_blocked'
		].includes(event.eventName)
	);
	if (detailEvents.length > 0 || visibleDetailRequests.length > 0) {
		const detailCounts = detailApprovalCounts(visibleDetailRequests, detailEvents);
		const detailId = 'liepin-detail-approval';
		graphNodes.push(
			sourceNode({
				id: detailId,
				kind: '详情审批',
				label: `详情审批 · ${String(detailCounts.requestCount)} 个`,
				detail: `已预留 ${String(detailCounts.approvedOrLeasedCount)} · 阻塞 ${String(detailCounts.blockedOrRejectedCount)}`,
				x: 80,
				y: baseY + (allMode ? 8 : 11),
				tone: detailCounts.blockedOrRejectedCount > 0 ? 'amber' : 'violet',
				sourceKind,
				sourceLabel,
				detailKind: 'liepinDetailApproval',
				detailPayload: {
					kind: 'liepinDetailApproval',
					...detailFields
				},
				eventIds: detailEvents.map(eventId),
				sourceRunId: detailEvents[0]?.sourceRunId ?? sourceCard?.sourceRunId ?? null,
				candidateReviewItemIds: safeCandidateReviewItemIds,
				candidateEvidenceRefs,
				detailOpenRequestIds: detailFields.detailOpenRequestIds
			})
		);
		graphEdges.push({ from: lastNode, to: detailId, tone: 'violet', label: '详情队列' });
		const detailLogAt = detailEvents[0]?.globalSeq ?? started?.globalSeq ?? graphNodes.length;
		logEntries.push({
			id: 'liepin-detail-log',
			at: Number.isFinite(detailLogAt) ? detailLogAt : graphNodes.length,
			tag: 'DETAIL',
			text: `详情审批队列 ${String(detailCounts.requestCount)} 个，已预留 ${String(detailCounts.approvedOrLeasedCount)} 个`,
			sourceKind,
			sourceLabel,
			lane: sourceKind,
			relatedNodeId: detailId
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
			lane: sourceKind
		});
	}
	return lastNode;
}

function appendFinalNode({
	candidateScores,
	fallbackAnchor,
	finalReport,
	finalTopStatus,
	graphEdges,
	graphNodes,
	hasCompletion,
	logEntries,
	runtimeSourceState,
	sourceTerminalNodes,
	hasRuntimeRoundGraph
}: {
	candidateScores: CandidateScore[];
	fallbackAnchor: string;
	finalReport: FinalReport;
	finalTopStatus: 'loading' | 'success' | 'error';
	graphEdges: RecruiterGraphEdge[];
	graphNodes: RecruiterGraphNode[];
	hasCompletion: boolean;
	hasRuntimeRoundGraph: boolean;
	logEntries: RecruiterLogEntry[];
	runtimeSourceState: WorkbenchRuntimeSourceState | null;
	sourceTerminalNodes: string[];
}): string | null {
	if (
		candidateScores.length === 0 &&
		!hasCompletion &&
		finalTopStatus === 'success' &&
		!runtimeSourceState
	) {
		return null;
	}
	const candidateCountKnown = finalTopStatus === 'success';
	const finalId = 'final-shortlist';
	const highScore = bestScore(candidateScores);
	const finalTone = finalTopStatus === 'success' && candidateScores.length > 0 ? 'green' : 'amber';
	const finalDetail =
		finalTopStatus === 'loading'
			? 'Top 10 生成中'
			: finalTopStatus === 'error'
				? 'Top 10 暂不可用'
				: highScore !== null
					? `最高 ${String(highScore)} 分`
					: candidateScores.length === 0
						? '暂无最终候选人'
						: '检索完成';
	const sourceAnchors = sourceTerminalNodes.length > 0 ? sourceTerminalNodes : [fallbackAnchor];
	const mergeNodeId =
		runtimeSourceState && !hasRuntimeRoundGraph
			? appendMergeNode({ graphEdges, graphNodes, runtimeSourceState, sourceAnchors })
			: null;
	const finalCandidateIds = candidateScores.map((candidate) => candidate.reviewItemId);
	graphNodes.push({
		id: finalId,
		at: graphNodes.length,
		kind: '排序',
		label:
			candidateCountKnown && candidateScores.length > 0
				? `最终短名单 · ${String(candidateScores.length)} 人`
				: '最终短名单',
		detail: finalDetail,
		x: 94,
		y: 50,
		tone: finalTone,
		sourceKind: 'all',
		sourceLabel: 'All sources',
		lane: 'shared',
		detailKind: 'aggregation',
		detailPayload: {
			kind: 'aggregation',
			candidateCount: candidateScores.length,
			bestScore: highScore,
			finalTopStatus,
			finalTopCandidateIds: finalCandidateIds,
			finalReport: finalReport.report,
			stopReason: finalReport.stopReason,
			coverageStatus: runtimeSourceState?.coverageStatus ?? null,
			finalizationRevision: runtimeSourceState?.finalizationRevision ?? null,
			finalizationReasonCode: runtimeSourceState?.finalizationReasonCode ?? null,
			identityMergeCount: runtimeSourceState?.identityMergeCount ?? 0,
			ambiguousDuplicateCount: runtimeSourceState?.ambiguousDuplicateCount ?? 0,
			canonicalResumeSelectedCount: runtimeSourceState?.canonicalResumeSelectedCount ?? 0,
			sourceStates: runtimeSourceState?.sources ?? []
		} as RecruiterGraphNode['detailPayload'],
		eventIds: finalReport.eventId ? [finalReport.eventId] : [],
		sourceRunId: null,
		candidateReviewItemIds: finalCandidateIds,
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	if (mergeNodeId) {
		graphEdges.push({ from: mergeNodeId, to: finalId, tone: 'green', label: 'Top 10' });
	} else {
		for (const sourceNodeId of sourceAnchors) {
			graphEdges.push({ from: sourceNodeId, to: finalId, tone: 'green', label: '聚合排序' });
		}
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
			relatedNodeId: finalId
		});
	}
	return finalId;
}

function appendMergeNode({
	graphEdges,
	graphNodes,
	runtimeSourceState,
	sourceAnchors
}: {
	graphEdges: RecruiterGraphEdge[];
	graphNodes: RecruiterGraphNode[];
	runtimeSourceState: WorkbenchRuntimeSourceState;
	sourceAnchors: string[];
}): string {
	const mergeId = 'merge-dedupe';
	const canonicalCount = runtimeSourceState.canonicalResumeSelectedCount;
	const ambiguousCount = runtimeSourceState.ambiguousDuplicateCount;
	graphNodes.push({
		id: mergeId,
		at: graphNodes.length,
		kind: '排序',
		label: `跨源合并 · ${String(runtimeSourceState.identityMergeCount)} 组`,
		detail: `规范简历 ${String(canonicalCount)} · 模糊重复 ${String(ambiguousCount)}`,
		x: 86,
		y: 50,
		tone: ambiguousCount > 0 ? 'violet' : 'blue',
		sourceKind: 'all',
		sourceLabel: 'All sources',
		lane: 'shared',
		detailKind: 'aggregation',
		detailPayload: {
			kind: 'aggregation',
			candidateCount: runtimeSourceState.canonicalResumeSelectedCount,
			bestScore: null,
			finalReport: null,
			stopReason: null,
			coverageStatus: runtimeSourceState.coverageStatus,
			finalizationRevision: runtimeSourceState.finalizationRevision ?? null,
			finalizationReasonCode: runtimeSourceState.finalizationReasonCode ?? null,
			identityMergeCount: runtimeSourceState.identityMergeCount,
			ambiguousDuplicateCount: runtimeSourceState.ambiguousDuplicateCount,
			canonicalResumeSelectedCount: runtimeSourceState.canonicalResumeSelectedCount,
			sourceStates: runtimeSourceState.sources
		} as RecruiterGraphNode['detailPayload'],
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	for (const sourceNodeId of sourceAnchors) {
		graphEdges.push({ from: sourceNodeId, to: mergeId, tone: 'violet', label: '身份合并' });
	}
	return mergeId;
}

function sourceNode(
	input: Omit<RecruiterGraphNode, 'at' | 'lane' | 'sourceLabel'> & {
		sourceKind: SourceKind;
		sourceLabel: string;
	}
): RecruiterGraphNode {
	return {
		...input,
		at: 0,
		lane: input.sourceKind,
		sourceLabel: input.sourceLabel
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
	sourceFilter: SourceFilter
): SourceKind[] {
	if (sourceFilter !== 'all') {
		return [sourceFilter];
	}
	return uniqueSourceKinds([
		...(session.runtimeSourceState?.selectedSourceKinds ?? []),
		...(session.runtimeSourceState?.sources.map((source) => source.sourceKind) ?? []),
		...session.sourceCards.map((card) => card.sourceKind),
		...scopedEvents.flatMap((event) => (event.sourceKind ? [event.sourceKind] : []))
	]);
}

function appendSourcePlanNode({
	anchor,
	graphEdges,
	graphNodes,
	runtimeSourceState,
	sourceKinds
}: {
	anchor: string;
	graphEdges: RecruiterGraphEdge[];
	graphNodes: RecruiterGraphNode[];
	runtimeSourceState: WorkbenchRuntimeSourceState | null;
	sourceKinds: SourceKind[];
}): string {
	if (!runtimeSourceState || sourceKinds.length === 0) {
		return anchor;
	}
	const sourceLabelsText = sourceKinds.map((sourceKind) => sourceLabels[sourceKind]).join(' + ');
	const planId = 'source-plan';
	graphNodes.push({
		id: planId,
		at: graphNodes.length,
		kind: '检索',
		label: `来源计划 · ${sourceLabelsText}`,
		detail: coverageStatusDetail(runtimeSourceState.coverageStatus),
		x: 28,
		y: 50,
		tone: runtimeSourceState.coverageStatus === 'complete' ? 'blue' : 'amber',
		sourceKind: 'all',
		sourceLabel: 'All sources',
		lane: 'shared',
		detailKind: 'aggregation',
		detailPayload: {
			kind: 'aggregation',
			candidateCount: runtimeSourceState.canonicalResumeSelectedCount,
			bestScore: null,
			finalReport: null,
			stopReason: null,
			coverageStatus: runtimeSourceState.coverageStatus,
			finalizationRevision: runtimeSourceState.finalizationRevision ?? null,
			finalizationReasonCode: runtimeSourceState.finalizationReasonCode ?? null,
			identityMergeCount: runtimeSourceState.identityMergeCount,
			ambiguousDuplicateCount: runtimeSourceState.ambiguousDuplicateCount,
			canonicalResumeSelectedCount: runtimeSourceState.canonicalResumeSelectedCount,
			sourceStates: runtimeSourceState.sources
		} as RecruiterGraphNode['detailPayload'],
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	graphEdges.push({ from: anchor, to: planId, tone: 'blue', label: '选择来源' });
	return planId;
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
		reflect: base + 14
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
	runtimeLaneState: WorkbenchRuntimeSourceLaneState | null
): RecruiterGraphNode['detailPayload'] & { kind: 'sourceQueue' } {
	const warningCode =
		runtimeLaneState?.reasonCode ??
		sourceCard?.warningCode ??
		sourceCard?.connectionWarningCode ??
		null;
	const warningMessage = displaySafeWarning(
		warningCode,
		sourceCard?.warningMessage ?? sourceCard?.connectionWarningMessage ?? null
	);
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
		runtimeStatus: runtimeLaneState?.status ?? null,
		runtimeEventType: runtimeLaneState?.eventType ?? null,
		runtimeEventSeq: runtimeLaneState?.eventSeq ?? null,
		runtimeCardsSeenCount: runtimeLaneState?.cardsSeenCount ?? 0,
		runtimeCardsFilteredCount: runtimeLaneState?.cardsFilteredCount ?? 0,
		runtimeCandidatesCount: runtimeLaneState?.candidatesCount ?? 0,
		runtimeDetailRecommendationsCount: runtimeLaneState?.detailRecommendationsCount ?? 0,
		runtimeDetailState: runtimeLaneState?.detailState ?? null
	};
}

function liepinQueueDetail(
	sourceCard: WorkbenchSession['sourceCards'][number] | undefined,
	runtimeLaneState: WorkbenchRuntimeSourceLaneState | null
): string {
	if (
		runtimeLaneState?.reasonCode === 'liepin_browser_probe_unavailable' ||
		runtimeLaneState?.reasonCode === 'blocked_backend_unavailable'
	) {
		return '浏览器通道不可用';
	}
	if (runtimeLaneState?.reasonCode === 'liepin_browser_account_mismatch') {
		return '猎聘账号不一致';
	}
	if (sourceCard?.connectionStatus === 'connected') {
		return '账号已连接，串行抓取简介';
	}
	return '等待猎聘登录';
}

function runtimeLaneStateForSource(
	runtimeSourceState: WorkbenchRuntimeSourceState | null,
	sourceKind: SourceKind
): WorkbenchRuntimeSourceLaneState | null {
	return runtimeSourceState?.sources.find((source) => source.sourceKind === sourceKind) ?? null;
}

function runtimeSourceStateForFilter(
	runtimeSourceState: WorkbenchRuntimeSourceState | null,
	sourceFilter: SourceFilter
): WorkbenchRuntimeSourceState | null {
	if (!runtimeSourceState || sourceFilter === 'all') {
		return runtimeSourceState;
	}
	const sources = runtimeSourceState.sources.filter((source) => source.sourceKind === sourceFilter);
	if (sources.length === 0) {
		return null;
	}
	return {
		...runtimeSourceState,
		selectedSourceKinds: [sourceFilter],
		sources,
		coverageStatus: coverageStatusFromLaneStates(sources)
	};
}

function runtimeSourceStateHasActivity(
	runtimeSourceState: WorkbenchRuntimeSourceState | null
): boolean {
	if (!runtimeSourceState) {
		return false;
	}
	if (
		runtimeSourceState.coverageStatus !== 'pending' ||
		runtimeSourceState.finalizationRevision != null ||
		runtimeSourceState.finalizationReasonCode != null ||
		runtimeSourceState.identityMergeCount > 0 ||
		runtimeSourceState.ambiguousDuplicateCount > 0 ||
		runtimeSourceState.canonicalResumeSelectedCount > 0
	) {
		return true;
	}
	return runtimeSourceState.sources.some(
		(source) =>
			source.status !== 'pending' ||
			source.eventType != null ||
			source.eventSeq != null ||
			source.reasonCode != null ||
			source.cardsSeenCount > 0 ||
			source.cardsFilteredCount > 0 ||
			source.candidatesCount > 0 ||
			source.detailRecommendationsCount > 0 ||
			source.detailState != null
	);
}

function coverageStatusFromLaneStates(
	sources: WorkbenchRuntimeSourceLaneState[]
): WorkbenchRuntimeSourceState['coverageStatus'] {
	if (sources.some((source) => source.status === 'pending' || source.status === 'running')) {
		return 'pending';
	}
	if (
		sources.some((source) => ['partial', 'blocked', 'failed', 'cancelled'].includes(source.status))
	) {
		return 'degraded';
	}
	if (sources.every((source) => source.status === 'completed' && source.candidatesCount === 0)) {
		return 'empty';
	}
	return 'complete';
}

function coverageStatusDetail(status: WorkbenchRuntimeSourceState['coverageStatus']): string {
	if (status === 'pending') {
		return '等待来源回传';
	}
	if (status === 'degraded') {
		return '部分来源降级';
	}
	if (status === 'empty') {
		return '来源无候选人';
	}
	return '来源计划已完成';
}

function finalReportFromEvents(runtimeEvents: RuntimeEventData[]): FinalReport {
	const completed = [...runtimeEvents]
		.reverse()
		.find(
			(item) =>
				item.event.eventName === 'runtime_finalizer_completed' ||
				item.event.eventName === 'runtime_run_completed'
		);
	if (!completed) {
		return { report: null, stopReason: null, eventId: null };
	}
	const report =
		completed.message ||
		stringValue(completed.payload.final_report) ||
		stringValue(completed.payload.finalReport) ||
		stringValue(completed.payload.summary) ||
		null;
	const stopReason =
		stringValue(completed.payload.stop_reason) ?? stringValue(completed.payload.stopReason) ?? null;
	return {
		report,
		stopReason,
		eventId: eventId(completed.event)
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
		roundNo:
			numberValue(outer.roundNo) ??
			numberValue(outer.round_no) ??
			numberValue(inner.round_no) ??
			numberValue(inner.roundNo),
		message: stringValue(outer.message) ?? stringValue(inner.message) ?? ''
	};
}

function criteriaFromRequirements(
	requirements: RuntimeEventData | undefined
): WorkbenchRequirementTriageInput {
	if (!requirements) {
		return emptyCriteria;
	}
	const queryHints = [
		...stringsValue(requirements.payload.search_terms),
		...stringsValue(requirements.payload.query_terms),
		...stringsValue(requirements.payload.notes_query_terms)
	];
	return {
		mustHaves: stringsValue(requirements.payload.must_have_capabilities),
		niceToHaves: stringsValue(requirements.payload.preferred_capabilities),
		synonyms: stringsValue(requirements.payload.synonyms),
		seniorityFilters: stringsValue(requirements.payload.seniority_filters),
		exclusions: stringsValue(requirements.payload.exclusions),
		generatedQueryHints: uniqueStrings(queryHints)
	};
}

export function criteriaFromTriage(
	triage: WorkbenchRequirementTriage
): WorkbenchRequirementTriageInput {
	return {
		mustHaves: [...triage.mustHaves],
		niceToHaves: [...triage.niceToHaves],
		synonyms: [...triage.synonyms],
		seniorityFilters: [...triage.seniorityFilters],
		exclusions: [...triage.exclusions],
		generatedQueryHints: [...triage.generatedQueryHints]
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
	const groups = new Map<string, RoundAccumulator>();
	const roundEventNames = new Set([
		'runtime_controller_started',
		'runtime_controller_completed',
		'runtime_search_started',
		'runtime_search_completed',
		'runtime_scoring_started',
		'runtime_scoring_completed',
		'runtime_round_completed',
		'runtime_reflection_started',
		'runtime_reflection_completed'
	]);
	for (const item of [...events].sort(
		(left, right) => left.event.globalSeq - right.event.globalSeq
	)) {
		if (!roundEventNames.has(item.event.eventName)) {
			continue;
		}
		const roundNo =
			item.roundNo ?? numberValue(item.payload.round_no) ?? numberValue(item.payload.roundNo) ?? 0;
		if (roundNo <= 0) {
			continue;
		}
		const sourceRunId = item.event.sourceRunId;
		const key = `${sourceRunId ?? 'source:none'}:${String(roundNo)}`;
		const round = groups.get(key) ?? emptyRoundAccumulator(item, roundNo);
		mergeRoundEvent(round, item);
		groups.set(key, round);
	}

	return [...groups.values()]
		.map((round) => ({
			eventSeq: round.eventSeq,
			eventIds: round.eventIds,
			sourceRunId: round.sourceRunId,
			roundNo: round.roundNo,
			queryTerms: uniqueStrings(round.queryTerms),
			queryLabel: round.queryLabel || listText(uniqueStrings(round.queryTerms)),
			executedQueries: round.executedQueries,
			rawCandidateCount: round.rawCandidateCount ?? 0,
			uniqueNewCount: round.uniqueNewCount ?? 0,
			recallCounts: round.recallCounts,
			newlyScoredCount: round.newlyScoredCount ?? round.scoredCount ?? 0,
			scoredCount: round.scoredCount ?? round.newlyScoredCount ?? 0,
			fitCount: round.fitCount ?? 0,
			notFitCount: round.notFitCount ?? 0,
			reflectionSummary: round.reflectionSummary,
			reflectionRationale: round.reflectionRationale,
			nextDirection: round.nextDirection,
			hasControllerStarted: round.hasControllerStarted,
			hasControllerCompleted: round.hasControllerCompleted,
			hasSearchStarted: round.hasSearchStarted,
			hasSearchCompleted: round.hasSearchCompleted,
			hasScoringStarted: round.hasScoringStarted,
			hasScoringCompleted: round.hasScoringCompleted,
			hasReflectionStarted: round.hasReflectionStarted,
			hasReflectionCompleted: round.hasReflectionCompleted
		}))
		.sort(
			(left, right) =>
				left.roundNo - right.roundNo ||
				(left.sourceRunId ?? '').localeCompare(right.sourceRunId ?? '') ||
				left.eventSeq - right.eventSeq
		);
}

type RoundAccumulator = {
	eventSeq: number;
	eventIds: string[];
	sourceRunId: string | null;
	roundNo: number;
	queryTerms: string[];
	queryLabel: string;
	executedQueries: ExecutedQuerySummary[];
	rawCandidateCount: number | null;
	uniqueNewCount: number | null;
	recallCounts: Record<string, unknown> | null;
	newlyScoredCount: number | null;
	scoredCount: number | null;
	fitCount: number | null;
	notFitCount: number | null;
	reflectionSummary: string;
	reflectionRationale: string;
	nextDirection: string;
	hasSearch: boolean;
	hasScoring: boolean;
	hasControllerStarted: boolean;
	hasControllerCompleted: boolean;
	hasSearchStarted: boolean;
	hasSearchCompleted: boolean;
	hasScoringStarted: boolean;
	hasScoringCompleted: boolean;
	hasReflectionStarted: boolean;
	hasReflectionCompleted: boolean;
};

function emptyRoundAccumulator(item: RuntimeEventData, roundNo: number): RoundAccumulator {
	return {
		eventSeq: item.event.globalSeq,
		eventIds: [],
		sourceRunId: item.event.sourceRunId ?? null,
		roundNo,
		queryTerms: [],
		queryLabel: '',
		executedQueries: [],
		rawCandidateCount: null,
		uniqueNewCount: null,
		recallCounts: null,
		newlyScoredCount: null,
		scoredCount: null,
		fitCount: null,
		notFitCount: null,
		reflectionSummary: '',
		reflectionRationale: '',
		nextDirection: '',
		hasSearch: false,
		hasScoring: false,
		hasControllerStarted: false,
		hasControllerCompleted: false,
		hasSearchStarted: false,
		hasSearchCompleted: false,
		hasScoringStarted: false,
		hasScoringCompleted: false,
		hasReflectionStarted: false,
		hasReflectionCompleted: false
	};
}

function mergeRoundEvent(round: RoundAccumulator, item: RuntimeEventData): void {
	round.eventSeq = Math.min(round.eventSeq, item.event.globalSeq);
	round.eventIds = uniqueStrings([...round.eventIds, eventId(item.event)]);
	if (item.event.eventName === 'runtime_controller_started') {
		round.hasControllerStarted = true;
	}
	if (item.event.eventName === 'runtime_controller_completed') {
		round.hasControllerStarted = true;
		round.hasControllerCompleted = true;
		mergeSearchPayload(round, item.payload);
	}
	if (item.event.eventName === 'runtime_search_started') {
		round.hasSearchStarted = true;
		mergeSearchPayload(round, item.payload);
	}
	if (
		item.event.eventName === 'runtime_search_completed' ||
		(item.event.eventName === 'runtime_round_completed' && !round.hasSearch)
	) {
		round.hasSearchStarted = true;
		round.hasSearchCompleted = true;
		mergeSearchPayload(round, item.payload);
		if (item.event.eventName === 'runtime_search_completed') {
			round.hasSearch = true;
		}
	}
	if (item.event.eventName === 'runtime_scoring_started') {
		round.hasScoringStarted = true;
		round.newlyScoredCount =
			metricValue(item.payload, 'candidate_count', 'candidateCount') ?? round.newlyScoredCount;
	}
	if (
		item.event.eventName === 'runtime_scoring_completed' ||
		(item.event.eventName === 'runtime_round_completed' && !round.hasScoring)
	) {
		round.hasScoringStarted = true;
		round.hasScoringCompleted = true;
		mergeScoringPayload(round, item.payload);
		if (item.event.eventName === 'runtime_scoring_completed') {
			round.hasScoring = true;
		}
	}
	if (item.event.eventName === 'runtime_reflection_started') {
		round.hasReflectionStarted = true;
	}
	if (
		item.event.eventName === 'runtime_round_completed' ||
		item.event.eventName === 'runtime_reflection_completed'
	) {
		round.hasReflectionStarted = true;
		round.hasReflectionCompleted = true;
		mergeReflectionPayload(round, item.payload);
	}
}

function mergeSearchPayload(round: RoundAccumulator, payload: Record<string, unknown>): void {
	const executedQueries = executedQueriesFromPayload(payload);
	round.executedQueries = uniqueExecutedQueries([...round.executedQueries, ...executedQueries]);
	round.queryTerms = uniqueStrings([...round.queryTerms, ...queryTermsFromPayload(payload)]);
	round.queryLabel =
		queryLabelFromExecutedQueries(round.executedQueries) ||
		listText(round.queryTerms) ||
		round.queryLabel;
	round.rawCandidateCount =
		metricValue(payload, 'raw_candidate_count', 'rawCandidateCount') ?? round.rawCandidateCount;
	round.uniqueNewCount =
		metricValue(payload, 'unique_new_count', 'uniqueNewCount') ?? round.uniqueNewCount;
	round.recallCounts =
		recordValue(payload.recall_counts) ?? recordValue(payload.recallCounts) ?? round.recallCounts;
}

function mergeScoringPayload(round: RoundAccumulator, payload: Record<string, unknown>): void {
	const scoredCount =
		metricValue(payload, 'scored_count', 'scoredCount') ??
		metricValue(payload, 'newly_scored_count', 'newlyScoredCount');
	round.scoredCount = scoredCount ?? round.scoredCount;
	round.newlyScoredCount = scoredCount ?? round.newlyScoredCount;
	round.fitCount = metricValue(payload, 'fit_count', 'fitCount') ?? round.fitCount;
	round.notFitCount = metricValue(payload, 'not_fit_count', 'notFitCount') ?? round.notFitCount;
}

function mergeReflectionPayload(round: RoundAccumulator, payload: Record<string, unknown>): void {
	round.reflectionSummary =
		stringValue(payload.reflection_summary) ??
		stringValue(payload.reflectionSummary) ??
		stringValue(payload.reflection_rationale) ??
		round.reflectionSummary;
	round.reflectionRationale =
		stringValue(payload.reflection_rationale) ??
		stringValue(payload.reflectionRationale) ??
		round.reflectionRationale;
	round.nextDirection =
		stringValue(payload.next_direction) ??
		stringValue(payload.nextDirection) ??
		round.nextDirection;
}

function candidateScoresFromInputs(
	events: WorkbenchEvent[],
	candidateReviewItems: WorkbenchCandidateReviewItem[] = [],
	sourceKindFilter?: SourceKind
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
			sourceKind: event.sourceKind ?? null,
			eventSeq: event.globalSeq
		});
	}
	for (const [index, item] of candidateReviewItems.entries()) {
		const scopedEvidence = sourceKindFilter
			? item.evidence.filter((evidence) => evidence.sourceKind === sourceKindFilter)
			: item.evidence;
		const score = sourceKindFilter
			? (maxNumber(scopedEvidence.map((evidence) => evidence.score ?? null)) ??
				item.aggregateScore ??
				null)
			: (item.aggregateScore ??
				firstNumber(scopedEvidence.map((evidence) => evidence.score ?? null)));
		if (score === null || score === undefined) {
			continue;
		}
		const sourceKind =
			sourceKindFilter ??
			scopedEvidence.find((evidence) => evidence.sourceKind)?.sourceKind ??
			null;
		byReviewItemId.set(item.reviewItemId, {
			reviewItemId: item.reviewItemId,
			score,
			sourceKind,
			eventSeq: byReviewItemId.get(item.reviewItemId)?.eventSeq ?? 100_000 + index
		});
	}
	return [...byReviewItemId.values()].sort((left, right) => left.eventSeq - right.eventSeq);
}

function candidateScoresFromFinalTopCandidates(
	finalTopCandidates: WorkbenchFinalTopCandidate[]
): CandidateScore[] {
	return finalTopCandidates
		.map((candidate, index) => ({
			reviewItemId: candidate.reviewItemId,
			score:
				candidate.aggregateScore ??
				maxNumber(candidate.sourceEvidence.map((evidence) => evidence.score ?? null)) ??
				0,
			sourceKind: null,
			eventSeq: candidate.rank > 0 ? candidate.rank : index + 1
		}))
		.sort((left, right) => left.eventSeq - right.eventSeq);
}

function queryLabelFromExecutedQueries(queries: ExecutedQuerySummary[]): string {
	const labels = queries
		.map((item) => {
			return listText(item.query_terms);
		})
		.filter(Boolean);
	if (labels.length > 0) {
		return labels.join(' / ');
	}
	return '';
}

function executedQueriesFromPayload(payload: Record<string, unknown>): ExecutedQuerySummary[] {
	const executed = Array.isArray(payload.executed_queries)
		? payload.executed_queries
		: Array.isArray(payload.executedQueries)
			? payload.executedQueries
			: [];
	const planned = Array.isArray(payload.planned_queries)
		? payload.planned_queries
		: Array.isArray(payload.plannedQueries)
			? payload.plannedQueries
			: [];
	const queries = [...executed, ...planned];
	return queries.flatMap((item) => {
		const query = recordValue(item);
		if (!query) {
			return [];
		}
		return [
			{
				query_role: stringValue(query.query_role) ?? stringValue(query.queryRole),
				lane_type: stringValue(query.lane_type) ?? stringValue(query.laneType),
				query_terms: uniqueStrings([
					...stringsValue(query.query_terms),
					...stringsValue(query.queryTerms)
				]),
				keyword_query: stringValue(query.keyword_query) ?? stringValue(query.keywordQuery),
				query_instance_id:
					stringValue(query.query_instance_id) ?? stringValue(query.queryInstanceId),
				query_fingerprint:
					stringValue(query.query_fingerprint) ?? stringValue(query.queryFingerprint)
			}
		];
	});
}

function uniqueExecutedQueries(queries: ExecutedQuerySummary[]): ExecutedQuerySummary[] {
	const seen = new Set<string>();
	const uniqueQueries: ExecutedQuerySummary[] = [];
	for (const query of queries) {
		const key = [
			query.query_instance_id,
			query.query_fingerprint,
			query.lane_type,
			query.query_role,
			query.keyword_query,
			query.query_terms.join('\u0000')
		].join('\u0001');
		if (seen.has(key)) {
			continue;
		}
		seen.add(key);
		uniqueQueries.push(query);
	}
	return uniqueQueries;
}

function scopeCandidateReviewItems(
	candidateReviewItems: WorkbenchCandidateReviewItem[],
	sourceKind: SourceKind
): WorkbenchCandidateReviewItem[] {
	return candidateReviewItems.filter((item) =>
		item.evidence.some((evidence) => evidence.sourceKind === sourceKind)
	);
}

function scopeDetailOpenRequests(
	detailOpenRequests: WorkbenchDetailOpenRequest[]
): WorkbenchDetailOpenRequest[] {
	return detailOpenRequests;
}

function detailRequestFields(detailOpenRequests: WorkbenchDetailOpenRequest[]) {
	const requestIds = detailOpenRequests.map((request) => request.requestId);
	return {
		detailOpenRequestIds: requestIds,
		requestIds,
		requestSummaries: detailOpenRequests.map(detailRequestSummary),
		budgetText: detailBudgetText(detailOpenRequests)
	};
}

function detailApprovalCounts(
	detailOpenRequests: WorkbenchDetailOpenRequest[],
	detailEvents: WorkbenchEvent[]
) {
	const currentRequestIds = new Set(detailOpenRequests.map((request) => request.requestId));
	const currentReviewItemIds = new Set(detailOpenRequests.map((request) => request.reviewItemId));
	const eventOnlyRequests = new Map<string, { leased: boolean; blocked: boolean }>();

	for (const event of detailEvents) {
		const requestId = stringValue(event.payload.requestId);
		const reviewItemId = stringValue(event.payload.reviewItemId);
		if (
			(requestId && currentRequestIds.has(requestId)) ||
			(reviewItemId && currentReviewItemIds.has(reviewItemId))
		) {
			continue;
		}
		const requestKey = reviewItemId ?? requestId ?? eventId(event);
		const current = eventOnlyRequests.get(requestKey) ?? { leased: false, blocked: false };
		if (event.eventName === 'liepin_detail_open_leased') {
			current.leased = true;
		}
		if (event.eventName === 'liepin_detail_open_blocked') {
			current.blocked = true;
		}
		eventOnlyRequests.set(requestKey, current);
	}
	const eventOnlyStatuses = [...eventOnlyRequests.values()];

	return {
		requestCount: detailOpenRequests.length + eventOnlyRequests.size,
		approvedOrLeasedCount:
			detailOpenRequests.filter(isApprovedOrLeasedDetailRequest).length +
			eventOnlyStatuses.filter((status) => status.leased).length,
		blockedOrRejectedCount:
			detailOpenRequests.filter(isBlockedOrRejectedDetailRequest).length +
			eventOnlyStatuses.filter((status) => status.blocked).length
	};
}

function isApprovedOrLeasedDetailRequest(request: WorkbenchDetailOpenRequest): boolean {
	return (
		request.status === 'approved' ||
		request.ledger?.status === 'leased' ||
		request.ledger?.status === 'opened' ||
		request.ledger?.status === 'maybe_used'
	);
}

function isBlockedOrRejectedDetailRequest(request: WorkbenchDetailOpenRequest): boolean {
	return (
		request.status === 'rejected' ||
		request.status === 'blocked' ||
		request.ledger?.status === 'blocked'
	);
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

function displaySafeWarning(
	warningCode: string | null,
	warningMessage: string | null
): string | null {
	const reasonLabel = sourceReasonLabel(warningCode);
	if (reasonLabel) {
		return reasonLabel;
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
	sourceKind: SourceKind
): RecruiterCandidateEvidenceRef[] {
	return candidateReviewItems.flatMap((item) =>
		item.evidence
			.filter((evidence) => evidence.sourceKind === sourceKind)
			.map((evidence) => ({
				evidenceId: evidence.evidenceId,
				reviewItemId: item.reviewItemId,
				sourceRunId: evidence.sourceRunId,
				sourceKind: evidence.sourceKind,
				evidenceLevel: evidence.evidenceLevel
			}))
	);
}

function detailRequestSummary(request: WorkbenchDetailOpenRequest): string {
	const candidateLabel = request.candidate?.displayName || '待审批候选人';
	return [
		candidateLabel,
		detailRequestStatusLabel(request.status),
		ledgerStatusLabel(request.ledger?.status)
	]
		.filter(Boolean)
		.join(' · ');
}

function detailRequestStatusLabel(status: string) {
	const labels: Record<string, string> = {
		pending: '待处理',
		approved: '已批准',
		rejected: '已拒绝',
		blocked: '已阻塞',
		bypassed: '已跳过'
	};
	return labels[status] ?? status;
}

function ledgerStatusLabel(status: string | null | undefined) {
	const labels: Record<string, string> = {
		leased: '已预留',
		opened: '已打开',
		released: '已释放'
	};
	return status ? (labels[status] ?? status) : null;
}

function queryTermsFromPayload(payload: Record<string, unknown>): string[] {
	const executed = Array.isArray(payload.executed_queries)
		? payload.executed_queries
		: Array.isArray(payload.executedQueries)
			? payload.executedQueries
			: [];
	const planned = Array.isArray(payload.planned_queries)
		? payload.planned_queries
		: Array.isArray(payload.plannedQueries)
			? payload.plannedQueries
			: [];
	const executedQueries = [...executed, ...planned];
	const executedTerms = executedQueries.flatMap((item) => {
		const query = recordValue(item);
		return query ? [...stringsValue(query.query_terms), ...stringsValue(query.queryTerms)] : [];
	});
	return uniqueStrings([
		...executedTerms,
		...stringsValue(payload.query_terms),
		...stringsValue(payload.queryTerms),
		...stringsValue(payload.search_terms),
		...stringsValue(payload.searchTerms)
	]);
}

function metricValue(
	payload: Record<string, unknown>,
	snakeKey: string,
	camelKey: string
): number | null {
	return numberValue(payload[snakeKey]) ?? numberValue(payload[camelKey]);
}

function firstNumber(values: Array<number | null>): number | null {
	return values.find((value) => value !== null) ?? null;
}

function maxNumber(values: Array<number | null>): number | null {
	const numbers = values.filter((value): value is number => value !== null);
	return numbers.length > 0 ? Math.max(...numbers) : null;
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

function firstTimestamp(
	events: WorkbenchEvent[],
	eventNames: string[],
	reverse = false
): Date | null {
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

function uniqueSortedNumbers(values: number[]): number[] {
	return [...new Set(values.filter((value) => Number.isFinite(value)))].sort(
		(left, right) => left - right
	);
}

function uniqueSourceKinds(values: SourceKind[]): SourceKind[] {
	return values.filter((value, index, array) => array.indexOf(value) === index);
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
	return value !== null && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}
