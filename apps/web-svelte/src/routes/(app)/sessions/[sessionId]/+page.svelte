<script lang="ts">
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import {
		approveRequirementReview,
		getGraphCandidateResumeSnapshot,
		getRuntimeGraph,
		getSession,
		listDetailOpenRequests,
		listCandidateReviewItems,
		listFinalTopCandidates,
		listGraphCandidates,
		listSessionEvents,
		prepareRequirementReview,
		startSessionSourceRuns,
		updateRequirementReview
	} from '$lib/api/workbench';
	import ActivityLog from '$lib/components/ActivityLog.svelte';
	import CandidateReviewQueue from '$lib/components/CandidateReviewQueue.svelte';
	import CriteriaHighlights from '$lib/components/CriteriaHighlights.svelte';
	import DetailOpenRequestQueue from '$lib/components/DetailOpenRequestQueue.svelte';
	import ErrorState from '$lib/components/ErrorState.svelte';
	import JobBrief from '$lib/components/JobBrief.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NodeDetailPanel from '$lib/components/NodeDetailPanel.svelte';
	import RequirementReviewPanel from '$lib/components/RequirementReviewPanel.svelte';
	import RightWorkbenchTabs from '$lib/components/RightWorkbenchTabs.svelte';
	import SourceCard from '$lib/components/SourceCard.svelte';
	import StrategyCanvas from '$lib/components/StrategyCanvas.svelte';
	import { workbenchKeys } from '$lib/query/keys';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import { runtimeGraphToStory } from '$lib/workbench/runtimeGraphView';
	import { hasStartableSourceRun } from '$lib/workbench/sessionFlow';
	import type {
		RequirementSheet,
		WorkbenchGraphCandidateSummary,
		WorkbenchSession
	} from '$lib/workbench/types';

	let { data } = $props<{ data: { sessionId: string } }>();

	let selectedNode = $state<RecruiterGraphNode | null>(null);
	let selectedGraphCandidate = $state<WorkbenchGraphCandidateSummary | null>(null);
	let rightDetailTab = $state<'notes' | 'node'>('notes');
	let briefCollapsed = $state(false);
	let startError = $state<string | null>(null);
	let requirementReviewEditing = $state(false);
	const queryClient = useQueryClient();

	const sessionQuery = createQuery(() => ({
		queryKey: workbenchKeys.session(data.sessionId),
		queryFn: () => getSession(data.sessionId)
	}));

	const candidatesQuery = createQuery(() => ({
		queryKey: workbenchKeys.candidates(data.sessionId),
		queryFn: () => listCandidateReviewItems(data.sessionId),
		enabled: Boolean(sessionQuery.data)
	}));

	const finalTopQuery = createQuery(() => ({
		queryKey: workbenchKeys.finalTop10(data.sessionId),
		queryFn: () => listFinalTopCandidates(data.sessionId),
		enabled: Boolean(sessionQuery.data)
	}));

	const detailOpenRequestsQuery = createQuery(() => ({
		queryKey: workbenchKeys.detailOpenRequests(data.sessionId),
		queryFn: () => listDetailOpenRequests(data.sessionId),
		enabled: Boolean(sessionQuery.data)
	}));

	const eventsQuery = createQuery(() => ({
		queryKey: workbenchKeys.sessionEvents(data.sessionId, 0),
		queryFn: () => listSessionEvents(data.sessionId, 0),
		enabled: Boolean(sessionQuery.data)
	}));

	const runtimeGraphQuery = createQuery(() => ({
		queryKey: workbenchKeys.runtimeGraph(data.sessionId),
		queryFn: () => getRuntimeGraph(data.sessionId),
		enabled: Boolean(sessionQuery.data)
	}));

	const graphCandidatesQuery = createQuery(() => ({
		queryKey: workbenchKeys.graphCandidates(data.sessionId, selectedNode?.id ?? ''),
		queryFn: () => listGraphCandidates(data.sessionId, selectedNode?.id ?? ''),
		enabled: Boolean(sessionQuery.data && selectedNode)
	}));

	const resumeSnapshotQuery = createQuery(() => ({
		queryKey: workbenchKeys.resumeSnapshot(
			data.sessionId,
			selectedGraphCandidate?.graphCandidateId ?? ''
		),
		queryFn: () =>
			getGraphCandidateResumeSnapshot(
				data.sessionId,
				selectedGraphCandidate?.graphCandidateId ?? ''
			),
		enabled: Boolean(
			selectedGraphCandidate?.canExpandResume && selectedGraphCandidate.graphCandidateId
		)
	}));

	const story = $derived(
		runtimeGraphQuery.data
			? runtimeGraphToStory(runtimeGraphQuery.data, eventsQuery.data?.events ?? [])
			: null
	);
	const requirementSheet = $derived(
		sessionQuery.data?.requirement_review.requirement_sheet ?? null
	);
	const requirementHasSheet = $derived(Boolean(requirementSheet));
	const requirementApproved = $derived(sessionQuery.data?.requirement_review.status === 'approved');
	const requirementPreparationRunning = $derived(
		sessionQuery.data
			? isRequirementPreparationRunning(sessionQuery.data, eventsQuery.data?.events ?? [])
			: false
	);
	const sourceRunsRunning = $derived(
		sessionQuery.data?.sourceCards.some((card) => card.status === 'running') ?? false
	);
	const sourceRunsStartable = $derived(hasStartableSourceRun(sessionQuery.data?.sourceCards ?? []));
	const canPrepareRequirements = $derived(!requirementHasSheet && !requirementPreparationRunning);
	const canApproveRequirement = $derived(
		requirementHasSheet && !requirementApproved && !requirementReviewEditing
	);
	const canStartSession = $derived(
		Boolean(sessionQuery.data) &&
			requirementApproved &&
			requirementHasSheet &&
			sourceRunsStartable &&
			!sourceRunsRunning &&
			!requirementReviewEditing
	);
	const startLabel = $derived(
		!requirementHasSheet ? '启动 Agent' : requirementApproved ? '启动检索' : '确认并开始检索'
	);
	const startDescription = $derived(
		!requirementHasSheet
			? '系统会先拆解 JD，生成可确认的检索标准。'
			: requirementApproved
				? '启动本 session 已选择的检索源，后台事件会生成策略流程。'
				: '确认系统提取的标准后，启动本会话已选择的检索源。'
	);

	const refreshSession = async () => {
		await Promise.all([
			queryClient.invalidateQueries({ queryKey: workbenchKeys.session(data.sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.candidates(data.sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.finalTop10(data.sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.runtimeGraph(data.sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.sessionEvents(data.sessionId, 0) })
		]);
	};

	const prepareMutation = createMutation(() => ({
		mutationFn: () => prepareRequirementReview(data.sessionId),
		onSuccess: refreshSession
	}));

	const saveRequirementMutation = createMutation(() => ({
		mutationFn: (sheet: RequirementSheet) =>
			updateRequirementReview(data.sessionId, { requirement_sheet: sheet }),
		onSuccess: refreshSession
	}));

	const approveRequirementMutation = createMutation(() => ({
		mutationFn: () => approveRequirementReview(data.sessionId),
		onSuccess: refreshSession
	}));

	const startMutation = createMutation(() => ({
		mutationFn: async () => {
			const currentStory = story;
			if (!sessionQuery.data || !currentStory) {
				throw new Error('Session is not loaded.');
			}
			if (!requirementHasSheet) {
				return prepareRequirementReview(data.sessionId);
			}
			if (requirementReviewEditing) {
				throw new Error('请先保存或取消需求标准修改。');
			}
			if (sessionQuery.data.requirement_review.status !== 'approved') {
				await approveRequirementReview(data.sessionId);
			}
			return startSessionSourceRuns(data.sessionId);
		},
		onMutate: () => {
			startError = null;
		},
		onError: (error) => {
			startError = safeErrorMessage(error, '检索启动失败');
		},
		onSuccess: refreshSession
	}));

	const actionError = $derived(
		prepareMutation.error
			? safeErrorMessage(prepareMutation.error, '标准生成失败')
			: saveRequirementMutation.error
				? safeErrorMessage(saveRequirementMutation.error, '标准保存失败')
				: approveRequirementMutation.error
					? safeErrorMessage(approveRequirementMutation.error, '标准确认失败')
					: startMutation.error
						? safeErrorMessage(startMutation.error, '检索启动失败')
						: null
	);
	const primaryActionPending = $derived(
		prepareMutation.isPending || approveRequirementMutation.isPending || startMutation.isPending
	);
	const pendingRunningNote = $derived(
		primaryActionPending
			? !requirementHasSheet
				? '正在拆解岗位需求，准备生成可确认的检索标准。'
				: requirementApproved
					? '检索已启动，正在根据已确认标准推进所选渠道。'
					: '正在确认检索标准，并准备启动所选渠道。'
			: null
	);
	const primaryActionEnabled = $derived(
		!requirementHasSheet
			? canPrepareRequirements
			: requirementApproved
				? canStartSession
				: canApproveRequirement
	);

	function selectNode(node: RecruiterGraphNode) {
		selectedNode = node;
		selectedGraphCandidate = null;
		rightDetailTab = 'node';
	}

	function runPrimaryAction() {
		if (!requirementHasSheet) {
			prepareMutation.mutate();
			return;
		}
		startMutation.mutate();
	}

	function isRequirementPreparationRunning(
		session: WorkbenchSession,
		events: {
			eventName: string;
			globalSeq: number;
			sourceKind?: string | null;
			sourceRunId?: string | null;
		}[]
	) {
		const status = String(session.requirement_review.status);
		if (status === 'pending' || status === 'running') {
			return true;
		}
		const startedAt = maxEventSeq(
			events,
			(event) =>
				event.sourceKind === null &&
				event.sourceRunId === null &&
				(event.eventName === 'runtime_run_started' ||
					event.eventName === 'runtime_requirements_started')
		);
		const finishedAt = maxEventSeq(
			events,
			(event) =>
				event.sourceKind === null &&
				event.sourceRunId === null &&
				(event.eventName === 'runtime_requirements_completed' ||
					event.eventName === 'runtime_requirements_failed' ||
					event.eventName === 'requirement_review_updated')
		);
		return startedAt > finishedAt;
	}

	function maxEventSeq<T extends { globalSeq: number }>(
		events: T[],
		predicate: (event: T) => boolean
	) {
		return events.reduce(
			(maxSeq, event) => (predicate(event) ? Math.max(maxSeq, event.globalSeq) : maxSeq),
			0
		);
	}
</script>

{#if sessionQuery.isPending}
	<div class="screen-state">
		<LoadingState label="正在加载会话" />
	</div>
{:else if sessionQuery.error}
	<div class="screen-state">
		<ErrorState message={safeErrorMessage(sessionQuery.error, '会话加载失败')} />
	</div>
{:else if story && sessionQuery.data}
	<div class:brief-collapsed={briefCollapsed} class="reference-grid">
		{#if briefCollapsed}
			<section class="jd-panel jd-panel-collapsed" aria-label="岗位简报已收起">
				<button
					class="minimal-icon-button"
					type="button"
					aria-label="展开岗位简报列"
					onclick={() => {
						briefCollapsed = false;
					}}
				>
					›
				</button>
			</section>
		{:else}
			<section class="jd-panel">
				<JobBrief
					session={sessionQuery.data}
					onCollapseColumn={() => {
						briefCollapsed = true;
					}}
				/>
				<CriteriaHighlights
					{requirementSheet}
					mode={requirementApproved ? 'confirmed' : requirementHasSheet ? 'runtime' : 'empty'}
				/>
				<p class="section-label source-section-label">检索渠道</p>
				<div class="source-card-list">
					{#each sessionQuery.data.sourceCards as card (card.sourceRunId)}
						<SourceCard {card} session={sessionQuery.data} {requirementApproved} />
					{/each}
				</div>
				<RequirementReviewPanel
					review={sessionQuery.data.requirement_review}
					saving={saveRequirementMutation.isPending}
					approving={approveRequirementMutation.isPending}
					error={actionError}
					onSave={(sheet) => saveRequirementMutation.mutate(sheet)}
					onApprove={() => approveRequirementMutation.mutate()}
					onEditingChange={(editing) => {
						requirementReviewEditing = editing;
					}}
				/>
			</section>
		{/if}

		<section class="strategy-panel">
			<StrategyCanvas
				loading={runtimeGraphQuery.isPending}
				error={Boolean(runtimeGraphQuery.error)}
				sourceKinds={sessionQuery.data.sourceCards.map((card) => card.sourceKind)}
				canStart={primaryActionEnabled}
				starting={primaryActionPending}
				{startLabel}
				{startDescription}
				{startError}
				{story}
				selectedNodeId={selectedNode?.id ?? null}
				onStart={runPrimaryAction}
				onSelectNode={selectNode}
			/>
		</section>

		<section class="right-rail">
			{#snippet notesPanel()}
				<ActivityLog
					loading={eventsQuery.isPending}
					error={Boolean(eventsQuery.error)}
					pendingNote={pendingRunningNote}
					{story}
				/>
				<CandidateReviewQueue
					sessionId={data.sessionId}
					finalTop={finalTopQuery.data ?? null}
					reviewItems={candidatesQuery.data?.items ?? []}
					loading={finalTopQuery.isPending || candidatesQuery.isPending}
					error={finalTopQuery.error || candidatesQuery.error
						? safeErrorMessage(finalTopQuery.error ?? candidatesQuery.error, '候选人队列加载失败')
						: null}
				/>
				<DetailOpenRequestQueue
					sessionId={data.sessionId}
					requests={detailOpenRequestsQuery.data?.requests ?? []}
					loading={detailOpenRequestsQuery.isPending}
					error={detailOpenRequestsQuery.error
						? safeErrorMessage(detailOpenRequestsQuery.error, '详情审批加载失败')
						: null}
				/>
			{/snippet}
			{#snippet nodePanel()}
				<NodeDetailPanel
					sessionId={data.sessionId}
					node={selectedNode}
					graphCandidatePage={graphCandidatesQuery.data ?? null}
					graphCandidatesLoading={graphCandidatesQuery.isPending && Boolean(selectedNode)}
					graphCandidatesError={graphCandidatesQuery.error
						? safeErrorMessage(graphCandidatesQuery.error, '候选人加载失败')
						: null}
					selectedGraphCandidateId={selectedGraphCandidate?.graphCandidateId ?? null}
					resumeSnapshot={resumeSnapshotQuery.data ?? null}
					resumeSnapshotLoading={resumeSnapshotQuery.isPending && Boolean(selectedGraphCandidate)}
					resumeSnapshotError={resumeSnapshotQuery.error
						? safeErrorMessage(resumeSnapshotQuery.error, '简历摘要加载失败')
						: null}
					onSelectGraphCandidate={(candidate) => {
						selectedGraphCandidate = candidate;
					}}
				/>
			{/snippet}
			<RightWorkbenchTabs
				activeTab={rightDetailTab}
				onActiveTabChange={(tab) => {
					rightDetailTab = tab;
				}}
				{notesPanel}
				{nodePanel}
			/>
		</section>
	</div>
{:else}
	<div class="screen-state">
		<ErrorState message="会话暂无可展示内容" />
	</div>
{/if}
