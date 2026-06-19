import type { ReactNode } from "react";
import "./ConversationShell.css";

type ConversationShellProps = {
  main: ReactNode;
  rail: ReactNode;
  side?: ReactNode;
};

export function ConversationShell({
  main,
  rail,
  side,
}: ConversationShellProps) {
  const hasSide = side != null;

  return (
    <div
      className="conversation-shell"
      data-side={hasSide ? "visible" : "hidden"}
    >
      <aside aria-label="会话列表" className="conversation-shell__rail">
        {rail}
      </aside>
      <section className="conversation-shell__main">{main}</section>
      {hasSide ? (
        <aside aria-label="运行详情" className="conversation-shell__side">
          {side}
        </aside>
      ) : null}
    </div>
  );
}
