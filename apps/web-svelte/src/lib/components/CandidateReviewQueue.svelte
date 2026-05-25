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
				? '可用渠道已完成合并。'
				: coverageStatus === 'degraded'
					? '覆盖不完整：猎聘受阻或部分完成时，仍保留 CTS 候选人。'
					: coverageStatus === 'empty'
						? '暂无最终候选人。'
						: '等待最终 Top 10 排序。';
		if (mergedCount > 0) {
			return `${prefix} 已合并 ${String(mergedCount)} 个重复身份。`;
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
		<p class="muted">正在加载候选人</p>
	{:else if error}
		<p class="form-error" role="alert">{error}</p>
	{:else if cards.length === 0}
		<div class="queue-empty">
			<strong>等待最终排序</strong>
			<span>候选人会按身份合并后进入最终 Top 10 短名单。</span>
		</div>
	{:else}
		<div class="candidate-list">
			{#each cards as card (card.runtimeIdentityId)}
				<CandidateReviewCard {sessionId} {card} />
			{/each}
		</div>
	{/if}
</div>
