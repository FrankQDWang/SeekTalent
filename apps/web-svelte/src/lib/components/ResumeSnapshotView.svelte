<script lang="ts">
	import type { WorkbenchGraphCandidateResumeSnapshot } from '$lib/workbench/types';
	import OriginalResumeView from './OriginalResumeView.svelte';

	let {
		graphCandidateId,
		snapshot,
		loading = false,
		error = null
	} = $props<{
		graphCandidateId: string;
		snapshot?: WorkbenchGraphCandidateResumeSnapshot | null;
		loading?: boolean;
		error?: string | null;
	}>();

	function snapshotStatusLabel(status: WorkbenchGraphCandidateResumeSnapshot['status']) {
		if (status === 'snapshot_forbidden') return '原始简历受限';
		if (status === 'snapshot_not_found') return '未找到原始简历';
		return '原始简历不可用';
	}
</script>

{#if loading}
	<p class="muted">正在读取原始简历...</p>
{:else if error}
	<p class="form-error" role="alert">{error}</p>
{:else if snapshot}
	{#if snapshot.status !== 'ready'}
		<div class="resume-snapshot">
			<strong>{snapshotStatusLabel(snapshot.status)}</strong>
			<p>{snapshot.reason ?? '该原始简历暂时不可展示。'}</p>
		</div>
	{:else}
		<div data-testid={`resume-snapshot-${graphCandidateId}`}>
			<OriginalResumeView originalResume={snapshot.originalResume ?? null} />
		</div>
	{/if}
{/if}
