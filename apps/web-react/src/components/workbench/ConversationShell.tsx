import type { ReactNode } from "react";
import "./ConversationShell.css";

type ConversationShellProps = {
  main: ReactNode;
  rail: ReactNode;
  side: ReactNode;
};

export function ConversationShell({
  main,
  rail,
  side,
}: ConversationShellProps) {
  return (
    <div className="conversation-shell">
      <aside aria-label="会话列表" className="conversation-shell__rail">
        {rail}
      </aside>
      <section className="conversation-shell__main">{main}</section>
      <aside aria-label="运行详情" className="conversation-shell__side">
        {side}
      </aside>
    </div>
  );
}
