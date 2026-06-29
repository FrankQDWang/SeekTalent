import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { PanelLeft, PanelLeftOpen, SquarePen } from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";
import "./ConversationShell.css";

type ConversationShellProps = {
  main: ReactNode;
  rail: ReactNode;
  side?: ReactNode;
};

type RailMode = "expanded" | "compact" | "closed";

export function ConversationShell({
  main,
  rail,
  side,
}: ConversationShellProps) {
  const hasSide = side != null;
  const [railMode, setRailMode] = useState<RailMode>(
    hasSide ? "compact" : "expanded",
  );
  const railVisible = railMode !== "closed";

  useEffect(() => {
    setRailMode((current) => {
      if (hasSide && current === "expanded") {
        return "compact";
      }
      if (!hasSide && current === "compact") {
        return "expanded";
      }
      return current;
    });
  }, [hasSide]);

  const railPanel = railVisible ? (
    <>
      <Panel
        className="conversation-shell__panel conversation-shell__panel--rail"
        defaultSize={railMode === "compact" ? 64 : 257}
        groupResizeBehavior="preserve-pixel-size"
        id="rail"
        maxSize={railMode === "compact" ? 92 : 420}
        minSize={railMode === "compact" ? 56 : 180}
      >
        <aside aria-label="会话列表" className="conversation-shell__rail">
          <div className="conversation-shell__rail-toolbar">
            <div aria-label="Wide Talent Search" className="brand-mark">
              WTS
            </div>
            <button
              aria-label={
                railMode === "compact" ? "展开会话列表" : "缩小会话列表"
              }
              className="conversation-shell__rail-control"
              onClick={() => {
                setRailMode((current) =>
                  current === "compact" ? "expanded" : "compact",
                );
              }}
              title={railMode === "compact" ? "展开会话列表" : "缩小会话列表"}
              type="button"
            >
              {railMode === "compact" ? (
                <PanelLeftOpen aria-hidden="true" size={18} />
              ) : (
                <PanelLeft aria-hidden="true" size={18} />
              )}
            </button>
          </div>
          <div className="conversation-shell__rail-body">
            <a className="conversation-shell__new-task" href="/">
              <SquarePen aria-hidden="true" size={20} />
              <span>新建任务</span>
            </a>
            {rail}
          </div>
        </aside>
      </Panel>
      <Separator
        aria-label="调整会话列表和工作区宽度"
        className="conversation-shell__separator"
      />
    </>
  ) : null;

  return (
    <div
      className="conversation-shell"
      data-rail={railMode}
      data-side={hasSide ? "visible" : "hidden"}
    >
      {!railVisible ? (
        <button
          aria-label="打开会话列表"
          className="conversation-shell__rail-opener"
          onClick={() => {
            setRailMode("expanded");
          }}
          title="打开会话列表"
          type="button"
        >
          <PanelLeftOpen aria-hidden="true" size={18} />
        </button>
      ) : null}
      <Group
        className="conversation-shell__layout"
        id="conversation-shell-layout"
        orientation="horizontal"
        resizeTargetMinimumSize={{ coarse: 28, fine: 12 }}
      >
        {railPanel}
        <Panel
          className="conversation-shell__panel conversation-shell__panel--main"
          defaultSize={hasSide ? "72%" : "100%"}
          id="main"
          minSize={hasSide ? 520 : 640}
        >
          <section className="conversation-shell__main">{main}</section>
        </Panel>
        {hasSide ? (
          <>
            <Separator
              aria-label="调整工作区和运行详情宽度"
              className="conversation-shell__separator"
            />
            <Panel
              className="conversation-shell__panel conversation-shell__panel--side"
              defaultSize={340}
              groupResizeBehavior="preserve-pixel-size"
              id="side"
              maxSize={560}
              minSize={300}
            >
              <aside aria-label="运行详情" className="conversation-shell__side">
                {side}
              </aside>
            </Panel>
          </>
        ) : null}
      </Group>
    </div>
  );
}
