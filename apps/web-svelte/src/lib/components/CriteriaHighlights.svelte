<script lang="ts">
	import type { QueryTermCandidate, RequirementSheet } from '$lib/workbench/types';

	let { requirementSheet, mode = 'confirmed' } = $props<{
		requirementSheet: RequirementSheet | null;
		mode?: 'confirmed' | 'runtime' | 'empty';
	}>();

	const chips = $derived(
		[
			...(requirementSheet?.must_have_capabilities ?? []).slice(0, 4),
			...(requirementSheet?.preferred_capabilities ?? []).slice(0, 2),
			...(requirementSheet?.initial_query_term_pool ?? [])
				.map((item: QueryTermCandidate) => item.term)
				.slice(0, 2)
		]
			.map((item) => item.trim())
			.filter(Boolean)
			.filter((item, index, values) => values.indexOf(item) === index)
			.slice(0, 6)
	);
</script>

{#if chips.length > 0}
	<div class="bonus-tags" aria-label="Extracted search criteria">
		<strong class="criteria-origin">{mode === 'runtime' ? '后台提取' : '已确认标准'}</strong>
		{#each chips as item (item)}
			<span>{item}</span>
		{/each}
	</div>
{/if}
