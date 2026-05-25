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
</script>

<section class="settings-page">
	<div class="panel settings-panel">
		<div class="panel-head">
			<p class="section-label">渠道设置</p>
			<h2>检索渠道</h2>
		</div>
		{#if connectionsQuery.isPending}
			<p class="muted">正在加载渠道连接</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, '渠道连接加载失败')}
			</p>
		{:else}
			<div class="source-settings-list">
				<article class="connection-card">
					<div>
						<strong>CTS</strong>
						<span>结构化简历库</span>
					</div>
					<dl>
						<div>
							<dt>状态</dt>
							<dd>可用</dd>
						</div>
						<div>
							<dt>模式</dt>
							<dd>本地索引</dd>
						</div>
					</dl>
					<p>CTS 作为本地结构化简历库可直接使用。</p>
				</article>

				<article class="connection-card">
					<div>
						<strong>猎聘</strong>
						<span>按平台排序读取卡片，详情打开受预算和审批保护</span>
					</div>
					<dl>
						<div>
							<dt>连接</dt>
							<dd>
								{(connectionsQuery.data?.connections ?? []).filter(
									(connection) => connection.sourceKind === 'liepin'
								).length}
							</dd>
						</div>
						<div>
							<dt>边界</dt>
							<dd>平台排序优先</dd>
						</div>
					</dl>
					<p>工作台只显示连接状态和审批结果，不会要求录入账号密码或浏览器会话材料。</p>
					<a class="primary-action" href={resolve('/settings/sources/liepin')}>管理猎聘</a>
				</article>
			</div>
		{/if}
	</div>
</section>
