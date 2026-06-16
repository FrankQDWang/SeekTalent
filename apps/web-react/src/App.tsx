import type { ReactNode } from "react";
import { FilePenLine, PanelRight } from "lucide-react";

type AppProps = {
  children?: ReactNode;
};

export function App({ children }: AppProps) {
  return (
    <main className="app-shell" data-testid="app-shell">
      <aside aria-label="任务导航" className="side-rail">
        <div aria-label="Wide Talent Search" className="brand-mark">
          WTS
        </div>
        <button className="rail-action" type="button">
          <FilePenLine aria-hidden="true" size={20} />
          <span>新建任务</span>
        </button>
        <button aria-label="展开右侧面板" className="rail-icon" type="button">
          <PanelRight aria-hidden="true" size={20} />
        </button>
      </aside>

      <section className="workspace-shell">
        <header className="top-bar">
          <div>
            <p className="surface-kicker">本地工作台</p>
            <h1>Wide Talent Search</h1>
          </div>
          <div aria-label="运行状态" className="top-status">
            <span className="status-pill" data-status="connected">
              BFF contract ready
            </span>
            <span className="status-pill" data-status="waiting">
              React foundation
            </span>
          </div>
        </header>

        {children}
      </section>
    </main>
  );
}
