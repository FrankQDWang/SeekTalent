import {
  BriefcaseBusiness,
  Maximize2,
  Minimize2,
  Minus,
  Plus,
  RotateCcw,
} from "lucide-react";
import { useRef, useState, type PointerEvent } from "react";
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

type PanState = {
  pointerId: number;
  startX: number;
  startY: number;
  scrollLeft: number;
  scrollTop: number;
};

const DEFAULT_ZOOM = 1;
const MIN_ZOOM = 0.6;
const MAX_ZOOM = 1.8;
const ZOOM_STEP = 0.15;

export function StrategyGraph({ graph, jobTitle = null }: StrategyGraphProps) {
  const rootCard = jobTitle ? jobRootCard(jobTitle, graph) : null;
  const projection = projectStrategyTimelineGraph(graph, {
    reserveRootColumn: rootCard !== null,
  });
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const panStateRef = useRef<PanState | null>(null);
  const [isMaximized, setIsMaximized] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const canvasWidth = Math.max(
    projection.width,
    rootCard ? rootCard.x + rootCard.width + 80 : 0,
  );
  const canvasHeight = Math.max(
    projection.height,
    rootCard ? rootCard.y + rootCard.height + 80 : 0,
  );
  const frameWidth = Math.ceil(canvasWidth * zoom);
  const frameHeight = Math.ceil(canvasHeight * zoom);

  const resizeZoom = (direction: "in" | "out") => {
    setZoom((current) =>
      clampZoom(current + (direction === "in" ? ZOOM_STEP : -ZOOM_STEP)),
    );
  };

  const resetViewport = () => {
    setZoom(DEFAULT_ZOOM);
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTo({ left: 0, top: 0 });
    }
  };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const target = event.target;
    if (target instanceof HTMLElement && target.closest("button, a")) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    panStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
    };
    viewport.setPointerCapture(event.pointerId);
    setIsPanning(true);
    event.preventDefault();
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const panState = panStateRef.current;
    const viewport = viewportRef.current;
    if (!panState || !viewport || event.pointerId !== panState.pointerId) {
      return;
    }
    viewport.scrollLeft =
      panState.scrollLeft - (event.clientX - panState.startX);
    viewport.scrollTop = panState.scrollTop - (event.clientY - panState.startY);
  };

  const stopPanning = (event: PointerEvent<HTMLDivElement>) => {
    const panState = panStateRef.current;
    const viewport = viewportRef.current;
    if (!panState || !viewport || event.pointerId !== panState.pointerId) {
      return;
    }
    panStateRef.current = null;
    if (viewport.hasPointerCapture(event.pointerId)) {
      viewport.releasePointerCapture(event.pointerId);
    }
    setIsPanning(false);
  };

  return (
    <section
      aria-label="检索策略图"
      className="strategy-graph"
      data-maximized={isMaximized ? "true" : "false"}
    >
      {projection.nodes.length === 0 && rootCard === null ? (
        <div className="strategy-graph__empty">等待检索策略生成</div>
      ) : (
        <>
          <div
            aria-label="检索策略图画布"
            className="strategy-graph__viewport"
            data-panning={isPanning ? "true" : "false"}
            onPointerCancel={stopPanning}
            onPointerDown={handlePointerDown}
            onPointerLeave={stopPanning}
            onPointerMove={handlePointerMove}
            onPointerUp={stopPanning}
            ref={viewportRef}
            tabIndex={0}
          >
            <div
              className="strategy-graph__canvas-frame"
              style={{
                height: frameHeight,
                width: frameWidth,
              }}
            >
              <div
                className="strategy-graph__canvas"
                style={{
                  height: canvasHeight,
                  transform: `scale(${String(zoom)})`,
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
                      className="strategy-graph__edge strategy-graph__edge--root"
                      data-edge-id="job-root->strategy-root"
                      d={rootToFirstPath(rootCard, projection.nodes[0])}
                    />
                  ) : null}
                  {projection.edges.map((edge) => (
                    <path
                      className="strategy-graph__edge"
                      data-edge-id={edge.edge.edgeId}
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
          <div aria-label="检索策略图控制" className="strategy-graph__controls">
            <button
              aria-label="放大策略图"
              onClick={() => resizeZoom("in")}
              title="放大"
              type="button"
            >
              <Plus aria-hidden="true" size={18} />
            </button>
            <button
              aria-label="缩小策略图"
              onClick={() => resizeZoom("out")}
              title="缩小"
              type="button"
            >
              <Minus aria-hidden="true" size={18} />
            </button>
            <button
              aria-label="最大化策略图"
              onClick={() => setIsMaximized(true)}
              title="最大化"
              type="button"
            >
              <Maximize2 aria-hidden="true" size={17} />
            </button>
            <button
              aria-label="恢复策略图初始位置"
              onClick={resetViewport}
              title="恢复初始位置"
              type="button"
            >
              <RotateCcw aria-hidden="true" size={17} />
            </button>
          </div>
          {isMaximized ? (
            <button
              aria-label="退出策略图最大化"
              className="strategy-graph__minimize"
              onClick={() => setIsMaximized(false)}
              title="退出最大化"
              type="button"
            >
              <Minimize2 aria-hidden="true" size={18} />
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

function clampZoom(value: number): number {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Number(value.toFixed(2))));
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
  if (sourceKinds.length === 1) {
    return sourceKinds[0] === "liepin" ? "猎聘来源" : "CTS 实验来源";
  }
  if (sourceKinds.length > 1) {
    return "多来源";
  }
  return null;
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
  const elbowX = Math.min(startX + 84, endX - 24);
  return ["M", startX, startY, "H", elbowX, "V", endY, "H", endX]
    .map(String)
    .join(" ");
}
