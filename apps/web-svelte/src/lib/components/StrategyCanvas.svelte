<script lang="ts">
	import ReadyStatePanel from './ReadyStatePanel.svelte';
	import StrategyGraph from './StrategyGraph.svelte';
	import { sourceLabel } from '$lib/workbench/sourceDisplay';
	import type { RecruiterGraphNode, SourceKind } from '$lib/workbench/recruiterAnimation';
	import type { RuntimeGraphStory } from '$lib/workbench/runtimeGraphView';

	let {
		loading,
		error,
		sourceKinds,
		canStart,
		starting,
		startLabel,
		startDescription,
		startError,
		story,
		selectedNodeId,
		onStart,
		onSelectNode
	} = $props<{
		loading: boolean;
		error: boolean;
		sourceKinds: SourceKind[];
		canStart: boolean;
		starting: boolean;
		startLabel: string;
		startDescription: string;
		startError: string | null;
		story: RuntimeGraphStory;
		selectedNodeId: string | null;
		onStart: () => void;
		onSelectNode: (node: RecruiterGraphNode) => void;
	}>();

	const nodes = $derived(story.graphNodes);
	const activeLaneKinds = $derived(
		sourceKinds.filter((sourceKind: SourceKind) =>
			nodes.some((node: RecruiterGraphNode) => node.lane === sourceKind)
		)
	);
	const hasRuntimeRoundRows = $derived(
		nodes.some((node: RecruiterGraphNode) => /^round-\d+-/.test(node.id))
	);
</script>

<div class="canvas-toolbar">
	<div>
		<span class="section-label">检索策略图</span>
	</div>
</div>
{#if loading}
	<div class="canvas-ready compact">正在加载时间线</div>
{:else if error}
	<div class="canvas-ready compact" role="alert">时间线加载失败</div>
{:else if nodes.length === 0}
	<ReadyStatePanel {canStart} {starting} {startLabel} {startDescription} {startError} {onStart} />
{:else}
	<div class="strategy-canvas" data-testid="strategy-canvas">
		<div class="graph-grid" aria-hidden="true"></div>
		{#if activeLaneKinds.length > 1 && !hasRuntimeRoundRows}
			<div class="source-lane-bands" aria-hidden="true">
				{#each activeLaneKinds as sourceKind (sourceKind)}
					<div
						class={`source-lane-band ${sourceKind}`}
						style={`--lane-y: ${sourceKind === 'cts' ? '30%' : '70%'}`}
					>
						<span>{sourceLabel(sourceKind)}</span>
					</div>
				{/each}
			</div>
		{/if}
		<StrategyGraph {story} {selectedNodeId} {onSelectNode} />
		{#if canStart || starting || startError}
			<div class="canvas-start-overlay">
				<button
					class="central-start"
					type="button"
					disabled={!canStart || starting}
					onclick={onStart}
				>
					{starting ? '处理中' : startLabel}
				</button>
				{#if startError}
					<p class="form-error" role="alert">{startError}</p>
				{/if}
			</div>
		{/if}
		{#if story.completionText}
			<div class="completion-toast">{story.completionText}</div>
		{/if}
	</div>
{/if}
