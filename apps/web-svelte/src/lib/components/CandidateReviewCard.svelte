<script lang="ts">
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import {
		createDetailOpenRequest,
		getGraphCandidateResumeSnapshot,
		openCandidateProviderAction,
		updateCandidateReviewItem
	} from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';
	import type { FinalCandidateViewModel } from '$lib/workbench/finalCandidateCards';
	import type { WorkbenchCandidateReviewItemUpdateInput } from '$lib/workbench/types';
	import ResumeSnapshotView from './ResumeSnapshotView.svelte';

	let { sessionId, card }: { sessionId: string; card: FinalCandidateViewModel } = $props();

	let note = $state('');
	let noteDirty = $state(false);
	let resumeExpanded = $state(false);
	let error = $state('');
	let providerMessage = $state('');
	const queryClient = useQueryClient();

	const snapshotQuery = createQuery(() => ({
		queryKey: workbenchKeys.resumeSnapshot(sessionId, card.resumeGraphCandidateId ?? ''),
		queryFn: () => getGraphCandidateResumeSnapshot(sessionId, card.resumeGraphCandidateId ?? ''),
		enabled: Boolean(resumeExpanded && card.canExpandResume && card.resumeGraphCandidateId)
	}));

	const updateMutation = createMutation(() => ({
		mutationFn: (input: WorkbenchCandidateReviewItemUpdateInput) => {
			if (!card.actionReviewItemId) {
				throw new Error('No safe review item is available for this action.');
			}
			return updateCandidateReviewItem(sessionId, card.actionReviewItemId, input);
		},
		onSuccess: async () => {
			error = '';
			noteDirty = false;
			await refreshCandidateState();
		},
		onError: (caught) => {
			error = safeErrorMessage(caught, '候选人更新失败');
		}
	}));

	const detailOpenMutation = createMutation(() => ({
		mutationFn: () => {
			if (!card.detailActionReviewItemId) {
				throw new Error('No Liepin card review item is available for detail request.');
			}
			return createDetailOpenRequest(sessionId, card.detailActionReviewItemId, {
				idempotencyKey: `detail:${card.detailActionReviewItemId}`
			});
		},
		onSuccess: async (request) => {
			error = '';
			providerMessage = detailOpenStatusMessage(request.status);
			await refreshCandidateState();
		},
		onError: (caught) => {
			error = safeErrorMessage(caught, '详情请求失败');
		}
	}));

	const providerActionMutation = createMutation(() => ({
		mutationFn: () => {
			if (!card.providerActionReviewItemId) {
				throw new Error('No safe Liepin review item is available for provider action.');
			}
			return openCandidateProviderAction(sessionId, card.providerActionReviewItemId);
		},
		onSuccess: (action) => {
			error = '';
			providerMessage = action.message;
		},
		onError: (caught) => {
			error = safeErrorMessage(caught, '安全打开动作不可用');
		}
	}));

	$effect(() => {
		if (!noteDirty) {
			note = card.note;
		}
	});

	function updateCandidate(input: WorkbenchCandidateReviewItemUpdateInput) {
		error = '';
		updateMutation.mutate({ note, ...input });
	}

	async function refreshCandidateState() {
		await Promise.all([
			queryClient.invalidateQueries({ queryKey: workbenchKeys.candidates(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.finalTop10(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.detailOpenRequests(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.session(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions })
		]);
	}

	function detailOpenStatusMessage(status: string) {
		if (status === 'pending') return '详情请求正在等待审批。';
		if (status === 'bypassed') return '详情额度已由旁路模式预留。';
		if (status === 'approved') return '详情额度已批准并预留。';
		return `详情请求状态：${status}`;
	}

	function sourceBadgeLabel(label: string) {
		const labels: Record<string, string> = {
			'CTS final': 'CTS 最终',
			'Liepin card': '猎聘卡片',
			'Liepin detail': '猎聘详情',
			'Multiple sources': '多源'
		};
		return labels[label] ?? label;
	}

	function evidenceLevelLabel(label: string) {
		const labels: Record<string, string> = {
			detail: '详情证据',
			card: '卡片证据',
			final: '最终证据',
			merged: '合并证据'
		};
		return labels[label] ?? label;
	}

	function candidateStatusLabel(status: string) {
		const labels: Record<string, string> = {
			promising: '已入围',
			rejected: '已淘汰',
			pending: '待评审'
		};
		return labels[status] ?? status;
	}
</script>

<article class="candidate-card" data-testid={`candidate-card-${card.runtimeIdentityId}`}>
	<div class="candidate-card-head">
		<div>
			<strong>{card.displayName || '候选人'}</strong>
			<span
				>{[card.title, card.company, card.location].filter(Boolean).join(' · ') ||
					'候选人摘要'}</span
			>
		</div>
		<div class="score-badge">{card.aggregateScore ?? '-'}</div>
	</div>

	<div class="badge-row">
		<span class="source-badge">第 {card.rank} 名</span>
		{#if card.sourceRound !== null}
			<span class="source-badge muted-badge">第 {card.sourceRound} 轮</span>
		{/if}
		{#each card.sourceBadges as badge (badge)}
			<span class="source-badge">{sourceBadgeLabel(badge)}</span>
		{/each}
		<span class="source-badge muted-badge">{evidenceLevelLabel(card.evidenceLevel)}</span>
		{#if card.status}
			<span class:approved={card.status === 'promising'} class="status-pill"
				>{candidateStatusLabel(card.status)}</span
			>
		{/if}
	</div>

	<p>{card.summary}</p>
	<p class="graph-candidate-summary">{card.coverageExplanation}</p>
	{#if card.mergeExplanation}
		<p class="candidate-action-message">{card.mergeExplanation}</p>
	{/if}
	{#if card.canonicalResumeHint}
		<p class="muted">{card.canonicalResumeHint}</p>
	{/if}
	{#if card.mergedStateHint}
		<p class="muted">{card.mergedStateHint}</p>
	{/if}
	<div class="candidate-facts">
		<span>来源合并</span>
		<p>{card.mergedReviewItemIds.length} 条记录合并为同一身份</p>
	</div>

	{#if card.whySelected}
		<div class="candidate-facts">
			<span>选择理由</span>
			<p>{card.whySelected}</p>
		</div>
	{/if}
	{#if card.matchedMustHaves.length > 0}
		<div class="candidate-facts">
			<span>硬性匹配</span>
			<p>{card.matchedMustHaves.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.matchedPreferences.length > 0}
		<div class="candidate-facts">
			<span>偏好匹配</span>
			<p>{card.matchedPreferences.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.strengths.length > 0}
		<div class="candidate-facts">
			<span>优势</span>
			<p>{card.strengths.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.weaknesses.length > 0}
		<div class="candidate-facts">
			<span>弱项</span>
			<p>{card.weaknesses.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.missingRisks.length > 0}
		<div class="candidate-facts">
			<span>风险</span>
			<p>{card.missingRisks.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}

	{#if card.resumeGraphCandidateId}
		<div class="candidate-actions">
			<button
				class="secondary-link"
				type="button"
				disabled={!card.canExpandResume}
				aria-expanded={resumeExpanded}
				onclick={() => {
					resumeExpanded = !resumeExpanded;
				}}
			>
				{resumeExpanded ? '收起安全简历摘要' : '查看安全简历摘要'}
			</button>
		</div>
		{#if resumeExpanded}
			<ResumeSnapshotView
				graphCandidateId={card.resumeGraphCandidateId}
				snapshot={snapshotQuery.data ?? null}
				loading={snapshotQuery.isPending}
				error={snapshotQuery.error
					? safeErrorMessage(snapshotQuery.error, '简历摘要加载失败')
					: null}
			/>
		{/if}
	{/if}

	<label class="field candidate-note">
		<span>备注</span>
		<textarea
			value={note}
			rows="3"
			oninput={(event) => {
				note = event.currentTarget.value;
				noteDirty = true;
			}}
		></textarea>
	</label>

	{#if error}
		<p class="form-error" role="alert">{error}</p>
	{/if}
	<div class="candidate-actions">
		<button
			class="primary-action"
			type="button"
			disabled={!card.actionReviewItemId || updateMutation.isPending}
			onclick={() => updateCandidate({ status: 'promising' })}
		>
			标记入围
		</button>
		<button
			class="secondary-link"
			type="button"
			disabled={!card.actionReviewItemId || updateMutation.isPending}
			onclick={() => updateCandidate({ status: 'rejected' })}
		>
			淘汰
		</button>
		<button
			class="secondary-link"
			type="button"
			disabled={!card.actionReviewItemId || updateMutation.isPending}
			onclick={() => updateCandidate({})}
		>
			保存备注
		</button>
		{#if card.canRequestLiepinDetail}
			<button
				class="secondary-link"
				type="button"
				disabled={detailOpenMutation.isPending}
				onclick={() => detailOpenMutation.mutate()}
			>
				申请详情
			</button>
		{/if}
		{#if card.canOpenProviderAction}
			<button
				class="secondary-link"
				type="button"
				disabled={!card.providerActionReviewItemId || providerActionMutation.isPending}
				onclick={() => providerActionMutation.mutate()}
			>
				打开猎聘
			</button>
		{/if}
	</div>
	{#if providerMessage}
		<p class="candidate-action-message">{providerMessage}</p>
	{/if}
</article>
