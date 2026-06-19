import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createWorkbenchQueryClient } from "../lib/query/client";
import { queryKeys } from "../lib/query/keys";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: { children?: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

describe("React Workbench shell", () => {
  it("renders a usable workbench scaffold", () => {
    expect.hasAssertions();

    render(
      <QueryClientProvider client={createWorkbenchQueryClient()}>
        <App />
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Wide Talent Search" }),
    ).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "任务导航" }),
    ).toBeVisible();
    expect(screen.getByText("React foundation")).toBeVisible();
  });

  it("configures query defaults and stable BFF query keys", () => {
    expect.hasAssertions();

    const client = createWorkbenchQueryClient();

    expect(client.getDefaultOptions().queries?.retry).toBe(false);
    expect(client.getDefaultOptions().queries?.staleTime).toBe(15_000);
    expect(queryKeys.agentConversation("conv_123")).toEqual([
      "agent",
      "workbench",
      "conversations",
      "conv_123",
    ]);
    expect(queryKeys.agentCandidateDetail("conv_123", "candidate_456")).toEqual(
      [
        "agent",
        "workbench",
        "conversations",
        "conv_123",
        "candidates",
        "candidate_456",
        "detail",
      ],
    );
  });
});
