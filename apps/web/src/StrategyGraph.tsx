import { Background, Controls, Handle, Position, ReactFlow, type NodeProps } from '@xyflow/react';
import { useEffect, useMemo, useState } from 'react';

import type { RecruiterGraphNode } from './recruiterAnimation';
import type { RunStory } from './runStory';
import {
  fallbackLayout,
  layoutStrategyGraph,
  type LaidOutStrategyGraph,
  type StrategyFlowNode,
} from './strategyGraphLayout';

type StrategyGraphProps = {
  story: RunStory;
  selectedNodeId: string | null;
  onSelectNode: (node: RecruiterGraphNode) => void;
};

const graphBounds = { width: 980, height: 560 };
const nodeTypes = { strategy: StrategyGraphNode };

export function StrategyGraph({ story, selectedNodeId, onSelectNode }: StrategyGraphProps) {
  const fallbackGraph = useMemo(
    () => fallbackLayout(story.graphNodes, story.graphEdges, graphBounds),
    [story.graphEdges, story.graphNodes],
  );
  const [laidOutGraph, setLaidOutGraph] = useState<LaidOutStrategyGraph>(fallbackGraph);
  const nodes = useMemo(
    () =>
      laidOutGraph.nodes.map((node) => {
        const selected = node.id === selectedNodeId;
        return {
          ...node,
          selected,
          data: { ...node.data, selected },
        };
      }),
    [laidOutGraph.nodes, selectedNodeId],
  );

  useEffect(() => {
    let canceled = false;
    setLaidOutGraph(fallbackGraph);
    void layoutStrategyGraph(story.graphNodes, story.graphEdges, graphBounds).then((graph) => {
      if (!canceled) {
        setLaidOutGraph(graph);
      }
    });
    return () => {
      canceled = true;
    };
  }, [fallbackGraph, story.graphEdges, story.graphNodes]);

  return (
    <ReactFlow
      className="strategy-flow"
      data-testid="strategy-flow"
      nodes={nodes}
      edges={laidOutGraph.edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.45}
      maxZoom={1.6}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onSelectNode(node.data.graphNode)}
    >
      <Background gap={24} size={1} className="strategy-flow-bg" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function StrategyGraphNode({ data }: NodeProps<StrategyFlowNode>) {
  const node = data.graphNode;
  return (
    <div className="strategy-flow-node-shell">
      <Handle className="strategy-flow-handle" type="target" position={Position.Left} />
      <button
        className="strategy-flow-node"
        data-tone={node.tone}
        data-kind={node.kind}
        type="button"
        aria-pressed={data.selected}
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
