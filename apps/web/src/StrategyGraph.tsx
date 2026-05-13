import { Background, Controls, Handle, Position, ReactFlow, type NodeChange, type NodeProps } from '@xyflow/react';
import { memo, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';

import type { RecruiterGraphNode } from './recruiterAnimation';
import type { RunStory } from './runStory';
import {
  fallbackLayout,
  layoutStrategyGraph,
  mergeManualNodePositions,
  type LaidOutStrategyGraph,
  type StrategyFlowNode,
} from './strategyGraphLayout';

type StrategyGraphProps = {
  story: RunStory;
  selectedNodeId: string | null;
  onSelectNode: (node: RecruiterGraphNode) => void;
};

const defaultGraphBounds = { width: 980, height: 560 };
const minGraphBounds = { width: 360, height: 420 };
const nodeTypes = { strategy: memo(StrategyGraphNode) };
type ManualPosition = { x: number; y: number };

export function StrategyGraph({ story, selectedNodeId, onSelectNode }: StrategyGraphProps) {
  const [shellRef, graphBounds] = useStrategyGraphBounds();
  const previousGraphKeyRef = useRef<string | null>(null);
  const laidOutNodeCountRef = useRef(0);
  const graphSignature = strategyGraphLayoutSignature(story);
  const layoutInputRef = useRef({
    signature: '',
    graphNodes: story.graphNodes,
    graphEdges: story.graphEdges,
  });
  if (layoutInputRef.current.signature !== graphSignature) {
    layoutInputRef.current = {
      signature: graphSignature,
      graphNodes: story.graphNodes,
      graphEdges: story.graphEdges,
    };
  }
  const layoutInput = layoutInputRef.current;
  const fallbackGraph = useMemo(
    () => fallbackLayout(layoutInput.graphNodes, layoutInput.graphEdges, graphBounds),
    [graphBounds, layoutInput],
  );
  const [laidOutGraph, setLaidOutGraph] = useState<LaidOutStrategyGraph>(fallbackGraph);
  const [manualPositions, setManualPositions] = useState<Map<string, ManualPosition>>(() => new Map());
  const manualPositionsRef = useRef<Map<string, ManualPosition>>(new Map());
  const graphKey = useMemo(() => activeStrategyGraphIdentity(story), [story.graphNodes]);
  const nodes = useMemo(
    () =>
      laidOutGraph.nodes.map((node) => {
        const selected = node.id === selectedNodeId;
        const manualPosition = manualPositions.get(node.id);
        return {
          ...node,
          position: manualPosition ?? node.position,
          draggable: true,
          selected,
          data: { ...node.data, selected, onSelectNode },
        };
      }),
    [laidOutGraph.nodes, manualPositions, onSelectNode, selectedNodeId],
  );
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setManualPositions((current) => {
      let changed = false;
      const next = new Map(current);
      for (const change of changes) {
        if (change.type === 'position' && change.position) {
          next.set(change.id, change.position);
          changed = true;
        }
        if (change.type === 'remove') {
          next.delete(change.id);
          changed = true;
        }
      }
      if (changed) {
        manualPositionsRef.current = next;
        return next;
      }
      return current;
    });
  }, []);

  useEffect(() => {
    let canceled = false;
    const nextGraphKey = graphKey;
    const previousGraphKey = previousGraphKeyRef.current ?? nextGraphKey;
    const applyGraph = (graph: LaidOutStrategyGraph) => {
      if (canceled) {
        return;
      }
      const merged = mergeManualNodePositions({
        current: new Map(graph.nodes.map((node) => [node.id, node.position])),
        manual: manualPositionsRef.current,
        currentGraphIdentity: previousGraphKey,
        nextGraphIdentity: nextGraphKey,
        nextNodeIds: graph.nodes.map((node) => node.id),
      });
      previousGraphKeyRef.current = nextGraphKey;
      manualPositionsRef.current = merged.manualPositions;
      setManualPositions(merged.manualPositions);
      setLaidOutGraph({
        ...graph,
        nodes: graph.nodes.map((node) => ({
          ...node,
          position: merged.positions.get(node.id) ?? node.position,
        })),
      });
      laidOutNodeCountRef.current = graph.nodes.length;
    };
    if (laidOutNodeCountRef.current === 0) {
      applyGraph(fallbackGraph);
    }
    void layoutStrategyGraph(layoutInput.graphNodes, layoutInput.graphEdges, graphBounds).then((graph) => {
      applyGraph(graph);
    });
    return () => {
      canceled = true;
    };
  }, [fallbackGraph, graphBounds, graphKey, layoutInput]);

  return (
    <div className="strategy-flow-shell" ref={shellRef}>
      <ReactFlow
        key={graphKey}
        className="strategy-flow"
        data-testid="strategy-flow"
        nodes={nodes}
        edges={laidOutGraph.edges}
        nodeTypes={nodeTypes}
        minZoom={0.2}
        maxZoom={1.6}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onSelectNode(node.data.graphNode)}
        onNodesChange={handleNodesChange}
      >
        <Background gap={24} size={1} className="strategy-flow-bg" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function useStrategyGraphBounds() {
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [bounds, setBounds] = useState(defaultGraphBounds);

  useEffect(() => {
    const element = shellRef.current;
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
            : defaultGraphBounds.height,
      };

      setBounds((currentBounds) =>
        currentBounds.width === nextBounds.width && currentBounds.height === nextBounds.height
          ? currentBounds
          : nextBounds,
      );
    };

    updateBounds();
    const observer = new ResizeObserver(updateBounds);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [shellRef, bounds] as const;
}

function activeStrategyGraphIdentity(story: RunStory): string {
  const jobNode = story.graphNodes.find((node) => node.detailPayload?.kind === 'job');
  if (jobNode?.detailPayload?.kind === 'job') {
    return `session:${jobNode.detailPayload.sessionId}`;
  }
  return `nodes:${story.graphNodes.map((node) => node.id).join('|')}`;
}

function strategyGraphLayoutSignature(story: RunStory): string {
  const nodes = story.graphNodes
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
        node.candidateEvidenceRefs?.map((ref) => `${ref.evidenceId}:${ref.evidenceLevel}`).join(',') ?? '',
      ].join('~'),
    )
    .join('|');
  const edges = story.graphEdges
    .map((edge) => [edge.from, edge.to, edge.tone, edge.label ?? ''].join('~'))
    .join('|');
  return `${nodes}::${edges}`;
}

function StrategyGraphNode({ data }: NodeProps<StrategyFlowNode>) {
  const node = data.graphNode;
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      data.onSelectNode?.(node);
    }
  };

  return (
    <div className="strategy-flow-node-shell">
      <Handle className="strategy-flow-handle" type="target" position={Position.Left} />
      <button
        className="strategy-flow-node"
        data-tone={node.tone}
        data-kind={node.kind}
        type="button"
        aria-pressed={data.selected}
        onKeyDown={handleKeyDown}
      >
        <span>
          {node.kind}
          {node.sourceLabel && node.sourceKind !== 'all' ? <em className="node-source-badge">{node.sourceLabel}</em> : null}
        </span>
        <strong>{node.label}</strong>
        <small>{node.detail}</small>
      </button>
      <Handle className="strategy-flow-handle" type="source" position={Position.Right} />
    </div>
  );
}
