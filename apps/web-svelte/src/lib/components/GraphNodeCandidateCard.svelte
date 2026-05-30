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

	const snapshotQuery = createQuery(() => ({
		queryKey: workbenchKeys.resumeSnapshot(sessionId, candidate.graphCandidateId),
		queryFn: () => getGraphCandidateResumeSnapshot(sessionId, candidate.graphCandidateId),
		enabled: Boolean(candidate.canExpandResume && candidate.graphCandidateId)
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

<article
	class="graph-resume-card"
	aria-label={`${candidate.displayName} 原始简历`}
	data-testid={`graph-candidate-card-${candidate.graphCandidateId}`}
>
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

	{#if candidate.canExpandResume}
		<ResumeSnapshotView
			graphCandidateId={candidate.graphCandidateId}
			snapshot={snapshotQuery.data ?? null}
			loading={snapshotQuery.isPending}
			error={snapshotQuery.error ? safeErrorMessage(snapshotQuery.error, '原始简历加载失败') : null}
		/>
	{:else}
		<div class="resume-snapshot">
			<strong>原始简历不可用</strong>
			<p>当前候选人没有可展示的原始来源简历。</p>
		</div>
	{/if}
</article>
