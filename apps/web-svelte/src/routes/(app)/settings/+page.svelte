<script lang="ts">
	import { resolve } from '$app/paths';
	import { createQuery } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { listSourceConnections } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';

	const connectionsQuery = createQuery(() => ({
		queryKey: workbenchKeys.sourceConnections,
		queryFn: listSourceConnections
	}));

	function sourceKindLabel(kind: string) {
		const labels: Record<string, string> = {
			cts: '结构化简历库',
			liepin: '猎聘'
		};
		return labels[kind] ?? kind;
	}

	function statusLabel(status: string) {
		const labels: Record<string, string> = {
			available: '可用',
			connected: '已连接',
			needs_login: '需登录',
			login_required: '需登录',
			login_in_progress: '登录中',
			verification_required: '待验证'
		};
		return labels[status] ?? status;
	}
</script>

<section class="settings-page">
	<div class="panel settings-panel">
		<div class="panel-head">
			<p class="section-label">设置</p>
			<h2>检索渠道</h2>
		</div>
		<p class="muted">管理招聘检索渠道和连接状态。</p>
		<nav class="settings-nav" aria-label="设置分区">
			<a class="primary-action" href={resolve('/settings/sources')}>打开渠道设置</a>
		</nav>
		{#if connectionsQuery.isPending}
			<p class="muted">正在加载渠道连接</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, '渠道设置加载失败')}
			</p>
		{:else}
			<div class="source-settings-list">
				<article class="connection-card compact">
					<div>
						<strong>CTS</strong>
						<span>本地结构化简历库</span>
					</div>
					<dl>
						<div>
							<dt>状态</dt>
							<dd>可用</dd>
						</div>
					</dl>
				</article>
				{#each connectionsQuery.data?.connections ?? [] as connection (connection.connectionId)}
					<article class="connection-card compact">
						<div>
							<strong>{connection.label}</strong>
							<span>{sourceKindLabel(connection.sourceKind)}</span>
						</div>
						<dl>
							<div>
								<dt>状态</dt>
								<dd>{statusLabel(connection.status)}</dd>
							</div>
						</dl>
					</article>
				{/each}
			</div>
		{/if}
	</div>
</section>
