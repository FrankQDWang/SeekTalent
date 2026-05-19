<script lang="ts">
	import { buildFinalCandidateCards } from '$lib/workbench/finalCandidateCards';
	import type {
		WorkbenchCandidateReviewItem,
		WorkbenchFinalTopCandidateListResponse
	} from '$lib/workbench/types';
	import CandidateReviewCard from './CandidateReviewCard.svelte';

	let {
		sessionId,
		finalTop,
		reviewItems = [],
		loading = false,
		error = null
	} = $props<{
		sessionId: string;
		finalTop?: WorkbenchFinalTopCandidateListResponse | null;
		reviewItems?: WorkbenchCandidateReviewItem[];
		loading?: boolean;
		error?: string | null;
	}>();

	const emptyFinalTop = $derived<WorkbenchFinalTopCandidateListResponse>(
		finalTop ?? { items: [], coverageStatus: 'pending', finalizationRevision: null }
	);
	const cards = $derived(buildFinalCandidateCards({ finalTop: emptyFinalTop, reviewItems }));
	const mergedIdentityCount = $derived(
		cards.filter((card) => card.mergedReviewItemIds.length > 1).length
	);
	const coverageText = $derived(coverageSummary(emptyFinalTop.coverageStatus, mergedIdentityCount));

	function coverageSummary(
		coverageStatus: WorkbenchFinalTopCandidateListResponse['coverageStatus'],
		mergedCount: number
	) {
		const prefix =
			coverageStatus === 'complete'
				? 'Dual-source complete where available.'
				: coverageStatus === 'degraded'
					? 'Coverage degraded: CTS candidates remain visible when Liepin is blocked or partial.'
					: coverageStatus === 'empty'
						? 'No final candidates yet.'
						: 'Waiting for final Top 10 ranking.';
		if (mergedCount > 0) {
			return `${prefix} ${String(mergedCount)} merged duplicate identities.`;
		}
		return prefix;
	}
</script>

<div class="queue-panel" aria-labelledby="candidate-review-queue-title">
	<div class="queue-heading">
		<span id="candidate-review-queue-title">最终短名单</span>
		<strong>{loading ? '加载中' : cards.length}</strong>
	</div>
	<p class="graph-candidate-summary">{coverageText}</p>
	{#if loading}
		<p class="muted">Loading candidates</p>
	{:else if error}
		<p class="form-error" role="alert">{error}</p>
	{:else if cards.length === 0}
		<div class="queue-empty">
			<strong>等待最终排序</strong>
			<span>候选人会从 identity-level Final Top 10 进入短名单。</span>
		</div>
	{:else}
		<div class="candidate-list">
			{#each cards as card (card.runtimeIdentityId)}
				<CandidateReviewCard {sessionId} {card} />
			{/each}
		</div>
	{/if}
</div>
