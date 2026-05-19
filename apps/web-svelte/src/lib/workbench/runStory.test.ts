import { describe, expect, it } from 'vitest';

import { buildRunStory } from './runStory';

type BuildRunStoryInput = Parameters<typeof buildRunStory>[0];
type WorkbenchSession = BuildRunStoryInput['session'];
type WorkbenchEvent = BuildRunStoryInput['events'][number];
type WorkbenchCandidateReviewItem = NonNullable<BuildRunStoryInput['candidateReviewItems']>[number];
type WorkbenchDetailOpenRequest = NonNullable<BuildRunStoryInput['detailOpenRequests']>[number];
type WorkbenchRequirementTriage = WorkbenchSession['requirementTriage'];

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
					approvedAt: null
				})
			}),
			events: [
				event({
					globalSeq: 2,
					sourceKind: null,
					sourceRunId: null,
					eventName: 'runtime_requirements_started',
					payload: {
						message: '正在分析岗位标题、JD 和 notes。',
						roundNo: null,
						stage: 'requirements'
					}
				})
			]
		});

		expect(story.graphNodes.find((node) => node.id === 'requirements')).toMatchObject({
			label: '需求拆解',
			detail: '正在拆解岗位需求'
		});
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'job', to: 'requirements' })
		);
	});

	it('builds non-trivial CTS and Liepin lanes with candidate and detail metadata', () => {
		const story = buildRunStory({
			session: session(),
			events,
			candidateReviewItems: [candidateReviewItem()],
			detailOpenRequests: [detailOpenRequest()],
			sourceFilter: 'all'
		});

		expect(story.graphNodes.map((node) => node.id)).toEqual(
			expect.arrayContaining([
				'job',
				'requirements',
				'cts-round-1-query',
				'cts-round-1-result',
				'cts-round-1-score',
				'cts-round-1-reflect',
				'liepin-card-search',
				'liepin-card-candidates',
				'liepin-detail-approval',
				'final-shortlist'
			])
		);
		expect(story.graphEdges).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ label: 'CTS 检索' }),
				expect.objectContaining({ label: '猎聘简介抓取' }),
				expect.objectContaining({ label: '详情队列' }),
				expect.objectContaining({ label: '聚合排序' })
			])
		);

		const candidates = story.graphNodes.find((node) => node.id === 'liepin-card-candidates');
		const detailApproval = story.graphNodes.find((node) => node.id === 'liepin-detail-approval');
		const finalShortlist = story.graphNodes.find((node) => node.id === 'final-shortlist');

		expect(candidates?.candidateReviewItemIds).toEqual(['review-liepin-1']);
		expect(candidates?.candidateEvidenceRefs).toEqual([
			{
				evidenceId: 'evidence-liepin-1',
				reviewItemId: 'review-liepin-1',
				sourceRunId: 'src-liepin',
				sourceKind: 'liepin',
				evidenceLevel: 'detail'
			}
		]);
		expect(candidates?.detailPayload).toMatchObject({
			kind: 'liepinCardCandidates',
			candidateReviewItemIds: ['review-liepin-1'],
			bestScore: 93
		});
		expect(detailApproval?.detailPayload).toMatchObject({
			kind: 'liepinDetailApproval',
			requestIds: ['detail-request-1'],
			requestSummaries: ['Ada Chen · approved · leased'],
			budgetText: 'approved · leased'
		});
		expect(finalShortlist?.detail).toBe('最高 93 分');
		expect(story.completionText).toBe('检索完成 · 候选人进入短名单');
	});

	it('filters source-specific graph nodes and workbench notes', () => {
		const noteEvents = [
			...events,
			noteEvent('CTS note', { globalSeq: 50, sourceKind: 'cts', sourceRunId: 'src-cts' }),
			noteEvent('Liepin detail note', {
				globalSeq: 51,
				sourceKind: 'liepin',
				sourceRunId: 'src-liepin'
			})
		];
		const ctsStory = buildRunStory({ session: session(), events: noteEvents, sourceFilter: 'cts' });
		const liepinStory = buildRunStory({
			session: session(),
			events: noteEvents,
			sourceFilter: 'liepin'
		});

		expect(ctsStory.graphNodes.some((node) => node.sourceKind === 'liepin')).toBe(false);
		expect(ctsStory.logEntries.some((entry) => entry.sourceKind === 'liepin')).toBe(false);
		expect(liepinStory.graphNodes.some((node) => node.sourceKind === 'cts')).toBe(false);
		expect(liepinStory.logEntries.some((entry) => entry.sourceKind === 'cts')).toBe(false);
		expect(liepinStory.logEntries.some((entry) => entry.text.includes('detail'))).toBe(true);
	});

	it('projects runtime source public state into source queue and final graph details', () => {
		const story = buildRunStory({
			session: session({
				runtimeSourceState: {
					selectedSourceKinds: ['cts', 'liepin'],
					coverageStatus: 'degraded',
					finalizationRevision: 1,
					finalizationReasonCode: 'source_lanes_degraded',
					identityMergeCount: 2,
					ambiguousDuplicateCount: 1,
					canonicalResumeSelectedCount: 9,
					sources: [
						{
							sourceKind: 'cts',
							status: 'completed',
							eventType: 'source_lane_completed',
							eventSeq: 2,
							cardsSeenCount: 10,
							cardsFilteredCount: 0,
							candidatesCount: 10,
							detailRecommendationsCount: 0,
							detailState: null
						},
						{
							sourceKind: 'liepin',
							status: 'partial',
							eventType: 'detail_recommended',
							eventSeq: 4,
							cardsSeenCount: 30,
							cardsFilteredCount: 8,
							candidatesCount: 5,
							detailRecommendationsCount: 4,
							detailState: 'detail_recommended'
						}
					]
				}
			}),
			events,
			candidateReviewItems: [candidateReviewItem()],
			sourceFilter: 'all'
		});

		expect(story.graphNodes.find((node) => node.id === 'cts-source-start')?.detail).toBe(
			'扫描 10 · 命中 10'
		);
		expect(
			story.graphNodes.find((node) => node.id === 'liepin-source-start')?.detailPayload
		).toMatchObject({
			kind: 'sourceQueue',
			runtimeStatus: 'partial',
			runtimeEventType: 'detail_recommended',
			runtimeCardsSeenCount: 30,
			runtimeCardsFilteredCount: 8,
			runtimeCandidatesCount: 5,
			runtimeDetailRecommendationsCount: 4,
			runtimeDetailState: 'detail_recommended'
		});
		expect(
			story.graphNodes.find((node) => node.id === 'final-shortlist')?.detailPayload
		).toMatchObject({
			kind: 'aggregation',
			coverageStatus: 'degraded',
			finalizationRevision: 1,
			finalizationReasonCode: 'source_lanes_degraded',
			identityMergeCount: 2,
			ambiguousDuplicateCount: 1,
			canonicalResumeSelectedCount: 9,
			sourceStates: [
				expect.objectContaining({ sourceKind: 'cts', status: 'completed', candidatesCount: 10 }),
				expect.objectContaining({
					sourceKind: 'liepin',
					status: 'partial',
					cardsFilteredCount: 8,
					detailRecommendationsCount: 4
				})
			]
		});
	});

	it('uses safe Liepin browser reason copy in source queue details', () => {
		const story = buildRunStory({
			session: session({
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
						warningMessage: null
					},
					{
						sourceRunId: 'src-liepin',
						sourceKind: 'liepin',
						label: 'Liepin',
						status: 'blocked',
						authState: 'login_required',
						cardsScannedCount: 0,
						uniqueCandidatesCount: 0,
						detailOpenUsedCount: 0,
						detailOpenBlockedCount: 0,
						warningCode: 'liepin_browser_login_required',
						warningMessage: 'Liepin login is not connected yet.',
						connectionStatus: 'login_required',
						connectionWarningCode: 'login_required',
						connectionWarningMessage: 'connection not connected'
					}
				]
			}),
			events: []
		});

		expect(
			story.graphNodes.find((node) => node.id === 'liepin-source-start')?.detailPayload
		).toMatchObject({
			kind: 'sourceQueue',
			warningCode: 'liepin_browser_login_required',
			warningMessage: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。'
		});
	});
	});

	function triage(overrides: Partial<WorkbenchRequirementTriage> = {}): WorkbenchRequirementTriage {
	return {
		sessionId: 'session-1',
		status: 'approved',
		mustHaves: ['Flink CDC'],
		niceToHaves: ['data platform'],
		synonyms: [],
		seniorityFilters: [],
		exclusions: [],
		generatedQueryHints: ['streaming data'],
		createdAt: '2026-05-09T00:00:00Z',
		updatedAt: '2026-05-09T00:00:00Z',
		approvedAt: '2026-05-09T00:00:00Z',
		...overrides
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
				warningMessage: null
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
				warningMessage: null
			}
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
				warningMessage: null
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
				connectionStatus: 'connected'
			}
		],
		runtimeSourceState: null,
		...overrides
	};
}

function event(overrides: Partial<WorkbenchEvent>): WorkbenchEvent {
	const globalSeq = overrides.globalSeq ?? 1;
	const timestamp = `2026-05-09T00:00:${String(globalSeq).padStart(2, '0')}Z`;
	return {
		globalSeq,
		sessionSeq: overrides.sessionSeq ?? globalSeq,
		sessionId: overrides.sessionId ?? 'session-1',
		sourceRunId: overrides.sourceRunId === undefined ? 'src-cts' : overrides.sourceRunId,
		sourceKind: overrides.sourceKind === undefined ? 'cts' : overrides.sourceKind,
		eventName: overrides.eventName ?? 'source_run_started',
		schemaVersion: overrides.schemaVersion ?? '1.0',
		idempotencyKey: overrides.idempotencyKey ?? null,
		payload: overrides.payload ?? {},
		occurredAt: overrides.occurredAt ?? timestamp,
		createdAt: overrides.createdAt ?? timestamp
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
			...(overrides.payload ?? {})
		}
	});
}

function candidateReviewItem(
	overrides: Partial<WorkbenchCandidateReviewItem> = {}
): WorkbenchCandidateReviewItem {
	return {
		reviewItemId: 'review-liepin-1',
		sessionId: 'session-1',
		graphCandidateId: 'graph-candidate-1',
		canExpandResume: true,
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
				createdAt: '2026-05-09T00:00:06Z'
			}
		],
		createdAt: '2026-05-09T00:00:06Z',
		updatedAt: '2026-05-09T00:00:06Z',
		...overrides
	};
}

function detailOpenRequest(
	overrides: Partial<WorkbenchDetailOpenRequest> = {}
): WorkbenchDetailOpenRequest {
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
			missingRisks: []
		},
		blockedReason: null,
		ledger: {
			ledgerId: 'ledger-1',
			status: 'leased',
			budgetDay: '2026-05-09',
			leaseExpiresAt: null
		},
		providerAction: null,
		createdAt: '2026-05-09T00:00:07Z',
		updatedAt: '2026-05-09T00:00:07Z',
		...overrides
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
				search_terms: ['streaming data']
			}
		}
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
				reflection_rationale: '强 Flink 候选人可能不写 Kafka。',
				next_direction: '增加 CDC 和 realtime ETL 关键词。'
			}
		}
	}),
	event({
		globalSeq: 3,
		sourceKind: 'cts',
		sourceRunId: 'src-cts',
		eventName: 'candidate_review_item_upserted',
		payload: { reviewItemId: 'review-cts-1', score: 80, sourceKind: 'cts' }
	}),
	event({
		globalSeq: 4,
		sourceKind: 'liepin',
		sourceRunId: 'src-liepin',
		eventName: 'source_run_started',
		payload: { sourceRunId: 'src-liepin', sourceKind: 'liepin' }
	}),
	event({
		globalSeq: 5,
		sourceKind: 'liepin',
		sourceRunId: 'src-liepin',
		eventName: 'liepin_card_search_completed',
		payload: { cardsScannedCount: 30, uniqueCandidatesCount: 5 }
	}),
	event({
		globalSeq: 6,
		sourceKind: 'liepin',
		sourceRunId: 'src-liepin',
		eventName: 'candidate_review_item_upserted',
		payload: { reviewItemId: 'review-liepin-1', autoDetailScore: 91, sourceKind: 'liepin' }
	}),
	event({
		globalSeq: 7,
		sourceKind: 'liepin',
		sourceRunId: 'src-liepin',
		eventName: 'liepin_detail_open_auto_recommended',
		payload: { reviewItemId: 'review-liepin-1' }
	})
];
