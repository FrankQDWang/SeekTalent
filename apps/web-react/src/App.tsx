import type { ReactNode } from "react";

type AppProps = {
  children?: ReactNode;
};

export function App({ children }: AppProps) {
  return (
    <main className="app-shell" data-testid="app-shell">
      {children}
    </main>
  );
}
