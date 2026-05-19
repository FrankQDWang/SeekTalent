<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import type { components } from '$lib/api/schema';

	type WorkbenchSession = components['schemas']['WorkbenchSessionResponse'];

	let { sessions, loading, error } = $props<{
		sessions: WorkbenchSession[];
		loading: boolean;
		error: string;
	}>();

	let collapsed = $state(false);
	let query = $state('');

	const filtered = $derived.by(() => {
		const normalized = query.trim().toLowerCase();
		if (!normalized) {
			return sessions;
		}
		return sessions.filter((session: WorkbenchSession) =>
			[session.jobTitle, session.jdText, session.notes ?? ''].some((value) =>
				value.toLowerCase().includes(normalized)
			)
		);
	});
</script>

<aside class:collapsed class="session-rail" data-testid="session-rail">
	<div class:rail-head-collapsed={collapsed} class="rail-head">
		<a href={resolve('/sessions')} class="rail-logo" aria-label="Sessions">ST</a>
		<button
			class="icon-button"
			type="button"
			aria-label={collapsed ? 'Expand session rail' : 'Collapse session rail'}
			aria-expanded={!collapsed}
			aria-controls="session-rail-content"
			onclick={() => {
				collapsed = !collapsed;
			}}
		>
			{collapsed ? '>' : '<'}
		</button>
	</div>
	{#if !collapsed}
		<div id="session-rail-content" class="rail-content">
			<input
				class="rail-search"
				bind:value={query}
				placeholder="Search sessions"
				aria-label="Search sessions"
			/>
			<nav class="rail-list">
				{#if loading}
					<p class="rail-empty">Loading sessions</p>
				{:else if error}
					<p class="rail-empty" role="alert">Could not load sessions</p>
				{:else if filtered.length === 0}
					<p class="rail-empty">No sessions</p>
				{:else}
					{#each filtered as session (session.sessionId)}
						<a
							class:active={page.url.pathname === `/sessions/${session.sessionId}`}
							class="rail-item"
							href={resolve(`/sessions/${session.sessionId}`)}
						>
							<span>{session.jobTitle || 'Untitled role'}</span>
							<small>{session.status}</small>
						</a>
					{/each}
				{/if}
			</nav>
		</div>
	{/if}
</aside>

<style>
	.session-rail {
		grid-column: 1;
		grid-row: 2;
		display: grid;
		width: 232px;
		min-width: 0;
		grid-template-rows: auto minmax(0, 1fr);
		gap: 12px;
		border-right: 1px solid #dedbd0;
		background: #efede6;
		padding: 12px;
	}

	.session-rail.collapsed {
		width: 58px;
	}

	.rail-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}

	.rail-head-collapsed {
		align-items: center;
		flex-direction: column;
		justify-content: flex-start;
	}

	.rail-logo {
		display: grid;
		width: 34px;
		height: 34px;
		place-items: center;
		border-radius: 8px;
		background: #3c5a4a;
		color: #f8faf7;
		font-weight: 800;
	}

	.icon-button {
		width: 30px;
		height: 30px;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: #5e584f;
		font-size: 18px;
		font-weight: 700;
	}

	.icon-button:hover {
		background: #e4eadf;
	}

	.rail-content {
		display: grid;
		min-height: 0;
		grid-template-rows: auto minmax(0, 1fr);
		gap: 14px;
	}

	.rail-search {
		height: 36px;
		border: 1px solid #d4d0c5;
		border-radius: 6px;
		background: rgba(251, 250, 246, 0.76);
		padding: 0 10px;
	}

	.rail-list {
		display: grid;
		min-height: 0;
		align-content: start;
		gap: 7px;
		overflow: auto;
	}

	.rail-item {
		display: grid;
		min-height: 56px;
		gap: 4px;
		border: 1px solid transparent;
		border-radius: 6px;
		color: #5e584f;
		padding: 9px;
	}

	.rail-item:hover,
	.rail-item.active {
		background: rgba(251, 250, 246, 0.72);
		color: #25231f;
	}

	.rail-item span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 13px;
		font-weight: 800;
	}

	.rail-item small,
	.rail-empty {
		margin: 0;
		color: #7d7568;
		font-size: 12px;
	}

	@media (max-width: 760px) {
		.session-rail,
		.session-rail.collapsed {
			width: 100%;
		}
	}
</style>
