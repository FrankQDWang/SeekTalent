import type { ReactNode } from "react";
import { useState } from "react";
import { PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
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
  const [railMode, setRailMode] = useState<RailMode>("expanded");
  const railVisible = railMode !== "closed";

  return (
    <div
      className="conversation-shell"
      data-rail={railMode}
      data-side={hasSide ? "visible" : "hidden"}
    >
      {railVisible ? (
        <aside aria-label="会话列表" className="conversation-shell__rail">
          <div className="conversation-shell__rail-toolbar">
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
                <PanelLeftClose aria-hidden="true" size={18} />
              )}
            </button>
            <button
              aria-label="关闭会话列表"
              className="conversation-shell__rail-control"
              onClick={() => {
                setRailMode("closed");
              }}
              title="关闭会话列表"
              type="button"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </div>
          <div className="conversation-shell__rail-body">{rail}</div>
        </aside>
      ) : (
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
      )}
      <section className="conversation-shell__main">{main}</section>
      {hasSide ? (
        <aside aria-label="运行详情" className="conversation-shell__side">
          {side}
        </aside>
      ) : null}
    </div>
  );
}
