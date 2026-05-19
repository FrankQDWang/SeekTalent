<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { ApiError, safeErrorMessage } from '$lib/api/errors';
	import { getMe, listSessions, logout } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';
	import { createWorkbenchEventStream } from '$lib/workbench/eventStream';
	import SessionRail from './SessionRail.svelte';
	import Topbar from './Topbar.svelte';

	let { children } = $props<{ children: import('svelte').Snippet }>();

	const queryClient = useQueryClient();
	const meQuery = createQuery(() => ({
		queryKey: workbenchKeys.me,
		queryFn: getMe,
		retry: false
	}));
	const sessionsQuery = createQuery(() => ({
		queryKey: workbenchKeys.sessions,
		queryFn: listSessions,
		enabled: Boolean(meQuery.data)
	}));
	const logoutMutation = createMutation(() => ({
		mutationFn: logout,
		onSuccess: async () => {
			queryClient.clear();
			await goto(resolve('/login'));
		}
	}));

	const displayName = $derived(meQuery.data?.user.displayName ?? 'User');
	const sessions = $derived(sessionsQuery.data?.sessions ?? []);
	const sessionError = $derived(
		sessionsQuery.error ? safeErrorMessage(sessionsQuery.error, 'Could not load sessions') : ''
	);
	const activeSessionId = $derived.by(() => {
		const match = page.url.pathname.match(/^\/sessions\/([^/?#]+)$/);
		return match?.[1] ? decodeURIComponent(match[1]) : null;
	});

	$effect(() => {
		const error = meQuery.error;
		if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
			void goto(resolve('/login'));
		}
	});

	$effect(() => {
		if (!meQuery.data) {
			return;
		}
		return createWorkbenchEventStream({
			queryClient,
			sessionId: activeSessionId
		});
	});
</script>

{#if meQuery.data}
	<main class="workbench-app">
		<Topbar
			{displayName}
			sessionCount={sessions.length}
			loggingOut={logoutMutation.isPending}
			onLogout={() => logoutMutation.mutate()}
		/>
		<SessionRail {sessions} loading={sessionsQuery.isPending} error={sessionError} />
		<section class="workbench-main">
			{@render children()}
		</section>
	</main>
{:else if meQuery.isPending}
	<main class="auth-check" aria-busy="true">
		<p>Loading workbench</p>
	</main>
{:else}
	<main class="auth-check" aria-busy="true">
		<p>Redirecting to login</p>
	</main>
{/if}

<style>
	.workbench-app {
		display: grid;
		height: 100vh;
		grid-template-columns: auto minmax(0, 1fr);
		grid-template-rows: 52px minmax(0, 1fr);
		background: #f6f5f1;
	}

	.workbench-main {
		grid-column: 2;
		grid-row: 2;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	.auth-check {
		display: grid;
		min-height: 100vh;
		place-items: center;
		background: #f6f5f1;
		color: #5e584f;
	}

	@media (max-width: 760px) {
		.workbench-app {
			height: auto;
			min-height: 100vh;
			grid-template-columns: minmax(0, 1fr);
			grid-template-rows: auto auto minmax(0, 1fr);
		}

		.workbench-main {
			grid-column: 1;
			grid-row: 3;
			overflow: visible;
		}
	}
</style>
