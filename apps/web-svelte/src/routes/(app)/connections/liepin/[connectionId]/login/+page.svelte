<script lang="ts">
	import { resolve } from '$app/paths';
	import { createQuery } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { getSourceConnection } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';

	let { data } = $props<{ data: { connectionId: string } }>();

	const connectionQuery = createQuery(() => ({
		queryKey: workbenchKeys.sourceConnection(data.connectionId),
		queryFn: () => getSourceConnection(data.connectionId)
	}));
</script>

<section class="connection-status-page">
	<div class="connection-status-panel">
		<div class="panel-head">
			<p class="section-label">Liepin connection status</p>
			<h2>Pi-first Liepin session</h2>
		</div>
		{#if connectionQuery.isPending}
			<p class="muted">Loading connection</p>
		{:else if connectionQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionQuery.error, 'Could not load connection')}
			</p>
		{:else if connectionQuery.data}
			<div class="connection-card compact">
				<div>
					<strong>{connectionQuery.data.label}</strong>
					<span>{connectionQuery.data.connectionId}</span>
				</div>
				<dl>
					<div>
						<dt>Status</dt>
						<dd>{connectionQuery.data.status}</dd>
					</div>
					<div>
						<dt>Mode</dt>
						<dd>Pi-first provider boundary</dd>
					</div>
					<div>
						<dt>Updated</dt>
						<dd>{connectionQuery.data.updatedAt}</dd>
					</div>
				</dl>
				{#if connectionQuery.data.warningMessage}
					<p>{connectionQuery.data.warningMessage}</p>
				{/if}
			</div>
			<div class="queue-empty compact-empty">
				<strong>Interactive login is not handled in this Workbench route.</strong>
				<span>
					Prepare the Liepin browser session outside the Svelte workbench. This page will not
					request credentials, render browser controls, or expose session material.
				</span>
			</div>
		{/if}
		<a class="secondary-link" href={resolve('/settings/sources/liepin')}>Back to Liepin settings</a>
	</div>
</section>
