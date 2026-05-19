<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';

	import type { StrategyFlowNode } from '$lib/workbench/strategyGraphLayout';

	let { data }: NodeProps<StrategyFlowNode> = $props();

	const graphNode = $derived(data.graphNode);

	function selectNode() {
		data.onSelectNode?.(graphNode);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter' && event.key !== ' ') {
			return;
		}
		event.preventDefault();
		selectNode();
	}
</script>

<div class="strategy-flow-node-shell">
	<Handle class="strategy-flow-handle" type="target" position={Position.Left} />
	<div
		class="strategy-flow-node"
		data-testid={`strategy-node-${graphNode.id}`}
		data-tone={graphNode.tone}
		data-kind={graphNode.kind}
		role="button"
		tabindex="0"
		aria-pressed={data.selected}
		onclick={selectNode}
		onkeydown={handleKeydown}
	>
		<span class="node-meta">
			{graphNode.kind}
			{#if graphNode.sourceLabel && graphNode.sourceKind !== 'all'}
				<em>{graphNode.sourceLabel}</em>
			{/if}
		</span>
		<strong>{graphNode.label}</strong>
		<small>{graphNode.detail}</small>
	</div>
	<Handle class="strategy-flow-handle" type="source" position={Position.Right} />
</div>
