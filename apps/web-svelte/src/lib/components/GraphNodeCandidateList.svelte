<script lang="ts">
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import type {
		WorkbenchGraphCandidateListResponse,
		WorkbenchGraphCandidateSummary
	} from '$lib/workbench/types';

	let {
		sessionId,
		node,
		page = null,
		loading = false,
		error = null,
		selectedGraphCandidateId = null,
		onSelectGraphCandidate
	} = $props<{
		sessionId: string;
		node: RecruiterGraphNode | null;
		page?: WorkbenchGraphCandidateListResponse | null;
		loading?: boolean;
		error?: string | null;
		selectedGraphCandidateId?: string | null;
		onSelectGraphCandidate?: (candidate: WorkbenchGraphCandidateSummary) => void;
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
		if (scope?.nodeKind === 'scoring') return '评分简历';
		if (scope?.nodeKind === 'final') return '最终候选人';
		if (scope?.nodeKind === 'detail_approval') return '待处理简历';
		if (scope?.source === 'cts') return 'CTS 召回简历';
		if (scope?.source === 'liepin') return '猎聘简历';
		return '本节点简历';
	}

	function scoreText(score: number | null | undefined) {
		return score === null || score === undefined ? '暂无分数' : `${String(score)} 分`;
	}
</script>

<div class="graph-candidate-panel" data-session-id={sessionId}>
	<div class="graph-candidate-heading">
		<span>{title}</span>
		<strong>{loading ? '加载中' : `已加载 ${String(items.length)} / 总计 ${String(total)}`}</strong>
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
				<button
					class="graph-candidate-card"
					class:selected={candidate.graphCandidateId === selectedGraphCandidateId}
					type="button"
					data-testid={`graph-candidate-${candidate.graphCandidateId}`}
					aria-pressed={candidate.graphCandidateId === selectedGraphCandidateId}
					onclick={() => onSelectGraphCandidate?.(candidate)}
				>
					<div class="candidate-card-head">
						<div>
							<strong>{candidate.displayName || '未命名候选人'}</strong>
							<span
								>{[candidate.title, candidate.company, candidate.location]
									.filter(Boolean)
									.join(' · ')}</span
							>
						</div>
						<div class="score-badge">{scoreText(candidate.score)}</div>
					</div>
					{#if candidate.summary}
						<p class="graph-candidate-summary">{candidate.summary}</p>
					{/if}
					{#if candidate.sourceBadges.length > 0}
						<div class="badge-row">
							{#each candidate.sourceBadges as badge (badge)}
								<span class="source-badge">{badge}</span>
							{/each}
						</div>
					{/if}
				</button>
			{/each}
		</div>
		{#if page?.truncated}
			<p class="muted">候选人列表已按安全上限截断。</p>
		{/if}
	{/if}
</div>
