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
			<p class="section-label">Settings</p>
			<h2>Source settings</h2>
		</div>
		<p class="muted">Manage recruiter search sources and connection readiness.</p>
		<nav class="settings-nav" aria-label="Settings sections">
			<a class="primary-action" href={resolve('/settings/sources')}>Open source settings</a>
		</nav>
		{#if connectionsQuery.isPending}
			<p class="muted">Loading source connections</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, 'Could not load source settings')}
			</p>
		{:else}
			<div class="source-settings-list">
				<article class="connection-card compact">
					<div>
						<strong>CTS</strong>
						<span>Local structured resume source</span>
					</div>
					<dl>
						<div>
							<dt>Status</dt>
							<dd>available</dd>
						</div>
					</dl>
				</article>
				{#each connectionsQuery.data?.connections ?? [] as connection (connection.connectionId)}
					<article class="connection-card compact">
						<div>
							<strong>{connection.label}</strong>
							<span>{connection.sourceKind}</span>
						</div>
						<dl>
							<div>
								<dt>Status</dt>
								<dd>{connection.status}</dd>
							</div>
						</dl>
					</article>
				{/each}
			</div>
		{/if}
	</div>
</section>
