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
		<a href={resolve('/sessions')} class="rail-logo" aria-label="会话">ST</a>
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
			{collapsed ? '›' : '‹'}
		</button>
	</div>
	{#if !collapsed}
		<div id="session-rail-content" class="rail-content">
			<input class="rail-search" bind:value={query} placeholder="搜索会话" aria-label="搜索会话" />
			<nav class="rail-list">
				{#if loading}
					<p class="rail-empty">正在加载会话</p>
				{:else if error}
					<p class="rail-empty" role="alert">会话加载失败</p>
				{:else if filtered.length === 0}
					<p class="rail-empty">暂无会话</p>
				{:else}
					{#each filtered as session (session.sessionId)}
						<a
							class:active={page.url.pathname === `/sessions/${session.sessionId}`}
							class="rail-item"
							href={resolve(`/sessions/${session.sessionId}`)}
						>
							<span>{session.jobTitle || 'Untitled role'}</span>
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
		border-right: 1px solid var(--line);
		background: var(--surface-muted);
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
		background: var(--accent);
		color: var(--accent-ink);
		font-weight: 800;
	}

	.icon-button {
		width: 30px;
		height: 30px;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--text-soft);
		font-size: 18px;
		font-weight: 700;
	}

	.icon-button:hover {
		background: var(--accent-soft);
	}

	.rail-content {
		display: grid;
		min-height: 0;
		grid-template-rows: auto minmax(0, 1fr);
		gap: 14px;
	}

	.rail-search {
		height: 36px;
		border: 1px solid transparent;
		border-radius: 6px;
		background: var(--surface-strong);
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
		display: flex;
		min-height: 46px;
		align-items: center;
		border: 1px solid transparent;
		border-radius: 6px;
		color: var(--text-soft);
		padding: 9px 10px;
	}

	.rail-item:hover,
	.rail-item.active {
		background: var(--surface-hover);
		color: var(--text);
	}

	.rail-item.active {
		background: var(--surface-selected);
	}

	.rail-item span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 13px;
		font-weight: 800;
	}

	.rail-empty {
		margin: 0;
		color: var(--text-muted);
		font-size: 12px;
	}

	@media (max-width: 760px) {
		.session-rail,
		.session-rail.collapsed {
			width: 100%;
		}
	}
</style>
