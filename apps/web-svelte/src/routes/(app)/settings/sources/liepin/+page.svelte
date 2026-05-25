<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		createMutation as createSvelteMutation,
		createQuery,
		useQueryClient
	} from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { createLiepinConnection, listSourceConnections } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';

	const queryClient = useQueryClient();
	let createError = $state('');

	const connectionsQuery = createQuery(() => ({
		queryKey: workbenchKeys.sourceConnections,
		queryFn: listSourceConnections
	}));
	const liepinConnections = $derived(
		(connectionsQuery.data?.connections ?? []).filter(
			(connection) => connection.sourceKind === 'liepin'
		)
	);
	const liepinCreateMutation = createSvelteMutation(() => ({
		mutationFn: createLiepinConnection,
		onSuccess: async (connection) => {
			createError = '';
			queryClient.setQueryData(workbenchKeys.sourceConnection(connection.connectionId), connection);
			await Promise.all([
				queryClient.invalidateQueries({ queryKey: workbenchKeys.sourceConnections }),
				queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions })
			]);
		},
		onError: (caught: unknown) => {
			createError = safeErrorMessage(caught, '猎聘连接创建失败');
		}
	}));

	function statusLabel(status: string) {
		const labels: Record<string, string> = {
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
			<p class="section-label">渠道设置</p>
			<h2>猎聘连接</h2>
		</div>
		<p class="muted">
			硬性条件过滤后，猎聘候选卡片仍按平台排序优先。详情打开由后端预算和审批共同控制。
		</p>
		{#if connectionsQuery.isPending}
			<p class="muted">正在加载猎聘连接</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, '猎聘连接加载失败')}
			</p>
		{:else if liepinConnections.length === 0}
			<div class="connection-empty">
				<strong>暂无猎聘连接</strong>
				<span>使用猎聘作为检索渠道前，请先创建一个受控连接。</span>
				<button
					class="primary-action"
					type="button"
					disabled={liepinCreateMutation.isPending}
					onclick={() => liepinCreateMutation.mutate()}
				>
					创建猎聘连接
				</button>
			</div>
		{:else}
			<div class="source-settings-list">
				{#each liepinConnections as connection (connection.connectionId)}
					<article
						class="connection-card"
						data-testid={`source-connection-${connection.connectionId}`}
					>
						<div>
							<strong>{connection.label}</strong>
							<span>{connection.connectionId}</span>
						</div>
						<dl>
							<div>
								<dt>状态</dt>
								<dd>{statusLabel(connection.status)}</dd>
							</div>
							<div>
								<dt>更新</dt>
								<dd>{connection.updatedAt}</dd>
							</div>
						</dl>
						{#if connection.warningMessage}
							<p>{connection.warningMessage}</p>
						{/if}
						<a
							class="primary-action"
							href={resolve(`/connections/liepin/${connection.connectionId}/login`)}
						>
							查看连接状态
						</a>
					</article>
				{/each}
			</div>
		{/if}
		{#if createError}
			<p class="form-error" role="alert">{createError}</p>
		{/if}
	</div>
</section>
