<script lang="ts">
	import GraphNodeCandidateCard from './GraphNodeCandidateCard.svelte';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import type { WorkbenchGraphCandidateListResponse } from '$lib/workbench/types';

	let {
		sessionId,
		node,
		page = null,
		loading = false,
		error = null
	} = $props<{
		sessionId: string;
		node: RecruiterGraphNode | null;
		page?: WorkbenchGraphCandidateListResponse | null;
		loading?: boolean;
		error?: string | null;
	}>();

	const items = $derived(page?.items ?? []);
	const total = $derived(page?.totalGraphCandidates ?? page?.totalEstimate ?? items.length);
	const sourceTotal = $derived(page?.totalSourceResults ?? total);
	const coverage = $derived(page?.coverage ?? null);
	const title = $derived(node ? graphCandidateListTitle(node, page) : '本节点简历');

	function graphCandidateListTitle(
		currentNode: RecruiterGraphNode,
		currentPage: WorkbenchGraphCandidateListResponse | null
	) {
		const scope = currentPage?.nodeScope;
		if (scope?.nodeKind === 'scoring') return '评分原始简历';
		if (scope?.nodeKind === 'final') return '最终原始简历';
		if (scope?.nodeKind === 'detail_approval') return '待处理原始简历';
		if (scope?.source === 'cts') return 'CTS 召回原始简历';
		if (scope?.source === 'liepin') return '猎聘召回原始简历';
		return '本节点原始简历';
	}
</script>

<div class="graph-candidate-panel" data-session-id={sessionId}>
	<div class="graph-candidate-heading">
		<span>{title}</span>
		<strong>{loading ? '加载中' : `${String(items.length)} / ${String(total)} 份`}</strong>
	</div>
	{#if coverage && page?.recoveryState !== 'recoverable_empty'}
		<p class="graph-candidate-coverage">
			本节点共 {String(sourceTotal)} 份简历{coverage.missingSafeIdentityCount > 0
				? `，${String(coverage.missingSafeIdentityCount)} 份身份信息暂不可展示`
				: ''}{coverage.missingSnapshotCount > 0
				? `，${String(coverage.missingSnapshotCount)} 份快照暂未写入`
				: ''}{coverage.forbiddenSnapshotCount > 0
				? `，${String(coverage.forbiddenSnapshotCount)} 份受限`
				: ''}
		</p>
	{/if}
	{#if page?.generatedAt}
		<p class="graph-candidate-coverage">生成时间：{page.generatedAt}</p>
	{/if}
	{#if coverage && coverage.droppedRows > 0}
		<p class="form-error" role="alert">
			有 {String(coverage.droppedRows)} 份简历未能展示，请检查数据投影。
		</p>
	{/if}
	{#if loading}
		<p class="muted">正在读取这个节点的原始简历...</p>
	{:else if error}
		<p class="form-error" role="alert">{error}</p>
	{:else if page?.recoveryState === 'recoverable_empty'}
		<div class="queue-empty compact-empty">
			<strong>原始简历需要恢复</strong>
			<span>{page.recoveryReason ?? '该节点的原始简历暂时不可读取。'}</span>
		</div>
	{:else if items.length === 0}
		<div class="queue-empty compact-empty">
			<strong>暂无原始简历</strong>
			<span>该节点还没有可展示的原始简历。</span>
		</div>
	{:else}
		<div class="graph-candidate-list">
			{#each items as candidate (candidate.graphCandidateId)}
				<GraphNodeCandidateCard {sessionId} {candidate} />
			{/each}
		</div>
		{#if page?.truncated}
			<p class="muted">原始简历已按上限截断。</p>
		{/if}
	{/if}
</div>
