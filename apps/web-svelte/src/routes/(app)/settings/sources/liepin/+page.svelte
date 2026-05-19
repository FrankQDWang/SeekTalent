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
			createError = safeErrorMessage(caught, 'Could not create Liepin connection');
		}
	}));
</script>

<section class="settings-page">
	<div class="panel settings-panel">
		<div class="panel-head">
			<p class="section-label">Source settings</p>
			<h2>Liepin connection</h2>
		</div>
		<p class="muted">
			Liepin card ordering remains provider-rank-first after hard filters. Detail opening is
			approval and lease gated by the backend budget.
		</p>
		{#if connectionsQuery.isPending}
			<p class="muted">Loading Liepin connection</p>
		{:else if connectionsQuery.error}
			<p class="form-error" role="alert">
				{safeErrorMessage(connectionsQuery.error, 'Could not load Liepin connection')}
			</p>
		{:else if liepinConnections.length === 0}
			<div class="connection-empty">
				<strong>No Liepin connection</strong>
				<span>Create a scoped connection before using Liepin as a session source.</span>
				<button
					class="primary-action"
					type="button"
					disabled={liepinCreateMutation.isPending}
					onclick={() => liepinCreateMutation.mutate()}
				>
					Create Liepin connection
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
								<dt>Status</dt>
								<dd>{connection.status}</dd>
							</div>
							<div>
								<dt>Updated</dt>
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
							Connection status
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
