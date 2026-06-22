import { BriefcaseBusiness } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  projectStrategyTimelineGraph,
  type AgentStrategyGraph,
} from "../../lib/strategy-graph/graphProjection";
import "./StrategyGraph.css";
import { StrategyGraphNode } from "./StrategyGraphNode";

type StrategyGraphProps = {
  graph: AgentStrategyGraph;
  jobTitle?: string | null;
};

export function StrategyGraph({ graph, jobTitle = null }: StrategyGraphProps) {
  const projection = projectStrategyTimelineGraph(graph);
  const rootCard = jobTitle ? jobRootCard(jobTitle, graph) : null;
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const canvasWidth = Math.max(
    projection.width,
    rootCard ? rootCard.x + rootCard.width + 80 : 0,
  );
  const canvasHeight = Math.max(
    projection.height,
    rootCard ? rootCard.y + rootCard.height + 80 : 0,
  );
  const canvasScale =
    viewportWidth > 0 ? Math.min(1, viewportWidth / canvasWidth) : 1;
  const scaledCanvasHeight = Math.ceil(canvasHeight * canvasScale);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return undefined;
    }

    const updateWidth = () => setViewportWidth(viewport.clientWidth);
    updateWidth();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateWidth);
      return () => window.removeEventListener("resize", updateWidth);
    }

    const observer = new ResizeObserver(updateWidth);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  return (
    <section className="strategy-graph" aria-label="检索策略图">
      {projection.nodes.length === 0 && rootCard === null ? (
        <div className="strategy-graph__empty">等待检索策略生成</div>
      ) : (
        <>
          <div className="strategy-graph__timeline" aria-hidden="true">
            <div className="strategy-graph__track">
              <span
                className="strategy-graph__progress"
                style={{ width: `${String(projection.progressPercent)}%` }}
              />
              {projection.rounds.map((round) => (
                <span
                  className="strategy-graph__tick"
                  data-state={round.state}
                  key={round.roundNo}
                  style={{ left: `${String(round.x)}%` }}
                >
                  {round.label}
                </span>
              ))}
            </div>
            <span className="strategy-graph__status">
              {projection.activeLabel}
            </span>
          </div>
          <div
            aria-label="检索策略图画布"
            className="strategy-graph__viewport"
            ref={viewportRef}
            tabIndex={0}
          >
            <div
              className="strategy-graph__canvas-frame"
              style={{
                height: scaledCanvasHeight,
              }}
            >
              <div
                className="strategy-graph__canvas"
                style={{
                  height: canvasHeight,
                  transform: `scale(${String(canvasScale)})`,
                  width: canvasWidth,
                }}
              >
                <svg
                  aria-hidden="true"
                  className="strategy-graph__edges"
                  height={canvasHeight}
                  viewBox={[
                    "0",
                    "0",
                    String(canvasWidth),
                    String(canvasHeight),
                  ].join(" ")}
                  width={canvasWidth}
                >
                  <defs>
                    <marker
                      id="strategy-graph-arrow"
                      markerHeight="8"
                      markerWidth="8"
                      orient="auto"
                      refX="7"
                      refY="4"
                      viewBox="0 0 8 8"
                    >
                      <path d="M 0 0 L 8 4 L 0 8 z" />
                    </marker>
                  </defs>
                  {rootCard && projection.nodes[0] ? (
                    <path
                      className="strategy-graph__edge"
                      d={rootToFirstPath(rootCard, projection.nodes[0])}
                    />
                  ) : null}
                  {projection.edges.map((edge) => (
                    <path
                      className="strategy-graph__edge"
                      d={edge.path}
                      key={edge.edge.edgeId}
                    />
                  ))}
                </svg>
                {rootCard ? <JobRootCard item={rootCard} /> : null}
                {projection.nodes.map((node) => (
                  <StrategyGraphNode item={node} key={node.node.nodeId} />
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

type JobRootCard = {
  x: number;
  y: number;
  height: number;
  summary: string | null;
  title: string;
  width: number;
};

function jobRootCard(title: string, graph: AgentStrategyGraph): JobRootCard {
  return {
    title,
    summary: graphBackedRootSummary(graph),
    x: 28,
    y: 168,
    width: 210,
    height: 90,
  };
}

function JobRootCard({ item }: { item: JobRootCard }) {
  return (
    <article
      aria-label={
        item.summary === null ? item.title : `${item.title}: ${item.summary}`
      }
      className="strategy-graph__job-card"
      style={{
        height: item.height,
        left: item.x,
        top: item.y,
        width: item.width,
      }}
    >
      <div className="strategy-graph__job-heading">
        <span aria-hidden="true">
          <BriefcaseBusiness size={16} strokeWidth={2.3} />
        </span>
        <strong>{item.title}</strong>
      </div>
      {item.summary === null ? null : <p>{item.summary}</p>}
    </article>
  );
}

function graphBackedRootSummary(graph: AgentStrategyGraph): string | null {
  const sourceKinds = Array.from(
    new Set(
      graph.nodes
        .map((node) => node.sourceKind)
        .filter(
          (sourceKind): sourceKind is "liepin" | "cts" =>
            sourceKind === "liepin" || sourceKind === "cts",
        ),
    ),
  );
  const roundCount = new Set(
    graph.nodes
      .map((node) => node.roundNo)
      .filter((roundNo): roundNo is number => typeof roundNo === "number"),
  ).size;
  const parts: string[] = [];
  if (sourceKinds.length === 1) {
    parts.push(sourceKinds[0] === "liepin" ? "猎聘来源" : "CTS 实验来源");
  } else if (sourceKinds.length > 1) {
    parts.push("多来源");
  }
  if (roundCount > 1) {
    parts.push(`${String(roundCount)} 轮检索`);
  } else if (roundCount === 1) {
    parts.push("单轮检索");
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function rootToFirstPath(
  root: JobRootCard,
  firstNode: { x: number; y: number; height: number } | undefined,
): string {
  if (!firstNode) {
    return "";
  }
  const startX = root.x + root.width;
  const startY = root.y + root.height / 2;
  const endX = firstNode.x;
  const endY = firstNode.y + firstNode.height / 2;
  const elbowX = startX + 84;
  return ["M", startX, startY, "H", elbowX, "V", endY, "H", endX]
    .map(String)
    .join(" ");
}
