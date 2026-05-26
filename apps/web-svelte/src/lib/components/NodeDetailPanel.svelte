<script lang="ts">
	import ErrorState from './ErrorState.svelte';
	import GraphNodeCandidateList from './GraphNodeCandidateList.svelte';
	import LoadingState from './LoadingState.svelte';
	import type { components } from '$lib/api/schema';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';

	type WorkbenchGraphCandidateSummary =
		components['schemas']['WorkbenchGraphCandidateSummaryResponse'];
	type WorkbenchGraphCandidateListResponse =
		components['schemas']['WorkbenchGraphCandidateListResponse'];
	type WorkbenchGraphCandidateResumeSnapshot =
		components['schemas']['WorkbenchGraphCandidateResumeSnapshotResponse'];

	type NodeDetailPanelProps = {
		sessionId?: string;
		node: RecruiterGraphNode | null;
		graphCandidatePage?: WorkbenchGraphCandidateListResponse | null;
		graphCandidatesLoading?: boolean;
		graphCandidatesError?: string | null;
		selectedGraphCandidateId?: string | null;
		resumeSnapshot?: WorkbenchGraphCandidateResumeSnapshot | null;
		resumeSnapshotLoading?: boolean;
		resumeSnapshotError?: string | null;
		onSelectGraphCandidate?: (candidate: WorkbenchGraphCandidateSummary) => void;
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
		graphCandidatesError = null,
		selectedGraphCandidateId = null,
		resumeSnapshot = null,
		resumeSnapshotLoading = false,
		resumeSnapshotError = null,
		onSelectGraphCandidate
	}: NodeDetailPanelProps = $props();

	const runtimeNode = $derived(
		node?.detailPayload?.kind === 'runtimeGraphNode' ? node.detailPayload.node : null
	);
	const runtimeSections = $derived(runtimeNode?.detailSections ?? []);
	const graphCandidates = $derived(graphCandidatePage?.items ?? []);
	const selectedCandidate = $derived(
		graphCandidates.find((candidate) => candidate.graphCandidateId === selectedGraphCandidateId) ??
			null
	);

	function selectGraphCandidate(candidate: WorkbenchGraphCandidateSummary) {
		onSelectGraphCandidate?.(candidate);
	}

	function candidateScopeText() {
		const scope = runtimeNode?.candidateScope;
		if (!scope || scope.scopeKind === 'none') {
			return scope?.reason ?? '该节点没有候选人列表。';
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

	function snapshotStatusLabel(status: WorkbenchGraphCandidateResumeSnapshot['status']) {
		return statusLabel(status, {
			ready: '已生成安全摘要',
			snapshot_forbidden: '暂无权限查看摘要',
			snapshot_not_found: '暂未生成摘要'
		});
	}

	function statusLabel(value: string | null | undefined, labels: Record<string, string>) {
		if (!value) {
			return '暂无状态';
		}
		return labels[value] ?? value;
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
				{selectedGraphCandidateId}
				onSelectGraphCandidate={selectGraphCandidate}
			/>

			{#if selectedGraphCandidateId}
				<section class="resume-summary" aria-label="简历摘要">
					<header>
						<span>简历摘要</span>
						{#if resumeSnapshot}
							<strong>{snapshotStatusLabel(resumeSnapshot.status)}</strong>
						{/if}
					</header>

					{#if resumeSnapshotLoading}
						<LoadingState label="正在加载安全摘要" />
					{:else if resumeSnapshotError}
						<ErrorState title="简历摘要加载失败" message={resumeSnapshotError} />
					{:else if selectedCandidate && !selectedCandidate.canExpandResume}
						<div class="node-detail-empty compact">
							<strong>暂无可展开摘要</strong>
							<span>当前候选人只有列表级信息。</span>
						</div>
					{:else if resumeSnapshot?.status && resumeSnapshot.status !== 'ready'}
						<div class="node-detail-empty compact">
							<strong>{snapshotStatusLabel(resumeSnapshot.status)}</strong>
							<span>没有展示原始来源内容。</span>
						</div>
					{:else if resumeSnapshot}
						<div class="resume-content">
							{#if resumeSnapshot.profile}
								<section>
									<h3>{resumeSnapshot.profile.displayName || selectedCandidate?.displayName}</h3>
									<p>
										{[
											resumeSnapshot.profile.headline,
											resumeSnapshot.profile.company,
											resumeSnapshot.profile.location
										]
											.filter(Boolean)
											.join(' · ')}
									</p>
									{#if resumeSnapshot.profile.summary}
										<p>{resumeSnapshot.profile.summary}</p>
									{/if}
								</section>
							{/if}

							{#if (resumeSnapshot.workExperience ?? []).length > 0}
								<section>
									<h3>经历</h3>
									<ul>
										{#each resumeSnapshot.workExperience ?? [] as item, index (`work-${index}`)}
											<li>
												<strong>{[item.title, item.company].filter(Boolean).join(' · ')}</strong>
												<span>{item.duration ?? ''}</span>
												{#if item.summary}
													<p>{item.summary}</p>
												{/if}
											</li>
										{/each}
									</ul>
								</section>
							{/if}

							{#if (resumeSnapshot.education ?? []).length > 0}
								<section>
									<h3>教育</h3>
									<ul>
										{#each resumeSnapshot.education ?? [] as item, index (`edu-${index}`)}
											<li>
												<strong>{item.school}</strong>
												<span>{[item.degree, item.major].filter(Boolean).join(' · ')}</span>
											</li>
										{/each}
									</ul>
								</section>
							{/if}

							{#if (resumeSnapshot.projects ?? []).length > 0}
								<section>
									<h3>项目</h3>
									<ul>
										{#each resumeSnapshot.projects ?? [] as item, index (`project-${index}`)}
											<li>
												<strong>{item.name}</strong>
												{#if item.summary}
													<p>{item.summary}</p>
												{/if}
											</li>
										{/each}
									</ul>
								</section>
							{/if}

							{#if (resumeSnapshot.skills ?? []).length > 0}
								<section>
									<h3>技能</h3>
									<div class="badge-row">
										{#each resumeSnapshot.skills ?? [] as skill (skill)}
											<i>{skill}</i>
										{/each}
									</div>
								</section>
							{/if}
						</div>
					{:else}
						<div class="node-detail-empty compact">
							<strong>选择候选人</strong>
							<span>点击候选人后按需加载简历摘要。</span>
						</div>
					{/if}
				</section>
			{/if}
		</div>
	{/if}
</aside>

<style>
	.node-detail-panel {
		display: grid;
		min-width: 320px;
		align-self: stretch;
		border: 1px solid #d7dee8;
		border-radius: 8px;
		background: #ffffff;
		color: #0f172a;
	}

	.node-detail-head {
		display: grid;
		gap: 6px;
		padding: 18px 20px;
		border-bottom: 1px solid #e2e8f0;
	}

	.node-detail-head span,
	.resume-summary header span,
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

	.node-detail-section,
	.resume-summary,
	.resume-content {
		display: grid;
		gap: 12px;
	}

	.node-detail-row {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		padding: 10px 0;
		border-bottom: 1px solid #edf2f7;
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
		border: 1px solid #e2e8f0;
		border-radius: 8px;
		background: #f8fafc;
	}

	p,
	ul,
	h3 {
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
		border: 1px dashed #cbd5e1;
		border-radius: 8px;
	}

	.resume-summary {
		padding-top: 4px;
		border-top: 1px solid #e2e8f0;
	}

	.resume-summary header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}

	.badge-row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.badge-row i {
		padding: 3px 7px;
		border-radius: 999px;
		background: #e0f2fe;
		color: #0369a1;
		font-size: 11px;
		font-style: normal;
		font-weight: 700;
	}

	.resume-content section {
		display: grid;
		gap: 8px;
		padding: 12px;
		border: 1px solid #e2e8f0;
		border-radius: 8px;
		background: #f8fafc;
	}

	.resume-content h3 {
		font-size: 14px;
		line-height: 1.35;
	}

	.resume-content li {
		display: grid;
		gap: 3px;
	}
</style>
