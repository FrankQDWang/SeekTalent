<script lang="ts">
	import type { WorkbenchGraphCandidateResumeSnapshot } from '$lib/workbench/types';

	type OriginalResume = NonNullable<WorkbenchGraphCandidateResumeSnapshot['originalResume']>;

	let expanded = $state(false);
	let { originalResume = null } = $props<{
		originalResume?: OriginalResume | null;
	}>();

	const sections = $derived(originalResume?.sections ?? []);
	const sourceUrl = $derived(originalResume?.sourceUrl?.trim() ?? '');
</script>

{#if originalResume && sections.length > 0}
	<div class="resume-snapshot original-resume" data-testid="original-resume">
		{#if sourceUrl}
			<div class="original-resume-source">
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- external provider URL opens in a new tab -->
				<a class="original-resume-source-url" href={sourceUrl} target="_blank" rel="noreferrer">
					{sourceUrl}
				</a>
			</div>
		{/if}
		<div class:expanded class="original-resume-viewport">
			{#each sections as section, sectionIndex (sectionIndex)}
				<section class="resume-snapshot-section">
					<span>{section.title}</span>
					{#each section.items ?? [] as item, itemIndex (itemIndex)}
						<div class="original-resume-item">
							<strong>{item.title}</strong>
							<div class="original-resume-fields">
								{#each item.fields ?? [] as field, fieldIndex (fieldIndex)}
									<div class="original-resume-field">
										<span>{field.label}</span>
										<p>{field.value}</p>
									</div>
								{/each}
							</div>
						</div>
					{/each}
				</section>
			{/each}
			{#if !expanded}
				<div class="original-resume-fade" aria-hidden="true"></div>
			{/if}
		</div>
		<button
			class="original-resume-toggle"
			type="button"
			aria-expanded={expanded}
			onclick={() => {
				expanded = !expanded;
			}}
		>
			{expanded ? '收起' : '展开完整简历'}
		</button>
	</div>
{:else}
	<div class="resume-snapshot">
		<strong>原始简历不可用</strong>
		<p>当前候选人没有可展示的原始来源简历。</p>
	</div>
{/if}

<style>
	.original-resume {
		position: relative;
		padding: 0;
		overflow: hidden;
	}

	.original-resume-source {
		padding: 11px 11px 0;
	}

	.original-resume-source-url {
		color: var(--accent);
		font-size: 12px;
		font-weight: 700;
		line-height: 1.45;
		overflow-wrap: anywhere;
		text-decoration: none;
	}

	.original-resume-source-url:hover {
		text-decoration: underline;
	}

	.original-resume-viewport {
		position: relative;
		display: grid;
		gap: 10px;
		max-height: 320px;
		padding: 11px;
		overflow: hidden;
	}

	.original-resume-viewport.expanded {
		max-height: none;
		padding-bottom: 52px;
	}

	.original-resume-fade {
		position: absolute;
		right: 0;
		bottom: 0;
		left: 0;
		height: 88px;
		background: linear-gradient(180deg, rgb(255 255 255 / 0%), var(--surface) 78%);
		pointer-events: none;
	}

	.original-resume-toggle {
		position: absolute;
		right: 10px;
		bottom: 10px;
		z-index: 1;
		border: 0;
		border-radius: 6px;
		background: var(--surface-strong);
		color: var(--text);
		font-size: 12px;
		font-weight: 800;
		padding: 7px 10px;
		box-shadow: 0 8px 20px rgb(15 23 42 / 12%);
		cursor: pointer;
	}

	.original-resume-toggle:hover {
		color: var(--accent);
	}

	.original-resume-item {
		display: grid;
		gap: 6px;
	}

	.original-resume-fields {
		display: grid;
		gap: 8px;
	}

	.original-resume-field {
		display: grid;
		gap: 3px;
		padding: 8px 0;
	}

	.original-resume-field span {
		color: var(--text-muted);
		font-size: 12px;
		font-weight: 700;
	}

	.original-resume-field p {
		margin: 0;
		color: var(--text);
		font-size: 13px;
		line-height: 1.6;
		white-space: pre-wrap;
	}
</style>
