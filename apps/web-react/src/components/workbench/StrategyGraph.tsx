import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type FitViewOptions,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { layoutStrategyGraph } from "../../lib/strategy-graph/graphLayout";
import {
  projectStrategyGraph,
  type AgentStrategyGraph,
  type StrategyFlowEdge,
  type StrategyFlowNode,
} from "../../lib/strategy-graph/graphProjection";
import "./StrategyGraph.css";
import { StrategyGraphNode } from "./StrategyGraphNode";

type StrategyGraphProps = {
  graph: AgentStrategyGraph;
};

type ProjectedGraph = ReturnType<typeof projectStrategyGraph>;

type LayoutState =
  | { status: "empty" }
  | { status: "loading" }
  | { status: "ready"; projected: ProjectedGraph }
  | { status: "failed"; message: string };

const nodeTypes: NodeTypes = {
  strategy: StrategyGraphNode,
};

const fitViewOptions: FitViewOptions = {
  padding: 0.18,
};

export function StrategyGraph({ graph }: StrategyGraphProps) {
  const [layoutState, setLayoutState] = useState<LayoutState>(
    graph.nodes.length === 0 ? { status: "empty" } : { status: "loading" },
  );
  const [highlightedNodeId, setHighlightedNodeId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;

    if (graph.nodes.length === 0) {
      setLayoutState({ status: "empty" });
      setHighlightedNodeId(null);
      return () => {
        cancelled = true;
      };
    }

    setLayoutState({ status: "loading" });
    void layoutStrategyGraph(graph)
      .then((positions) => {
        if (!cancelled) {
          setLayoutState({
            status: "ready",
            projected: projectStrategyGraph(graph, positions),
          });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLayoutState({
            status: "failed",
            message:
              error instanceof Error
                ? error.message
                : "Strategy graph layout failed",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [graph]);

  const nodes = useMemo<StrategyFlowNode[]>(() => {
    if (layoutState.status !== "ready") {
      return [];
    }

    return layoutState.projected.nodes.map((node) => ({
      ...node,
      selected: node.id === highlightedNodeId,
    }));
  }, [highlightedNodeId, layoutState]);

  const edges =
    layoutState.status === "ready" ? layoutState.projected.edges : [];

  return (
    <section
      className="strategy-graph"
      aria-label="检索策略图"
      aria-busy={layoutState.status === "loading"}
    >
      {layoutState.status === "empty" ? (
        <div className="strategy-graph__empty">等待检索策略生成</div>
      ) : null}
      {layoutState.status === "failed" ? (
        <div className="strategy-graph__error" role="status">
          {layoutState.message}
        </div>
      ) : null}
      <ReactFlow<StrategyFlowNode, StrategyFlowEdge>
        ariaLabelConfig={{
          "node.a11yDescription.default":
            "按 Enter 选择检索策略节点，选择只会高亮节点，不会打开详情面板。",
        }}
        edges={edges}
        fitView
        fitViewOptions={fitViewOptions}
        maxZoom={1.45}
        minZoom={0.28}
        nodeTypes={nodeTypes}
        nodes={nodes}
        nodesConnectable={false}
        nodesDraggable={false}
        onNodeClick={(_, node) => setHighlightedNodeId(node.id)}
        onPaneClick={() => setHighlightedNodeId(null)}
        proOptions={{ hideAttribution: true }}
        selectNodesOnDrag={false}
      >
        <Background color="var(--st-border-strong)" gap={28} size={1} />
        <Controls
          aria-label="检索策略图控制"
          position="bottom-right"
          showInteractive={false}
        />
      </ReactFlow>
    </section>
  );
}
