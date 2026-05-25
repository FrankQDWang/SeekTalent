<script lang="ts">
	import ErrorState from './ErrorState.svelte';
	import LoadingState from './LoadingState.svelte';
	import type { components } from '$lib/api/schema';
	import type {
		RecruiterGraphDetailPayload,
		RecruiterGraphNode,
		SourceKind
	} from '$lib/workbench/recruiterAnimation';

	type WorkbenchGraphCandidateSummary =
		components['schemas']['WorkbenchGraphCandidateSummaryResponse'];
	type WorkbenchGraphCandidateResumeSnapshot =
		components['schemas']['WorkbenchGraphCandidateResumeSnapshotResponse'];

	type DetailItem =
		| { type: 'row'; label: string; value: string | number | null | undefined }
		| { type: 'block'; title: string; value: string | null | undefined }
		| { type: 'list'; title: string; values: string[] };

	type NodeDetailPanelProps = {
		node: RecruiterGraphNode | null;
		graphCandidates?: WorkbenchGraphCandidateSummary[];
		graphCandidatesLoading?: boolean;
		graphCandidatesError?: string | null;
		selectedGraphCandidateId?: string | null;
		resumeSnapshot?: WorkbenchGraphCandidateResumeSnapshot | null;
		resumeSnapshotLoading?: boolean;
		resumeSnapshotError?: string | null;
		onSelectGraphCandidate?: (candidate: WorkbenchGraphCandidateSummary) => void;
	};

	const sourceLabels: Record<SourceKind, string> = {
		cts: 'CTS',
		liepin: '猎聘'
	};

	let {
		node,
		graphCandidates = [],
		graphCandidatesLoading = false,
		graphCandidatesError = null,
		selectedGraphCandidateId = null,
		resumeSnapshot = null,
		resumeSnapshotLoading = false,
		resumeSnapshotError = null,
		onSelectGraphCandidate
	}: NodeDetailPanelProps = $props();

	const detailItems = $derived(node?.detailPayload ? payloadDetailItems(node.detailPayload) : []);
	const selectedCandidate = $derived(
		graphCandidates.find((candidate) => candidate.graphCandidateId === selectedGraphCandidateId) ??
			null
	);

	function selectGraphCandidate(candidate: WorkbenchGraphCandidateSummary) {
		onSelectGraphCandidate?.(candidate);
	}

	function payloadDetailItems(payload: RecruiterGraphDetailPayload): DetailItem[] {
		switch (payload.kind) {
			case 'reflection':
				return [
					detailRow('轮次', `第 ${String(payload.roundNo)} 轮`),
					detailBlock('总结', payload.summary ? `总结：${payload.summary}` : ''),
					detailBlock('原因', payload.rationale),
					detailBlock('下一步', payload.nextDirection)
				];
			case 'requirements': {
				const sheet = payload.requirementSheet;
				return [
					detailRow('状态', requirementStatusLabel(payload.reviewStatus)),
					detailBlock('role_summary', sheet?.role_summary ?? ''),
					detailList('title_anchor_terms', sheet?.title_anchor_terms ?? []),
					detailBlock('title_anchor_rationale', sheet?.title_anchor_rationale ?? ''),
					detailList('must_have_capabilities', sheet?.must_have_capabilities ?? []),
					detailList('preferred_capabilities', sheet?.preferred_capabilities ?? []),
					detailList('exclusion_signals', sheet?.exclusion_signals ?? []),
					detailBlock('hard_constraints', JSON.stringify(sheet?.hard_constraints ?? {})),
					detailBlock('preferences', JSON.stringify(sheet?.preferences ?? {})),
					detailBlock(
						'initial_query_term_pool',
						JSON.stringify(sheet?.initial_query_term_pool ?? [])
					),
					detailBlock('scoring_rationale', sheet?.scoring_rationale ?? '')
				];
			}
			case 'ctsRoundQuery':
				return [
					detailRow('轮次', `第 ${String(payload.roundNo)} 轮`),
					detailBlock('关键词', payload.queryLabel),
					detailList('查询词', payload.queryTerms),
					detailList(
						'检索分支',
						(payload.executedQueries ?? []).map((query) =>
							[
								query.lane_type ?? 'default',
								query.query_role,
								query.query_terms.join(' / ') || query.keyword_query,
								query.query_instance_id
							]
								.filter(Boolean)
								.join(' · ')
						)
					)
				];
			case 'ctsRoundResults':
				return [
					detailRow('轮次', `第 ${String(payload.roundNo)} 轮`),
					detailRow('原始命中', `${String(payload.rawCandidateCount)} 人`),
					detailRow('新增候选人', `${String(payload.uniqueNewCount)} 人`),
					detailBlock(
						'召回分布',
						payload.recallCounts ? textFromRecord(payload.recallCounts) : null
					)
				];
			case 'ctsRoundScoring':
				return [
					detailRow('轮次', `第 ${String(payload.roundNo)} 轮`),
					detailRow('进入评分', `${String(payload.scoredCount ?? payload.newlyScoredCount)} 人`),
					detailRow('Fit', `${String(payload.fitCount)} 人`),
					detailRow('Not fit', `${String(payload.notFitCount)} 人`)
				];
			case 'sourceQueue':
				return [
					detailRow('渠道', sourceLabels[payload.sourceKind]),
					detailRow('状态', sourceRunStatusLabel(payload.status)),
					detailRow('授权', authStatusLabel(payload.authState ?? payload.connectionStatus)),
					detailRow('已扫描', `${String(payload.cardsScannedCount)} 张`),
					detailRow('去重候选人', `${String(payload.uniqueCandidatesCount)} 人`),
					detailRow(
						'运行状态',
						payload.runtimeStatus ? runtimeStatusLabel(payload.runtimeStatus) : null
					),
					detailRow(
						'最新事件',
						payload.runtimeEventType ? runtimeEventLabel(payload.runtimeEventType) : null
					),
					detailRow('事件序号', payload.runtimeEventSeq),
					detailRow(
						'运行扫描',
						payload.runtimeStatus ? `${String(payload.runtimeCardsSeenCount ?? 0)} 张` : null
					),
					detailRow(
						'已过滤',
						payload.runtimeStatus ? `${String(payload.runtimeCardsFilteredCount ?? 0)} 张` : null
					),
					detailRow(
						'运行候选人',
						payload.runtimeStatus ? `${String(payload.runtimeCandidatesCount ?? 0)} 人` : null
					),
					detailRow(
						'详情推荐',
						payload.runtimeStatus
							? `${String(payload.runtimeDetailRecommendationsCount ?? 0)} 个`
							: null
					),
					detailRow(
						'详情状态',
						payload.runtimeDetailState ? detailStateLabel(payload.runtimeDetailState) : null
					),
					detailBlock('提示', payload.warningMessage)
				];
			case 'liepinCardSearch':
				return [
					detailRow('已扫描', `${String(payload.cardsScannedCount)} 张`),
					detailRow('去重候选人', `${String(payload.uniqueCandidatesCount)} 人`),
					...detailRequestItems(payload)
				];
			case 'liepinDetailApproval':
				return detailRequestItems(payload);
			case 'liepinCardCandidates':
				return [
					detailRow('候选人数', `${String(payload.candidateReviewItemIds.length)} 人`),
					detailRow('最高分', scoreText(payload.bestScore)),
					detailRow('证据数', `${String(payload.candidateEvidenceRefs.length)} 条`),
					...detailRequestItems(payload)
				];
			case 'aggregation':
				return [
					detailRow('候选人数', `${String(payload.candidateCount)} 人`),
					detailRow('最高分', scoreText(payload.bestScore)),
					detailRow(
						'覆盖状态',
						payload.coverageStatus ? coverageStatusLabel(payload.coverageStatus) : null
					),
					detailRow('完成版本', payload.finalizationRevision),
					detailRow(
						'完成原因',
						payload.finalizationReasonCode
							? finalizationReasonLabel(payload.finalizationReasonCode)
							: null
					),
					detailRow(
						'已合并身份',
						payload.identityMergeCount ? `${String(payload.identityMergeCount)} 个` : null
					),
					detailRow(
						'待确认重复',
						payload.ambiguousDuplicateCount ? `${String(payload.ambiguousDuplicateCount)} 个` : null
					),
					detailRow(
						'标准简历',
						payload.canonicalResumeSelectedCount
							? `${String(payload.canonicalResumeSelectedCount)} 份`
							: null
					),
					detailList(
						'渠道状态',
						(payload.sourceStates ?? []).map((source) =>
							[
								sourceLabels[source.sourceKind],
								runtimeStatusLabel(source.status),
								`已扫描 ${String(source.cardsSeenCount)}`,
								source.cardsFilteredCount > 0
									? `已过滤 ${String(source.cardsFilteredCount)}`
									: null,
								`候选人 ${String(source.candidatesCount)}`,
								source.detailRecommendationsCount > 0
									? `详情推荐 ${String(source.detailRecommendationsCount)}`
									: null,
								source.detailState ? detailStateLabel(source.detailState) : null
							]
								.filter(Boolean)
								.join(' · ')
						)
					),
					detailBlock('最终报告', payload.finalReport),
					detailRow('结束原因', payload.stopReason)
				];
			case 'job':
				return [
					detailRow('岗位', payload.jobTitle),
					detailRow('检索模式', payload.sourceKinds.map((kind) => sourceLabels[kind]).join(' / ')),
					detailBlock('JD 预览', clip(payload.jdText, 260))
				];
		}
	}

	function detailRequestItems(
		payload: Extract<
			RecruiterGraphDetailPayload,
			{ kind: 'liepinCardSearch' | 'liepinCardCandidates' | 'liepinDetailApproval' }
		>
	): DetailItem[] {
		return [
			detailRow('详情请求', `${String(payload.detailOpenRequestIds.length)} 个`),
			detailList('请求摘要', payload.requestSummaries),
			detailBlock('预算状态', payload.budgetText)
		];
	}

	function detailRow(label: string, value: string | number | null | undefined): DetailItem {
		return { type: 'row', label, value };
	}

	function detailBlock(title: string, value: string | null | undefined): DetailItem {
		return { type: 'block', title, value };
	}

	function detailList(title: string, values: string[]): DetailItem {
		return { type: 'list', title, values };
	}

	function requirementStatusLabel(status: 'confirmed' | 'draft' | 'runtime') {
		if (status === 'confirmed') return '已确认';
		if (status === 'runtime') return '运行时解析';
		return '草稿';
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

	function scoreText(score: number | null | undefined) {
		return score === null || score === undefined ? '暂无分数' : `${String(score)} 分`;
	}

	function sourceRunStatusLabel(status: string | null | undefined) {
		return statusLabel(status, {
			queued: '等待中',
			blocked: '已阻塞',
			running: '运行中',
			completed: '已完成',
			failed: '失败'
		});
	}

	function authStatusLabel(status: string | null | undefined) {
		return statusLabel(status, {
			not_required: '无需授权',
			login_required: '需要登录',
			login_in_progress: '登录中',
			verification_required: '需要验证',
			connected: '已连接',
			expired: '已过期',
			blocked: '已阻塞',
			disconnected: '未连接'
		});
	}

	function runtimeStatusLabel(status: string | null | undefined) {
		return statusLabel(status, {
			pending: '等待中',
			running: '运行中',
			completed: '已完成',
			partial: '部分完成',
			blocked: '已阻塞',
			failed: '失败',
			cancelled: '已取消'
		});
	}

	function runtimeEventLabel(eventType: string | null | undefined) {
		return statusLabel(eventType, {
			source_lane_started: '渠道已启动',
			source_lane_completed: '渠道已完成',
			source_lane_blocked: '渠道已阻塞',
			source_lane_partial: '渠道部分完成',
			source_lane_failed: '渠道失败',
			source_lane_cancelled: '渠道已取消',
			detail_recommended: '已推荐详情',
			detail_approved: '详情已批准',
			detail_leased: '详情已预留',
			detail_completed: '详情已完成',
			detail_blocked: '详情已阻塞'
		});
	}

	function coverageStatusLabel(status: string | null | undefined) {
		return statusLabel(status, {
			pending: '等待覆盖',
			complete: '全部覆盖',
			degraded: '覆盖不完整',
			empty: '无候选人'
		});
	}

	function detailStateLabel(status: string | null | undefined) {
		return statusLabel(status, {
			detail_recommended: '已推荐详情',
			pending_approval: '等待批准',
			leased: '已预留详情',
			completed: '详情已完成',
			blocked: '详情已阻塞'
		});
	}

	function finalizationReasonLabel(reason: string | null | undefined) {
		return statusLabel(reason, {
			source_lanes_completed: '所有渠道已完成',
			source_lanes_degraded: '部分渠道不可用',
			detail_enrichment_applied: '详情已补充'
		});
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

	function textFromRecord(value: Record<string, unknown>) {
		return Object.entries(value)
			.map(([key, item]) => `${key}: ${String(item)}`)
			.join(' / ');
	}

	function clip(value: string, maxLength: number) {
		const trimmed = value.trim();
		if (trimmed.length <= maxLength) {
			return trimmed;
		}
		return `${trimmed.slice(0, maxLength - 1)}...`;
	}

	function hasValue(value: string | number | null | undefined): value is string | number {
		return value !== null && value !== undefined && String(value).trim().length > 0;
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
			{#if detailItems.length > 0}
				<section class="node-detail-section" aria-label="节点业务细节">
					{#each detailItems as item, index (`${item.type}-${index}`)}
						{#if item.type === 'row'}
							<div class="node-detail-row">
								<span>{item.label}</span>
								<strong>{hasValue(item.value) ? item.value : '暂无数据'}</strong>
							</div>
						{:else if item.type === 'block'}
							<section class="node-detail-block">
								<span>{item.title}</span>
								<p class:muted={!hasValue(item.value)}>
									{hasValue(item.value) ? item.value : '暂无数据'}
								</p>
							</section>
						{:else}
							<section class="node-detail-block">
								<span>{item.title}</span>
								{#if item.values.length > 0}
									<ul>
										{#each item.values as value, valueIndex (`${value}-${valueIndex}`)}
											<li>{value}</li>
										{/each}
									</ul>
								{:else}
									<p class="muted">暂无数据</p>
								{/if}
							</section>
						{/if}
					{/each}
				</section>
			{:else}
				<div class="node-detail-empty compact">
					<strong>暂无业务细节</strong>
					<span>该节点还没有结构化详情。</span>
				</div>
			{/if}

			<section class="node-detail-candidates" aria-label="节点候选人">
				<header>
					<span>图谱候选人</span>
					<strong>{graphCandidates.length} 人</strong>
				</header>

				{#if graphCandidatesLoading}
					<LoadingState label="正在加载节点候选人" />
				{:else if graphCandidatesError}
					<ErrorState title="候选人加载失败" message={graphCandidatesError} />
				{:else if graphCandidates.length === 0}
					<div class="node-detail-empty compact">
						<strong>暂无候选人</strong>
						<span>该节点当前没有可展示的图谱候选人。</span>
					</div>
				{:else}
					<div class="candidate-list">
						{#each graphCandidates as candidate (candidate.graphCandidateId)}
							<button
								class="candidate-card"
								class:selected={candidate.graphCandidateId === selectedGraphCandidateId}
								type="button"
								data-testid={`graph-candidate-${candidate.graphCandidateId}`}
								aria-pressed={candidate.graphCandidateId === selectedGraphCandidateId}
								onclick={() => selectGraphCandidate(candidate)}
							>
								<span class="candidate-topline">
									<strong>{candidate.displayName || '未命名候选人'}</strong>
									<em>{scoreText(candidate.score)}</em>
								</span>
								<span
									>{[candidate.title, candidate.company, candidate.location]
										.filter(Boolean)
										.join(' · ')}</span
								>
								{#if candidate.summary}
									<small>{candidate.summary}</small>
								{/if}
								{#if candidate.sourceBadges.length > 0}
									<span class="badge-row">
										{#each candidate.sourceBadges as badge (badge)}
											<i>{badge}</i>
										{/each}
									</span>
								{/if}
							</button>
						{/each}
					</div>
				{/if}
			</section>

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
	.node-detail-candidates header span,
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
	.node-detail-candidates,
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
	.node-detail-empty span,
	.candidate-card span,
	.candidate-card small {
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

	.node-detail-candidates,
	.resume-summary {
		padding-top: 4px;
		border-top: 1px solid #e2e8f0;
	}

	.node-detail-candidates header,
	.resume-summary header,
	.candidate-topline {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}

	.candidate-list {
		display: grid;
		gap: 10px;
	}

	.candidate-card {
		display: grid;
		gap: 7px;
		padding: 12px;
		border: 1px solid #e2e8f0;
		border-radius: 8px;
		background: #ffffff;
		text-align: left;
		cursor: pointer;
	}

	.candidate-card:hover,
	.candidate-card:focus-visible,
	.candidate-card.selected {
		border-color: #0f766e;
		outline: 2px solid color-mix(in srgb, #0f766e 20%, transparent);
		outline-offset: 2px;
	}

	.candidate-card em {
		color: #0f766e;
		font-style: normal;
		font-weight: 700;
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
