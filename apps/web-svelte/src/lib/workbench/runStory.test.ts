import { describe, expect, it } from 'vitest';

import { buildRunStory, displayTriageFromStory } from './runStory';

type BuildRunStoryInput = Parameters<typeof buildRunStory>[0];
type WorkbenchSession = BuildRunStoryInput['session'];
type WorkbenchEvent = BuildRunStoryInput['events'][number];
type WorkbenchCandidateReviewItem = NonNullable<BuildRunStoryInput['candidateReviewItems']>[number];
type WorkbenchDetailOpenRequest = NonNullable<BuildRunStoryInput['detailOpenRequests']>[number];
type WorkbenchFinalTopCandidate = NonNullable<BuildRunStoryInput['finalTopCandidates']>[number];
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

	it('does not draw the sourcing pipeline before the agent starts', () => {
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
				}),
				sourceRuns: [
					{
						sourceRunId: 'src-cts',
						sourceKind: 'cts',
						status: 'queued',
						authState: 'not_required',
						cardsScannedCount: 0,
						uniqueCandidatesCount: 0,
						detailOpenUsedCount: 0,
						detailOpenBlockedCount: 0,
						warningCode: null,
						warningMessage: null
					},
					{
						sourceRunId: 'src-liepin',
						sourceKind: 'liepin',
						status: 'queued',
						authState: 'not_required',
						cardsScannedCount: 0,
						uniqueCandidatesCount: 0,
						detailOpenUsedCount: 0,
						detailOpenBlockedCount: 0,
						warningCode: null,
						warningMessage: null
					}
				],
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'queued', authState: 'not_required' }),
					sourceCard({ sourceKind: 'liepin', status: 'queued', authState: 'not_required' })
				],
				runtimeSourceState: {
					selectedSourceKinds: ['cts', 'liepin'],
					coverageStatus: 'pending',
					finalizationRevision: null,
					finalizationReasonCode: null,
					identityMergeCount: 0,
					ambiguousDuplicateCount: 0,
					canonicalResumeSelectedCount: 0,
					sources: [
						{
							sourceKind: 'cts',
							status: 'pending',
							eventType: null,
							eventSeq: null,
							cardsSeenCount: 0,
							cardsFilteredCount: 0,
							candidatesCount: 0,
							detailRecommendationsCount: 0,
							detailState: null
						},
						{
							sourceKind: 'liepin',
							status: 'pending',
							eventType: null,
							eventSeq: null,
							cardsSeenCount: 0,
							cardsFilteredCount: 0,
							candidatesCount: 0,
							detailRecommendationsCount: 0,
							detailState: null
						}
					]
				}
			}),
			events: [event({ eventName: 'session_created', sourceKind: null, sourceRunId: null })],
			finalTopCandidates: [],
			finalTopStatus: 'loading'
		});

		expect(story.graphNodes).toHaveLength(0);
		expect(story.completionText).toBeNull();
		expect(story.logEntries[0]?.text).toBe('已创建岗位会话，等待启动 Agent 拆解检索标准。');
	});

	it('does not draw merge or final nodes while sourcing is still running', () => {
		const story = buildRunStory({
			session: session({
				sourceRuns: [
					{
						sourceRunId: 'src-cts',
						sourceKind: 'cts',
						status: 'running',
						authState: 'not_required',
						cardsScannedCount: 0,
						uniqueCandidatesCount: 0,
						detailOpenUsedCount: 0,
						detailOpenBlockedCount: 0,
						warningCode: null,
						warningMessage: null
					},
					{
						sourceRunId: 'src-liepin',
						sourceKind: 'liepin',
						status: 'blocked',
						authState: 'login_required',
						cardsScannedCount: 0,
						uniqueCandidatesCount: 0,
						detailOpenUsedCount: 0,
						detailOpenBlockedCount: 0,
						warningCode: 'source_browser_extension_disconnected',
						warningMessage: '浏览器检索通道暂不可用'
					}
				],
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'running', authState: 'not_required' }),
					sourceCard({
						sourceKind: 'liepin',
						status: 'blocked',
						authState: 'login_required',
						warningCode: 'source_browser_extension_disconnected',
						warningMessage: '浏览器检索通道暂不可用'
					})
				],
				runtimeSourceState: {
					selectedSourceKinds: ['cts', 'liepin'],
					coverageStatus: 'pending',
					finalizationRevision: null,
					finalizationReasonCode: null,
					identityMergeCount: 0,
					ambiguousDuplicateCount: 0,
					canonicalResumeSelectedCount: 0,
					sources: [
						{
							sourceKind: 'cts',
							status: 'running',
							eventType: 'source_lane_started',
							eventSeq: 11,
							cardsSeenCount: 0,
							cardsFilteredCount: 0,
							candidatesCount: 0,
							detailRecommendationsCount: 0,
							detailState: null
						},
						{
							sourceKind: 'liepin',
							status: 'blocked',
							reasonCode: 'source_browser_extension_disconnected',
							eventType: 'source_lane_blocked',
							eventSeq: 12,
							cardsSeenCount: 0,
							cardsFilteredCount: 0,
							candidatesCount: 0,
							detailRecommendationsCount: 0,
							detailState: null
						}
					]
				}
			}),
			events: [
				event({ globalSeq: 11, eventName: 'source_run_started', sourceKind: 'cts' }),
				event({
					globalSeq: 12,
					eventName: 'source_run_blocked',
					sourceKind: 'liepin',
					sourceRunId: 'src-liepin'
				})
			],
			finalTopCandidates: [],
			finalTopStatus: 'loading'
		});

		expect(story.graphNodes.map((node) => node.id)).toEqual(
			expect.arrayContaining(['source-plan', 'cts-source-start', 'liepin-source-start'])
		);
		expect(story.graphNodes.some((node) => node.id === 'merge-dedupe')).toBe(false);
		expect(story.graphNodes.some((node) => node.id === 'final-shortlist')).toBe(false);
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
			requestSummaries: ['Ada Chen · 已批准 · 已预留'],
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

	it('projects runtime source plan, source branches, merge-dedupe, and final top10 from runtime-owned state', () => {
		const story = buildRunStory({
			session: session({
				sourceRuns: [],
				sourceCards: [],
				runtimeSourceState: runtimeSourceState()
			}),
			events: [],
			candidateReviewItems: [],
			finalTopCandidates: [
				finalTopCandidate({
					reviewItemId: 'review-final-1',
					runtimeIdentityId: 'identity-1',
					canonicalReviewItemId: 'review-final-1',
					mergedReviewItemIds: ['review-cts-1', 'review-liepin-1'],
					rank: 1,
					sourceBadges: ['CTS final', 'Liepin card', 'Multiple sources'],
					aggregateScore: 94
				})
			],
			finalTopStatus: 'success'
		});

		expect(story.graphNodes.map((node) => node.id)).toEqual(
			expect.arrayContaining([
				'source-plan',
				'cts-source-start',
				'liepin-source-start',
				'merge-dedupe',
				'final-shortlist'
			])
		);
		expect(story.graphEdges).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ from: 'requirements', to: 'source-plan', label: '选择来源' }),
				expect.objectContaining({ from: 'source-plan', to: 'cts-source-start' }),
				expect.objectContaining({ from: 'source-plan', to: 'liepin-source-start' }),
				expect.objectContaining({ to: 'merge-dedupe', label: '身份合并' }),
				expect.objectContaining({ from: 'merge-dedupe', to: 'final-shortlist', label: 'Top 10' })
			])
		);
		expect(story.graphNodes.find((node) => node.id === 'merge-dedupe')).toMatchObject({
			label: '跨源合并 · 2 组',
			detail: '规范简历 10 · 模糊重复 1',
			tone: 'violet'
		});
		expect(story.graphNodes.find((node) => node.id === 'final-shortlist')).toMatchObject({
			label: '最终短名单 · 1 人',
			detail: '最高 94 分',
			tone: 'green',
			candidateReviewItemIds: ['review-final-1']
		});
		expect(
			story.graphNodes.find((node) => node.id === 'final-shortlist')?.detailPayload
		).toMatchObject({
			kind: 'aggregation',
			candidateCount: 1,
			bestScore: 94,
			finalTopStatus: 'success',
			finalTopCandidateIds: ['review-final-1'],
			identityMergeCount: 2
		});
	});

	it('uses final top candidate count for the final node instead of raw review item count', () => {
		const story = buildRunStory({
			session: session({
				runtimeSourceState: runtimeSourceState({ canonicalResumeSelectedCount: 1 })
			}),
			events: [],
			candidateReviewItems: [
				candidateReviewItem({ reviewItemId: 'raw-review-1', aggregateScore: 80 }),
				candidateReviewItem({ reviewItemId: 'raw-review-2', aggregateScore: 79 }),
				candidateReviewItem({ reviewItemId: 'raw-review-3', aggregateScore: 78 })
			],
			finalTopCandidates: [finalTopCandidate({ reviewItemId: 'review-final-1' })],
			finalTopStatus: 'success'
		});

		expect(story.graphNodes.find((node) => node.id === 'final-shortlist')).toMatchObject({
			label: '最终短名单 · 1 人',
			candidateReviewItemIds: ['review-final-1']
		});
		expect(
			story.graphNodes.find((node) => node.id === 'final-shortlist')?.detailPayload
		).toMatchObject({
			candidateCount: 1,
			finalTopCandidateIds: ['review-final-1']
		});
	});

	it('does not show zero final candidates while final top is loading', () => {
		const story = buildRunStory({
			session: session({
				runtimeSourceState: runtimeSourceState({ coverageStatus: 'pending' })
			}),
			events: [],
			candidateReviewItems: [],
			finalTopCandidates: [],
			finalTopStatus: 'loading'
		});

		expect(story.graphNodes.find((node) => node.id === 'final-shortlist')).toMatchObject({
			label: '最终短名单',
			detail: 'Top 10 生成中',
			tone: 'amber'
		});
		expect(story.graphNodes.find((node) => node.id === 'final-shortlist')?.label).not.toContain(
			'0 人'
		);
	});

	it('marks final top as unavailable when the final top query errors', () => {
		const story = buildRunStory({
			session: session({
				runtimeSourceState: runtimeSourceState({ coverageStatus: 'degraded' })
			}),
			events: [],
			candidateReviewItems: [],
			finalTopCandidates: [],
			finalTopStatus: 'error'
		});

		expect(story.graphNodes.find((node) => node.id === 'final-shortlist')).toMatchObject({
			label: '最终短名单',
			detail: 'Top 10 暂不可用',
			tone: 'amber'
		});
		expect(
			story.graphNodes.find((node) => node.id === 'final-shortlist')?.detailPayload
		).toMatchObject({
			finalTopStatus: 'error'
		});
	});

	it('uses safe Liepin browser reason copy in source queue details', () => {
		const story = buildRunStory({
			session: session({
				runtimeSourceState: {
					selectedSourceKinds: ['cts', 'liepin'],
					coverageStatus: 'degraded',
					finalizationRevision: 1,
					finalizationReasonCode: 'source_lanes_degraded',
					identityMergeCount: 0,
					ambiguousDuplicateCount: 0,
					canonicalResumeSelectedCount: 0,
					sources: [
						{
							sourceKind: 'liepin',
							status: 'blocked',
							eventType: 'source_lane_blocked',
							eventSeq: 2,
							reasonCode: 'liepin_browser_probe_unavailable',
							cardsSeenCount: 0,
							cardsFilteredCount: 0,
							candidatesCount: 0,
							detailRecommendationsCount: 0,
							detailState: null
						}
					]
				},
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
			warningCode: 'liepin_browser_probe_unavailable',
			warningMessage: '浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。'
		});
		expect(story.graphNodes.find((node) => node.id === 'liepin-source-start')?.detail).toBe(
			'浏览器通道不可用'
		);
	});

	it('projects visible triage criteria without response-only fields', () => {
		const visible = displayTriageFromStory(
			triage({
				status: 'draft',
				mustHaves: ['saved must'],
				niceToHaves: [],
				synonyms: [],
				seniorityFilters: [],
				exclusions: [],
				generatedQueryHints: []
			}),
			{
				mustHaves: ['runtime must'],
				niceToHaves: ['runtime nice'],
				synonyms: ['runtime synonym'],
				seniorityFilters: [],
				exclusions: [],
				generatedQueryHints: ['runtime query']
			}
		);

		expect(visible).toEqual({
			mustHaves: ['saved must'],
			niceToHaves: ['runtime nice'],
			synonyms: ['runtime synonym'],
			seniorityFilters: [],
			exclusions: [],
			generatedQueryHints: ['runtime query']
		});
		expect(visible).not.toHaveProperty('sessionId');
		expect(visible).not.toHaveProperty('status');
		expect(visible).not.toHaveProperty('createdAt');
	});

	it('renders dual-source runtime rounds as fan-out fan-in modules', () => {
		const story = buildRunStory({
			session: session({
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'completed' }),
					sourceCard({ sourceKind: 'liepin', status: 'completed' })
				]
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', {
					roundReturned: 14
				}),
				runtimeRoundEvent('runtime_round_source_result', 1, 'liepin', 'completed', {
					roundReturned: 8
				}),
				runtimeRoundEvent('runtime_round_merge_completed', 1, null, 'completed', {
					mergedIdentities: 18
				}),
				runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', {
					topPoolCount: 10
				}),
				runtimeRoundEvent('runtime_round_feedback_completed', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_query_ready', 2, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 2, 'cts', 'completed', {
					roundReturned: 9
				}),
				runtimeRoundEvent('runtime_round_source_result', 2, 'liepin', 'completed', {
					roundReturned: 5
				}),
				runtimeRoundEvent('runtime_round_merge_completed', 2, null, 'completed', {
					mergedIdentities: 11
				}),
				runtimeRoundEvent('runtime_round_scoring_completed', 2, null, 'completed', {
					topPoolCount: 10
				})
			],
			finalTopCandidates: Array.from({ length: 10 }, (_, index) =>
				finalTopCandidate({ rank: index + 1, reviewItemId: `review-final-${index + 1}` })
			)
		});

		expect(story.graphNodes.map((node) => node.id)).toEqual(
			expect.arrayContaining([
				'round-1-query',
				'round-1-source-cts',
				'round-1-source-liepin',
				'round-1-merge',
				'round-1-score',
				'round-1-feedback',
				'round-2-query',
				'round-2-source-cts',
				'round-2-source-liepin',
				'round-2-merge',
				'round-2-score',
				'final-shortlist'
			])
		);
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-merge' })
		);
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'round-1-source-liepin', to: 'round-1-merge' })
		);
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'round-1-feedback', to: 'round-2-query' })
		);
	});

	it('renders a selected single-source run without an unselected source or fake cross-source merge', () => {
		const ctsSourceState = runtimeSourceState().sources[0]!;
		const story = buildRunStory({
			session: session({
				sourceCards: [sourceCard({ sourceKind: 'cts', status: 'completed' })],
				runtimeSourceState: runtimeSourceState({
					selectedSourceKinds: ['cts'],
					identityMergeCount: 0,
					ambiguousDuplicateCount: 0,
					sources: [ctsSourceState]
				})
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', {
					roundReturned: 12
				}),
				runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', {
					topPoolCount: 10
				})
			],
			finalTopCandidates: [finalTopCandidate({ reviewItemId: 'review-final-1' })],
			finalTopStatus: 'success'
		});

		expect(story.graphNodes.some((node) => node.id === 'round-1-source-cts')).toBe(true);
		expect(story.graphNodes.some((node) => node.id === 'round-1-source-liepin')).toBe(false);
		expect(story.graphNodes.some((node) => node.id === 'round-1-merge')).toBe(false);
		expect(story.graphNodes.some((node) => node.id === 'merge-dedupe')).toBe(false);
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-score' })
		);
	});

	it('keeps selected blocked Liepin visible while CTS continues into merge', () => {
		const story = buildRunStory({
			session: session({
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'running' }),
					sourceCard({ sourceKind: 'liepin', status: 'blocked' })
				]
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', {
					roundReturned: 11
				}),
				runtimeRoundEvent(
					'runtime_round_source_result',
					1,
					'liepin',
					'blocked',
					{ roundReturned: 0 },
					'source_login_required'
				),
				runtimeRoundEvent('runtime_round_merge_completed', 1, null, 'degraded', {
					mergedIdentities: 11
				})
			]
		});

		const liepinNode = story.graphNodes.find((node) => node.id === 'round-1-source-liepin');
		expect(liepinNode?.tone).toBe('amber');
		expect(liepinNode?.detail).toContain('登录');
		expect(story.graphEdges).toContainEqual(
			expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-merge' })
		);
	});

	it('adds business detail payloads to runtime source round nodes', () => {
		const story = buildRunStory({
			session: session({
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'running' }),
					sourceCard({ sourceKind: 'liepin', status: 'running' })
				]
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 3, null, 'completed', {
					topPoolCount: 10
				}),
				runtimeRoundEvent('runtime_round_source_result', 3, 'cts', 'completed', {
					roundReturned: 16,
					roundIdentities: 9,
					sourceCumulativeReturned: 28,
					sourceCumulativeIdentities: 28
				}),
				runtimeRoundEvent('runtime_round_source_result', 3, 'liepin', 'completed', {
					roundReturned: 29,
					roundIdentities: 29,
					sourceCumulativeReturned: 119,
					sourceCumulativeIdentities: 119
				})
			]
		});

		const ctsNode = story.graphNodes.find((node) => node.id === 'round-3-source-cts');
		const liepinNode = story.graphNodes.find((node) => node.id === 'round-3-source-liepin');
		expect(ctsNode?.detailKind).toBe('ctsRoundResults');
		expect(ctsNode?.detailPayload).toMatchObject({
			kind: 'ctsRoundResults',
			roundNo: 3,
			rawCandidateCount: 16,
			uniqueNewCount: 9,
			recallCounts: {
				sourceCumulativeReturned: 28,
				sourceCumulativeIdentities: 28,
				status: 'completed'
			}
		});
		expect(liepinNode?.detailKind).toBe('liepinCardSearch');
		expect(liepinNode?.detailPayload).toMatchObject({
			kind: 'liepinCardSearch',
			cardsScannedCount: 29,
			uniqueCandidatesCount: 29
		});
	});

	it('renders partial runtime source results as degraded source coverage', () => {
		const story = buildRunStory({
			session: session({
				sourceCards: [
					sourceCard({ sourceKind: 'cts', status: 'completed' }),
					sourceCard({ sourceKind: 'liepin', status: 'partial' })
				]
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', {
					roundReturned: 11
				}),
				runtimeRoundEvent(
					'runtime_round_source_result',
					1,
					'liepin',
					'partial',
					{ roundReturned: 3, roundIdentities: 2 },
					'source_partial'
				),
				runtimeRoundEvent('runtime_round_merge_completed', 1, null, 'degraded', {
					mergedIdentities: 13
				})
			]
		});

		const liepinNode = story.graphNodes.find((node) => node.id === 'round-1-source-liepin');
		expect(liepinNode?.tone).toBe('amber');
		expect(liepinNode?.detail).toContain('部分结果');
	});

	it('does not render runtime finalization as a round-zero module', () => {
		const story = buildRunStory({
			session: session({
				sourceCards: [sourceCard({ sourceKind: 'cts', status: 'completed' })]
			}),
			events: [
				runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
				runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', {
					roundReturned: 12
				}),
				runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', {
					topPoolCount: 10
				}),
				runtimeFinalizationEvent()
			],
			finalTopCandidates: Array.from({ length: 10 }, (_, index) =>
				finalTopCandidate({ rank: index + 1, reviewItemId: `review-final-${index + 1}` })
			)
		});

		expect(story.graphNodes.some((node) => node.id.startsWith('round-0-'))).toBe(false);
		expect(story.graphNodes.some((node) => node.id === 'final-shortlist')).toBe(true);
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

function sourceCard(
	overrides: Partial<WorkbenchSession['sourceCards'][number]> = {}
): WorkbenchSession['sourceCards'][number] {
	const sourceKind = overrides.sourceKind ?? 'cts';
	return {
		sourceRunId: sourceKind === 'cts' ? 'src-cts' : 'src-liepin',
		sourceKind,
		label: sourceKind === 'cts' ? 'CTS' : 'Liepin',
		status: 'completed',
		authState: sourceKind === 'cts' ? 'not_required' : 'login_required',
		cardsScannedCount: 0,
		uniqueCandidatesCount: 0,
		detailOpenUsedCount: 0,
		detailOpenBlockedCount: 0,
		warningCode: null,
		warningMessage: null,
		...(sourceKind === 'liepin' ? { connectionStatus: 'connected' } : {}),
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

function runtimeRoundEvent(
	eventName: string,
	roundNo: number,
	sourceKind: 'cts' | 'liepin' | null,
	status: 'pending' | 'running' | 'completed' | 'partial' | 'blocked' | 'degraded' | 'failed',
	counts: Record<string, number> = {},
	safeReasonCode: string | null = null
): WorkbenchEvent {
	const stageByEventName: Record<string, string> = {
		runtime_round_query_ready: 'round_query',
		runtime_round_source_dispatch: 'source_dispatch',
		runtime_round_source_result: 'source_result',
		runtime_round_merge_completed: 'merge',
		runtime_round_scoring_completed: 'scoring',
		runtime_round_feedback_completed: 'feedback'
	};
	const eventOffset = Object.keys(stageByEventName).indexOf(eventName);
	const globalSeq = roundNo * 10 + Math.max(eventOffset, 0);
	return event({
		globalSeq,
		eventName,
		sourceKind,
		sourceRunId: sourceKind ? `src-${sourceKind}` : null,
		payload: {
			schemaVersion: 'runtime_public_event_v1',
			runtimeRunId: 'run-story-1',
			eventId: `run-story-1:${eventName}:${roundNo}:${sourceKind ?? 'shared'}`,
			eventSeq: globalSeq,
			stage: stageByEventName[eventName],
			roundNo,
			sourceKind,
			sourcePlanId: sourceKind ? `run-story-1:source:${sourceKind}` : null,
			roundQueryBundleId: `run-story-1:round:${roundNo}:query_bundle`,
			status,
			counts,
			safeReasonCode,
			createdAt: '2026-05-22T00:00:00Z'
		}
	});
}

function runtimeFinalizationEvent(): WorkbenchEvent {
	return event({
		globalSeq: 99,
		eventName: 'runtime_finalization_completed',
		sourceKind: null,
		sourceRunId: null,
		payload: {
			schemaVersion: 'runtime_public_event_v1',
			runtimeRunId: 'run-story-1',
			eventId: 'run-story-1:final:finalization:shared:99',
			eventSeq: 99,
			stage: 'finalization',
			roundNo: null,
			sourceKind: null,
			sourcePlanId: null,
			roundQueryBundleId: null,
			status: 'completed',
			counts: { topPoolCount: 10 },
			safeReasonCode: null,
			createdAt: '2026-05-22T00:00:00Z'
		}
	});
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

function runtimeSourceState(
	overrides: Partial<NonNullable<WorkbenchSession['runtimeSourceState']>> = {}
): NonNullable<WorkbenchSession['runtimeSourceState']> {
	return {
		selectedSourceKinds: ['cts', 'liepin'],
		coverageStatus: 'complete',
		finalizationRevision: 2,
		finalizationReasonCode: 'completed',
		identityMergeCount: 2,
		ambiguousDuplicateCount: 1,
		canonicalResumeSelectedCount: 10,
		sources: [
			{
				sourceKind: 'cts',
				status: 'completed',
				eventType: 'source_lane_completed',
				eventSeq: 2,
				cardsSeenCount: 20,
				cardsFilteredCount: 2,
				candidatesCount: 10,
				detailRecommendationsCount: 0,
				detailState: null
			},
			{
				sourceKind: 'liepin',
				status: 'completed',
				eventType: 'source_lane_completed',
				eventSeq: 3,
				cardsSeenCount: 30,
				cardsFilteredCount: 5,
				candidatesCount: 8,
				detailRecommendationsCount: 3,
				detailState: 'completed'
			}
		],
		...overrides
	};
}

function finalTopCandidate(
	overrides: Partial<WorkbenchFinalTopCandidate> = {}
): WorkbenchFinalTopCandidate {
	return {
		reviewItemId: 'review-final-1',
		runtimeIdentityId: 'identity-1',
		canonicalReviewItemId: 'review-final-1',
		mergedReviewItemIds: ['review-final-1'],
		rank: 1,
		displayName: 'Ada Chen',
		title: 'Data Platform Engineer',
		company: 'Example Inc.',
		location: 'Shanghai',
		summary: 'Built Kafka and Flink data platforms.',
		aggregateScore: 93,
		fitBucket: 'fit',
		sourceBadges: ['CTS final'],
		evidenceLevel: 'final',
		sourceEvidence: [
			{
				evidenceId: 'evidence-final-1',
				sourceRunId: 'src-cts',
				sourceKind: 'cts',
				evidenceLevel: 'final',
				score: 93,
				fitBucket: 'fit'
			}
		],
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
