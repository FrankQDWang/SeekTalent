<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { useQueryClient } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { createSession } from '$lib/api/workbench';
	import CreateSessionForm from '$lib/components/CreateSessionForm.svelte';
	import ReadyStatePanel from '$lib/components/ReadyStatePanel.svelte';
	import { workbenchKeys } from '$lib/query/keys';
	import type { WorkbenchSessionCreateInput } from '$lib/workbench/types';

	let creating = $state(false);
	let createError = $state<string | null>(null);
	const queryClient = useQueryClient();

	async function handleCreate(input: WorkbenchSessionCreateInput) {
		creating = true;
		createError = null;
		try {
			const session = await createSession(input);
			queryClient.setQueryData(workbenchKeys.session(session.sessionId), session);
			await queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions });
			await goto(resolve(`/sessions/${session.sessionId}`));
		} catch (error) {
			createError = safeErrorMessage(error, '会话创建失败');
		} finally {
			creating = false;
		}
	}
</script>

<div class="reference-grid empty-session">
	<section class="jd-panel create-panel">
		<CreateSessionForm {creating} error={createError} onCreate={handleCreate} />
	</section>
	<section class="strategy-panel">
		<ReadyStatePanel />
	</section>
	<section class="right-rail">
		<div class="right-log">
			<p class="section-label">岗位简报</p>
			<div class="timeline-empty">Create a JD session to initialize the agent console.</div>
		</div>
		<div class="queue-panel">
			<div class="queue-heading">
				<span>节点详情</span>
				<strong>-</strong>
			</div>
			<div class="queue-empty">
				<strong>未选择节点</strong>
				<span>Create a session first.</span>
			</div>
		</div>
	</section>
</div>
