import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  useSubmitAgentWorkbenchMessage,
  useUpdateAgentWorkbenchRequirementDraft,
} from "./agentWorkbench";
import {
  submitAgentWorkbenchMessage,
  updateAgentWorkbenchRequirementDraft,
} from "./client";
import { createWorkbenchQueryClient } from "../query/client";
import { agentWorkbenchRequirementReviewViewFixture } from "../../test/fixtures/agentWorkbenchBff";

vi.mock("./client", () => ({
  amendAgentWorkbenchRequirementFromText: vi.fn(),
  confirmAgentWorkbenchRequirements: vi.fn(),
  createAgentWorkbenchConversation: vi.fn(),
  getAgentWorkbenchCandidateDetail: vi.fn(),
  getAgentWorkbenchConversation: vi.fn(),
  listAgentWorkbenchConversations: vi.fn(),
  submitAgentWorkbenchMessage: vi.fn(),
  updateAgentWorkbenchRequirementDraft: vi.fn(),
}));

describe("Agent Workbench mutation idempotency keys", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reuses the user message idempotency key across retries", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(submitAgentWorkbenchMessage)
      .mockRejectedValueOnce(new Error("network failed"))
      .mockResolvedValueOnce(agentWorkbenchRequirementReviewViewFixture);

    const { result } = renderHook(
      () => useSubmitAgentWorkbenchMessage("agent_conv_1"),
      { wrapper: wrapperFor(queryClient) },
    );

    await expect(
      result.current.mutateAsync("继续帮我找候选人"),
    ).rejects.toThrow("network failed");
    const firstPayload = vi.mocked(submitAgentWorkbenchMessage).mock
      .calls[0]?.[1];
    await result.current.mutateAsync("继续帮我找候选人");
    const secondPayload = vi.mocked(submitAgentWorkbenchMessage).mock
      .calls[1]?.[1];

    expect(submitAgentWorkbenchMessage).toHaveBeenCalledTimes(2);
    expect(secondPayload?.idempotencyKey).toBe(firstPayload?.idempotencyKey);
  });

  it("reuses the requirement update idempotency key across retries", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(updateAgentWorkbenchRequirementDraft)
      .mockRejectedValueOnce(new Error("network failed"))
      .mockResolvedValueOnce(agentWorkbenchRequirementReviewViewFixture);

    const { result } = renderHook(
      () => useUpdateAgentWorkbenchRequirementDraft("agent_conv_1"),
      { wrapper: wrapperFor(queryClient) },
    );
    const input = {
      draftRevisionId: "reqdraft_1",
      operations: [
        {
          itemId: "item_1",
          op: "set_selected" as const,
          selected: false,
        },
      ],
    };

    await expect(result.current.mutateAsync(input)).rejects.toThrow(
      "network failed",
    );
    const firstPayload = vi.mocked(updateAgentWorkbenchRequirementDraft).mock
      .calls[0]?.[1];
    await result.current.mutateAsync(input);
    const secondPayload = vi.mocked(updateAgentWorkbenchRequirementDraft).mock
      .calls[1]?.[1];

    expect(updateAgentWorkbenchRequirementDraft).toHaveBeenCalledTimes(2);
    expect(secondPayload?.idempotencyKey).toBe(firstPayload?.idempotencyKey);
  });
});

function wrapperFor(
  queryClient: ReturnType<typeof createWorkbenchQueryClient>,
) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
