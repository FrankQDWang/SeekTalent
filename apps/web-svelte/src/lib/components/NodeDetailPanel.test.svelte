<script lang="ts">
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import { createQueryClient } from '$lib/query/client';
	import type { WorkbenchGraphCandidateListResponse } from '$lib/workbench/types';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import NodeDetailPanel from './NodeDetailPanel.svelte';

	let {
		sessionId = 'session-1',
		node,
		graphCandidatePage = null,
		graphCandidatesLoading = false,
		graphCandidatesError = null
	}: {
		sessionId?: string;
		node: RecruiterGraphNode | null;
		graphCandidatePage?: WorkbenchGraphCandidateListResponse | null;
		graphCandidatesLoading?: boolean;
		graphCandidatesError?: string | null;
	} = $props();

	const queryClient = createQueryClient();
</script>

<QueryClientProvider client={queryClient}>
	<NodeDetailPanel
		{sessionId}
		{node}
		{graphCandidatePage}
		{graphCandidatesLoading}
		{graphCandidatesError}
	/>
</QueryClientProvider>
