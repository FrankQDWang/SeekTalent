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
		if (status === 'pending') return 'Detail request is waiting for approval.';
		if (status === 'bypassed') return 'Detail lease is reserved by bypass mode.';
		if (status === 'approved') return 'Detail lease is approved and reserved.';
		return `Detail request ${status}.`;
	}
</script>

<article class="candidate-card" data-testid={`candidate-card-${card.runtimeIdentityId}`}>
	<div class="candidate-card-head">
		<div>
			<strong>{card.displayName || '候选人'}</strong>
			<span
				>{[card.title, card.company, card.location].filter(Boolean).join(' · ') ||
					'Profile summary'}</span
			>
		</div>
		<div class="score-badge">{card.aggregateScore ?? '-'}</div>
	</div>

	<div class="badge-row">
		<span class="source-badge">Rank {card.rank}</span>
		{#each card.sourceBadges as badge (badge)}
			<span class="source-badge">{badge}</span>
		{/each}
		<span class="source-badge muted-badge">{card.evidenceLevel}</span>
		{#if card.status}
			<span class:approved={card.status === 'promising'} class="status-pill">{card.status}</span>
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
		<span>Review ids</span>
		<p>{card.mergedReviewItemIds.join(' / ')}</p>
	</div>

	{#if card.matchedMustHaves.length > 0}
		<div class="candidate-facts">
			<span>Must</span>
			<p>{card.matchedMustHaves.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.strengths.length > 0}
		<div class="candidate-facts">
			<span>入围理由</span>
			<p>{card.strengths.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if card.missingRisks.length > 0}
		<div class="candidate-facts">
			<span>Risk</span>
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
		<span>Note</span>
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
			Mark promising
		</button>
		<button
			class="secondary-link"
			type="button"
			disabled={!card.actionReviewItemId || updateMutation.isPending}
			onclick={() => updateCandidate({ status: 'rejected' })}
		>
			Reject
		</button>
		<button
			class="secondary-link"
			type="button"
			disabled={!card.actionReviewItemId || updateMutation.isPending}
			onclick={() => updateCandidate({})}
		>
			Save note
		</button>
		{#if card.canRequestLiepinDetail}
			<button
				class="secondary-link"
				type="button"
				disabled={detailOpenMutation.isPending}
				onclick={() => detailOpenMutation.mutate()}
			>
				Request detail
			</button>
		{/if}
		{#if card.canOpenProviderAction}
			<button
				class="secondary-link"
				type="button"
				disabled={!card.providerActionReviewItemId || providerActionMutation.isPending}
				onclick={() => providerActionMutation.mutate()}
			>
				Open Liepin
			</button>
		{/if}
	</div>
	{#if providerMessage}
		<p class="candidate-action-message">{providerMessage}</p>
	{/if}
</article>
