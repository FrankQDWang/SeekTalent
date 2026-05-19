<script lang="ts">
	import type { WorkbenchRequirementTriage } from '$lib/workbench/types';
	import type { WorkbenchRequirementTriageInput } from '$lib/workbench/recruiterAnimation';

	type Triage = WorkbenchRequirementTriage | WorkbenchRequirementTriageInput;

	let { triage, mode = 'confirmed' } = $props<{
		triage: Triage;
		mode?: 'confirmed' | 'runtime' | 'empty';
	}>();

	const chips = $derived(
		[
			...(triage.mustHaves ?? []).slice(0, 4),
			...(triage.niceToHaves ?? []).slice(0, 2),
			...(triage.generatedQueryHints ?? []).slice(0, 2)
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
