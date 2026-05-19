<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { getGraphCandidateResumeSnapshot } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';
	import type { WorkbenchGraphCandidateSummary } from '$lib/workbench/types';
	import ResumeSnapshotView from './ResumeSnapshotView.svelte';

	let { sessionId, candidate } = $props<{
		sessionId: string;
		candidate: WorkbenchGraphCandidateSummary;
	}>();

	let expanded = $state(false);
	const snapshotQuery = createQuery(() => ({
		queryKey: workbenchKeys.resumeSnapshot(sessionId, candidate.graphCandidateId),
		queryFn: () => getGraphCandidateResumeSnapshot(sessionId, candidate.graphCandidateId),
		enabled: expanded && candidate.canExpandResume
	}));

	const subtitle = $derived(
		[candidate.title, candidate.company, candidate.location].filter(Boolean).join(' · ')
	);

	function relationshipLabel(kind: WorkbenchGraphCandidateSummary['relationshipKind']) {
		if (kind === 'new') return '新增召回';
		if (kind === 'recalled') return '召回';
		if (kind === 'scored') return '已评分';
		if (kind === 'fit') return '匹配';
		if (kind === 'not_fit') return '不匹配';
		if (kind === 'final') return '入围';
		return '详情请求';
	}
</script>

<article class:expanded class="graph-candidate-card">
	<div class="candidate-card-head">
		<div>
			<strong>{candidate.displayName}</strong>
			<span>{subtitle || relationshipLabel(candidate.relationshipKind)}</span>
		</div>
		<div class="score-badge">{candidate.score ?? '-'}</div>
	</div>
	<div class="badge-row">
		{#each candidate.sourceBadges as badge (badge)}
			<span class="source-badge">{badge}</span>
		{/each}
		<span class="source-badge muted-badge">{relationshipLabel(candidate.relationshipKind)}</span>
		{#if candidate.fitBucket}
			<span class="source-badge muted-badge">{candidate.fitBucket}</span>
		{/if}
	</div>
	<p class="graph-candidate-summary">{candidate.summary || '暂无简介'}</p>
	{#if candidate.matchedMustHaves.length > 0}
		<div class="candidate-facts">
			<span>Must</span>
			<p>{candidate.matchedMustHaves.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if candidate.strengths.length > 0}
		<div class="candidate-facts">
			<span>入围理由</span>
			<p>{candidate.strengths.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	{#if candidate.missingRisks.length > 0}
		<div class="candidate-facts">
			<span>Risk</span>
			<p>{candidate.missingRisks.slice(0, 4).join(' / ')}</p>
		</div>
	{/if}
	<div class="candidate-actions">
		<button
			class="secondary-link"
			type="button"
			disabled={!candidate.canExpandResume}
			aria-expanded={expanded}
			onclick={() => {
				expanded = !expanded;
			}}
		>
			{expanded
				? '收起安全简历摘要'
				: candidate.canExpandResume
					? '查看安全简历摘要'
					: '简历摘要不可用'}
		</button>
	</div>
	{#if expanded}
		<ResumeSnapshotView
			graphCandidateId={candidate.graphCandidateId}
			snapshot={snapshotQuery.data ?? null}
			loading={snapshotQuery.isPending}
			error={snapshotQuery.error ? safeErrorMessage(snapshotQuery.error, '简历摘要加载失败') : null}
		/>
	{/if}
</article>
