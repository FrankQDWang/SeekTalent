<script lang="ts">
	import type { RecruiterLogEntry } from '$lib/workbench/recruiterAnimation';
	import type { RuntimeGraphStory } from '$lib/workbench/runtimeGraphView';

	type ActivityLogEntry = RecruiterLogEntry & {
		noteKind?: string;
		statusHint?: string;
	};

	let {
		loading,
		error,
		pendingNote = null,
		story
	} = $props<{
		loading: boolean;
		error: boolean;
		pendingNote?: string | null;
		story: RuntimeGraphStory;
	}>();

	const businessEvents = $derived.by(() => {
		if (!pendingNote) {
			return story.logEntries;
		}
		if (story.logEntries.some((entry: RecruiterLogEntry) => entry.text === pendingNote)) {
			return story.logEntries;
		}
		const pendingEntry: ActivityLogEntry = {
			id: 'pending-running-note',
			at: Number.MAX_SAFE_INTEGER,
			tag: 'SYS',
			text: pendingNote,
			sourceKind: 'all',
			sourceLabel: 'All sources',
			lane: 'shared',
			noteKind: 'waiting',
			statusHint: 'waiting'
		};
		return [...story.logEntries, pendingEntry];
	});
</script>

<div class="right-log">
	<div class="right-section-head">
		<p class="section-label">运行笔记</p>
	</div>
	{#if loading}
		<p class="muted">正在加载时间线</p>
	{:else if error}
		<p class="form-error" role="alert">时间线加载失败</p>
	{:else if businessEvents.length === 0}
		<div class="timeline-empty">等待 Agent 生成业务笔记。</div>
	{:else}
		<ol
			class="log-stream"
			aria-label="运行笔记流"
			aria-live="polite"
			aria-relevant="additions text"
		>
			{#each businessEvents as event, index (event.id)}
				<li class={`log-line log-${event.tag.toLowerCase()}`}>
					<span class="log-line-dot" aria-hidden="true"></span>
					<p class="log-line-text">
						{#if event.sourceLabel && event.sourceKind !== 'all'}
							<em class="log-source-badge">{event.sourceLabel}</em>
						{/if}
						<span
							class:is-latest={index === businessEvents.length - 1}
							class:is-waiting={index === businessEvents.length - 1 &&
								(event.noteKind === 'waiting' || event.statusHint === 'waiting')}
							class="streaming-note-text"
							data-testid={index === businessEvents.length - 1
								? 'latest-streaming-note'
								: undefined}
						>
							<span class="streaming-note-base">{event.text}</span>
							<span class="streaming-note-fill" aria-hidden="true">{event.text}</span>
						</span>
					</p>
				</li>
			{/each}
		</ol>
	{/if}
</div>
