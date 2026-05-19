<script lang="ts">
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import type { WorkbenchGraphCandidateListResponse } from '$lib/workbench/types';
	import GraphNodeCandidateCard from './GraphNodeCandidateCard.svelte';

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
	const title = $derived(node ? graphCandidateListTitle(node) : '本节点简历');

	function graphCandidateListTitle(currentNode: RecruiterGraphNode) {
		if (currentNode.detailKind === 'ctsRoundResults') return '召回简历';
		if (currentNode.detailKind === 'ctsRoundScoring') return '评分简历';
		if (
			currentNode.detailKind === 'liepinCardSearch' ||
			currentNode.detailKind === 'liepinCardCandidates'
		) {
			return '本轮简历';
		}
		if (currentNode.detailKind === 'liepinDetailApproval') return '待处理简历';
		return '本节点简历';
	}
</script>

<div class="graph-candidate-panel">
	<div class="graph-candidate-heading">
		<span>{title}</span>
		<strong>{loading ? '加载中' : `已加载 ${String(items.length)} / 总计 ${String(total)}`}</strong>
	</div>
	{#if coverage && page?.recoveryState !== 'recoverable_empty'}
		<p class="graph-candidate-coverage">
			本节点共 {String(sourceTotal)} 份简历{coverage.missingSnapshotCount > 0
				? `，${String(coverage.missingSnapshotCount)} 份快照暂未写入`
				: ''}{coverage.forbiddenSnapshotCount > 0
				? `，${String(coverage.forbiddenSnapshotCount)} 份受限`
				: ''}
		</p>
	{/if}
	{#if coverage && coverage.droppedRows > 0}
		<p class="form-error" role="alert">
			有 {String(coverage.droppedRows)} 份简历未能展示，请检查数据投影。
		</p>
	{/if}
	{#if loading}
		<p class="muted">正在读取这个节点对应的候选人...</p>
	{:else if error}
		<p class="form-error" role="alert">{error}</p>
	{:else if page?.recoveryState === 'recoverable_empty'}
		<div class="queue-empty compact-empty">
			<strong>候选人索引需要恢复</strong>
			<span>{page.recoveryReason ?? '该节点的候选人关系暂时不可读取。'}</span>
		</div>
	{:else if items.length === 0}
		<div class="queue-empty compact-empty">
			<strong>暂无候选人明细</strong>
			<span>该节点还没有可展示的候选人摘要。</span>
		</div>
	{:else}
		<div class="graph-candidate-list">
			{#each items as candidate (candidate.graphCandidateId)}
				<GraphNodeCandidateCard {sessionId} {candidate} />
			{/each}
		</div>
		{#if page?.truncated}
			<p class="muted">候选人列表已按安全上限截断。</p>
		{/if}
	{/if}
</div>
