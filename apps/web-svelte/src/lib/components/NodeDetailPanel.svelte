<script lang="ts">
	import GraphNodeCandidateList from './GraphNodeCandidateList.svelte';
	import type { components } from '$lib/api/schema';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';

	type WorkbenchGraphCandidateListResponse =
		components['schemas']['WorkbenchGraphCandidateListResponse'];

	type NodeDetailPanelProps = {
		sessionId?: string;
		node: RecruiterGraphNode | null;
		graphCandidatePage?: WorkbenchGraphCandidateListResponse | null;
		graphCandidatesLoading?: boolean;
		graphCandidatesError?: string | null;
	};

	const sourceLabels: Record<'cts' | 'liepin', string> = {
		cts: 'CTS',
		liepin: '猎聘'
	};

	let {
		sessionId = '',
		node,
		graphCandidatePage = null,
		graphCandidatesLoading = false,
		graphCandidatesError = null
	}: NodeDetailPanelProps = $props();

	const runtimeNode = $derived(
		node?.detailPayload?.kind === 'runtimeGraphNode' ? node.detailPayload.node : null
	);
	const runtimeSections = $derived(runtimeNode?.detailSections ?? []);

	function candidateScopeText() {
		const scope = runtimeNode?.candidateScope;
		if (!scope || scope.scopeKind === 'none') {
			return scope?.reason ?? '该节点没有原始简历。';
		}
		return [
			scope.scopeKind,
			scope.sourceKind,
			scope.roundNo ? `第 ${String(scope.roundNo)} 轮` : null
		]
			.filter(Boolean)
			.join(' · ');
	}

	function sourceLabel(sourceKind: RecruiterGraphNode['sourceKind']) {
		if (sourceKind === 'cts' || sourceKind === 'liepin') {
			return sourceLabels[sourceKind];
		}
		if (sourceKind === 'all') {
			return 'All sources';
		}
		return '未标记渠道';
	}
</script>

<aside class="node-detail-panel" data-testid="node-detail-panel">
	{#if !node}
		<div class="node-detail-empty">
			<strong>未选择节点</strong>
			<span>点击策略图节点后查看业务细节。</span>
		</div>
	{:else}
		<header class="node-detail-head">
			<span>{node.kind}</span>
			<h2>{node.label}</h2>
			<small>{node.sourceLabel ?? sourceLabel(node.sourceKind)}</small>
		</header>

		<div class="node-detail-body">
			{#if runtimeNode}
				<section class="node-detail-section" aria-label="节点业务细节">
					<section class="node-detail-block">
						<span>节点说明</span>
						<p>{runtimeNode.summaryText}</p>
					</section>
					<section class="node-detail-block">
						<span>候选人范围</span>
						<p>{candidateScopeText()}</p>
					</section>
					{#each runtimeSections as section (`${section.heading}-${section.kind}`)}
						<section class="node-detail-block">
							<span>{section.heading}</span>
							{#if section.kind === 'text'}
								<p class:muted={!section.text}>{section.text || '暂无数据'}</p>
							{:else if section.kind === 'facts'}
								{#if (section.facts ?? []).length > 0}
									<div class="node-detail-facts">
										{#each section.facts ?? [] as fact (`${fact.label}-${fact.value}`)}
											<div class="node-detail-row">
												<span>{fact.label}</span>
												<strong>{fact.value}</strong>
											</div>
										{/each}
									</div>
								{:else}
									<p class="muted">暂无数据</p>
								{/if}
							{:else if (section.values ?? []).length > 0}
								<ul>
									{#each section.values ?? [] as value (value)}
										<li>{value}</li>
									{/each}
								</ul>
							{:else}
								<p class="muted">暂无数据</p>
							{/if}
						</section>
					{/each}
				</section>
			{:else}
				<div class="node-detail-empty compact">
					<strong>暂无业务细节</strong>
					<span>该节点还没有结构化详情。</span>
				</div>
			{/if}

			<GraphNodeCandidateList
				{sessionId}
				{node}
				page={graphCandidatePage}
				loading={graphCandidatesLoading}
				error={graphCandidatesError}
			/>
		</div>
	{/if}
</aside>

<style>
	.node-detail-panel {
		display: grid;
		min-width: 320px;
		align-self: stretch;
		background: #ffffff;
		color: #0f172a;
	}

	.node-detail-head {
		display: grid;
		gap: 6px;
		padding: 18px 20px;
	}

	.node-detail-head span,
	.node-detail-block > span {
		color: #64748b;
		font-size: 12px;
		font-weight: 700;
	}

	.node-detail-head h2 {
		margin: 0;
		font-size: 18px;
		line-height: 1.3;
	}

	.node-detail-head small {
		color: #0f766e;
		font-size: 12px;
		font-weight: 700;
	}

	.node-detail-body {
		display: grid;
		gap: 18px;
		align-content: start;
		padding: 18px;
	}

	.node-detail-section {
		display: grid;
		gap: 12px;
	}

	.node-detail-row {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		padding: 10px 0;
		font-size: 13px;
	}

	.node-detail-row span {
		color: #64748b;
	}

	.node-detail-row strong {
		text-align: right;
	}

	.node-detail-block {
		display: grid;
		gap: 7px;
		padding: 12px;
		background: #f8fafc;
	}

	p,
	ul {
		margin: 0;
	}

	p,
	li,
	.node-detail-empty span {
		color: #475569;
		font-size: 13px;
		line-height: 1.55;
	}

	ul {
		display: grid;
		gap: 6px;
		padding-left: 18px;
	}

	.muted {
		color: #94a3b8;
	}

	.node-detail-empty {
		display: grid;
		min-height: 220px;
		place-content: center;
		gap: 6px;
		padding: 24px;
		color: #475569;
		text-align: center;
	}

	.node-detail-empty.compact {
		min-height: 96px;
	}

</style>
