<script lang="ts">
	import {
		Background,
		Controls,
		SvelteFlow,
		type NodeEventWithPointer,
		type NodeTargetEventWithPointer,
		type NodeTypes
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { untrack } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';

	import StrategyGraphNode from './StrategyGraphNode.svelte';
	import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
	import type { RunStory } from '$lib/workbench/runStory';
	import {
		fallbackLayout,
		layoutStrategyGraph,
		mergeManualNodePositions,
		type LaidOutStrategyGraph,
		type StrategyFlowNode,
		type StrategyFlowEdge
	} from '$lib/workbench/strategyGraphLayout';

	type GraphBounds = { width: number; height: number };
	type GraphPosition = { x: number; y: number };

	type StrategyGraphProps = {
		story: RunStory;
		selectedNodeId: string | null;
		onSelectNode: (node: RecruiterGraphNode) => void;
	};

	const defaultGraphBounds: GraphBounds = { width: 980, height: 560 };
	const minGraphBounds: GraphBounds = { width: 360, height: 420 };
	const nodeTypes = { strategy: StrategyGraphNode } satisfies NodeTypes;

	let { story, selectedNodeId, onSelectNode }: StrategyGraphProps = $props();
	let shellElement = $state<HTMLDivElement | null>(null);
	let graphBounds = $state<GraphBounds>(defaultGraphBounds);
	let laidOutGraph = $state.raw<LaidOutStrategyGraph>(fallbackLayout([], [], defaultGraphBounds));
	let flowNodes = $state.raw<StrategyFlowNode[]>([]);
	let flowEdges = $state.raw<StrategyFlowEdge[]>([]);
	let manualPositions = $state.raw(new SvelteMap<string, GraphPosition>());
	let previousGraphKey: string | null = null;
	let layoutRunId = 0;

	const graphKey = $derived(activeStrategyGraphIdentity(story));
	const graphSignature = $derived(strategyGraphLayoutSignature(story));

	$effect(() => {
		const element = shellElement;
		if (!element) {
			return;
		}

		const updateBounds = () => {
			const rect = element.getBoundingClientRect();
			const measuredWidth = rect.width || element.offsetWidth;
			const measuredHeight = rect.height || element.offsetHeight;
			const nextBounds = {
				width:
					measuredWidth > 0
						? Math.max(minGraphBounds.width, Math.round(measuredWidth))
						: defaultGraphBounds.width,
				height:
					measuredHeight > 0
						? Math.max(minGraphBounds.height, Math.round(measuredHeight))
						: defaultGraphBounds.height
			};

			if (graphBounds.width !== nextBounds.width || graphBounds.height !== nextBounds.height) {
				graphBounds = nextBounds;
			}
		};

		updateBounds();
		const observer = new ResizeObserver(updateBounds);
		observer.observe(element);
		return () => observer.disconnect();
	});

	$effect(() => {
		const signature = graphSignature;
		const nextGraphKey = graphKey;
		const bounds = graphBounds;
		const graphNodes = story.graphNodes;
		const graphEdges = story.graphEdges;
		const runId = (layoutRunId += 1);

		void signature;
		applyGraph(fallbackLayout(graphNodes, graphEdges, bounds), nextGraphKey);
		void layoutStrategyGraph(graphNodes, graphEdges, bounds).then((graph) => {
			if (runId === layoutRunId) {
				applyGraph(graph, nextGraphKey);
			}
		});
	});

	$effect(() => {
		const graph = laidOutGraph;
		const selected = selectedNodeId;
		const manualSignature = [...manualPositions.entries()]
			.map(([id, position]) => `${id}:${position.x},${position.y}`)
			.join('|');

		void selected;
		void manualSignature;
		flowNodes = projectFlowNodes(graph);
		flowEdges = graph.edges;
	});

	const handleNodeClick: NodeEventWithPointer<MouseEvent | TouchEvent, StrategyFlowNode> = ({
		node
	}) => {
		onSelectNode(node.data.graphNode);
	};

	const handleNodeDragStop: NodeTargetEventWithPointer<
		MouseEvent | TouchEvent,
		StrategyFlowNode
	> = ({ targetNode, nodes }) => {
		const draggedNodes = targetNode ? [targetNode] : nodes;
		if (draggedNodes.length === 0) {
			return;
		}

		const next = new SvelteMap(manualPositions);
		for (const node of draggedNodes) {
			next.set(node.id, node.position);
		}
		manualPositions = next;
	};

	function applyGraph(graph: LaidOutStrategyGraph, nextGraphKey: string) {
		const previousGraphIdentity = previousGraphKey ?? nextGraphKey;
		const merged = mergeManualNodePositions({
			current: new Map(graph.nodes.map((node) => [node.id, node.position])),
			manual: untrack(() => new Map(manualPositions)),
			currentGraphIdentity: previousGraphIdentity,
			nextGraphIdentity: nextGraphKey,
			nextNodeIds: graph.nodes.map((node) => node.id)
		});
		previousGraphKey = nextGraphKey;
		manualPositions = new SvelteMap(merged.manualPositions);
		laidOutGraph = {
			...graph,
			nodes: graph.nodes.map((node) => ({
				...node,
				position: merged.positions.get(node.id) ?? node.position
			}))
		};
	}

	function projectFlowNodes(graph: LaidOutStrategyGraph): StrategyFlowNode[] {
		return graph.nodes.map((node) => {
			const selected = node.id === selectedNodeId;
			const manualPosition = manualPositions.get(node.id);
			return {
				...node,
				position: manualPosition ?? node.position,
				draggable: true,
				selected,
				data: { ...node.data, selected, onSelectNode }
			};
		});
	}

	function activeStrategyGraphIdentity(currentStory: RunStory): string {
		const jobNode = currentStory.graphNodes.find((node) => node.detailPayload?.kind === 'job');
		if (jobNode?.detailPayload?.kind === 'job') {
			return `session:${jobNode.detailPayload.sessionId}`;
		}
		return `nodes:${currentStory.graphNodes.map((node) => node.id).join('|')}`;
	}

	function strategyGraphLayoutSignature(currentStory: RunStory): string {
		const nodes = currentStory.graphNodes
			.map((node) =>
				[
					node.id,
					node.kind,
					node.label,
					node.detail,
					node.tone,
					node.sourceKind ?? '',
					node.lane ?? '',
					node.candidateReviewItemIds?.join(',') ?? '',
					node.candidateEvidenceRefs
						?.map((ref) => `${ref.evidenceId}:${ref.evidenceLevel}`)
						.join(',') ?? ''
				].join('~')
			)
			.join('|');
		const edges = currentStory.graphEdges
			.map((edge) => [edge.from, edge.to, edge.tone, edge.label ?? ''].join('~'))
			.join('|');
		return `${nodes}::${edges}`;
	}
</script>

<div
	class="strategy-flow-shell"
	bind:this={shellElement}
	style={`--strategy-content-width: ${laidOutGraph.contentWidth ?? defaultGraphBounds.width}px; --strategy-content-height: ${laidOutGraph.contentHeight ?? defaultGraphBounds.height}px;`}
>
	{#if story.graphNodes.length === 0}
		<div class="strategy-flow-empty" data-testid="strategy-flow-empty">
			<strong>暂无策略图</strong>
			<span>会话启动后会展示检索策略和候选人流转。</span>
		</div>
	{:else}
		<SvelteFlow
			id={`strategy-flow-${graphKey}`}
			class="strategy-flow"
			data-testid="strategy-flow"
			bind:nodes={flowNodes}
			bind:edges={flowEdges}
			{nodeTypes}
			minZoom={0.2}
			maxZoom={1.6}
			nodesDraggable={true}
			nodesConnectable={false}
			elementsSelectable={true}
			nodesFocusable={true}
			proOptions={{ hideAttribution: true }}
			onnodeclick={handleNodeClick}
			onnodedragstop={handleNodeDragStop}
		>
			<Background gap={24} size={1} class="strategy-flow-bg" />
			<Controls showLock={false} />
		</SvelteFlow>
	{/if}
</div>

<style>
	.strategy-flow-shell {
		overflow: auto;
	}

	.strategy-flow-shell :global(.strategy-flow) {
		min-width: var(--strategy-content-width);
		min-height: var(--strategy-content-height);
	}
</style>
