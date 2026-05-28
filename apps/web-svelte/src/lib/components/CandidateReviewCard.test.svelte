<script lang="ts">
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import { createQueryClient } from '$lib/query/client';
	import type { FinalCandidateViewModel } from '$lib/workbench/finalCandidateCards';
	import CandidateReviewCard from './CandidateReviewCard.svelte';

	let { sessionId, card }: { sessionId: string; card: FinalCandidateViewModel } = $props();
	const queryClient = createQueryClient();
</script>

<QueryClientProvider client={queryClient}>
	<CandidateReviewCard {sessionId} {card} />
</QueryClientProvider>
