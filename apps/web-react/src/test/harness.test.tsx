import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { createWorkbenchQueryClient } from "../lib/query/client";
import { queryKeys } from "../lib/query/keys";

describe("React Workbench shell", () => {
  it("renders the workbench root shell", () => {
    expect.hasAssertions();

    render(
      <QueryClientProvider client={createWorkbenchQueryClient()}>
        <App>
          <section aria-label="工作台内容">Content</section>
        </App>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    expect(screen.getByLabelText("工作台内容")).toBeVisible();
    expect(screen.queryByText("React foundation")).not.toBeInTheDocument();
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
