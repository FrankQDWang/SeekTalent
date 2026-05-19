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
			<p class="section-label">Source settings</p>
			<h2>检索渠道</h2>
		</div>
		{#if connectionsQuery.isPending}
			<p class="muted">Loading source connections</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, 'Could not load source connections')}
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
							<dt>Status</dt>
							<dd>available</dd>
						</div>
						<div>
							<dt>Mode</dt>
							<dd>local index</dd>
						</div>
					</dl>
					<p>CTS is available as the local structured resume source.</p>
				</article>

				<article class="connection-card">
					<div>
						<strong>Liepin</strong>
						<span>Provider-rank-first card source with gated detail budget</span>
					</div>
					<dl>
						<div>
							<dt>Connections</dt>
							<dd>
								{(connectionsQuery.data?.connections ?? []).filter(
									(connection) => connection.sourceKind === 'liepin'
								).length}
							</dd>
						</div>
						<div>
							<dt>Boundary</dt>
							<dd>Pi-first</dd>
						</div>
					</dl>
					<p>
						Workbench shows connection readiness and approvals only. It will not request credentials
						or browser session material.
					</p>
					<a class="primary-action" href={resolve('/settings/sources/liepin')}>Manage Liepin</a>
				</article>
			</div>
		{/if}
	</div>
</section>
