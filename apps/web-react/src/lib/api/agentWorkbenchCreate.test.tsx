import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCreateAgentWorkbenchConversationFromJd } from "./agentWorkbench";
import { createAgentWorkbenchConversationFromJd } from "./client";
import { createWorkbenchQueryClient } from "../query/client";
import { queryKeys } from "../query/keys";
import { agentWorkbenchRequirementReviewViewFixture } from "../../test/fixtures/agentWorkbenchBff";

vi.mock("./client", () => ({
  createAgentWorkbenchConversationFromJd: vi.fn(),
}));

describe("create Agent Workbench conversation from JD hook", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("starts from JD through one Workbench BFF request and seeds the view cache", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(createAgentWorkbenchConversationFromJd).mockResolvedValueOnce({
      ...agentWorkbenchRequirementReviewViewFixture,
      conversation: {
        ...agentWorkbenchRequirementReviewViewFixture.conversation,
        conversationId: "agent_conv_created",
        title: "AI Agent 平台工程师",
      },
    });

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => useCreateAgentWorkbenchConversationFromJd(),
      { wrapper },
    );

    const output = await result.current.mutateAsync({
      jobDescription:
        "寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
      jobTitle: "AI Agent 平台工程师",
    });

    expect(createAgentWorkbenchConversationFromJd).toHaveBeenCalledWith(
      expect.objectContaining({
        jobDescription:
          "寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
        jobTitle: "AI Agent 平台工程师",
      }),
    );
    const payload = vi.mocked(createAgentWorkbenchConversationFromJd).mock
      .calls[0]?.[0];
    expect(payload?.idempotencyKey).toContain("workbench:from-jd:");
    expect(payload).not.toHaveProperty("sourceKinds");
    expect(output.conversationId).toBe("agent_conv_created");
    expect(
      queryClient.getQueryData(
        queryKeys.agentConversation("agent_conv_created"),
      ),
    ).toMatchObject({
      conversation: { conversationId: "agent_conv_created" },
      requirementDraft: { status: "needs_review" },
    });
    await waitFor(() => {
      expect(
        queryClient.isFetching({ queryKey: queryKeys.agentConversations }),
      ).toBeGreaterThanOrEqual(0);
    });
  });

  it("rejects blank JD input before creating a conversation", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => useCreateAgentWorkbenchConversationFromJd(),
      { wrapper },
    );

    await expect(
      result.current.mutateAsync({
        jobDescription: "   \n\t ",
        jobTitle: null,
      }),
    ).rejects.toThrow("Job description is required.");

    expect(createAgentWorkbenchConversationFromJd).not.toHaveBeenCalled();
  });

  it("reuses the from-JD idempotency key when first-turn start is retried", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(createAgentWorkbenchConversationFromJd)
      .mockRejectedValueOnce(new Error("start failed"))
      .mockResolvedValueOnce({
        ...agentWorkbenchRequirementReviewViewFixture,
        conversation: {
          ...agentWorkbenchRequirementReviewViewFixture.conversation,
          conversationId: "agent_conv_retry",
          title: "AI Agent 平台工程师",
        },
      });

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => useCreateAgentWorkbenchConversationFromJd(),
      { wrapper },
    );
    const input = {
      jobDescription:
        "寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
      jobTitle: "AI Agent 平台工程师",
    };

    await expect(result.current.mutateAsync(input)).rejects.toThrow(
      "start failed",
    );
    const firstPayload = vi.mocked(createAgentWorkbenchConversationFromJd).mock
      .calls[0]?.[0];
    const output = await result.current.mutateAsync(input);
    const secondPayload = vi.mocked(createAgentWorkbenchConversationFromJd).mock
      .calls[1]?.[0];

    expect(createAgentWorkbenchConversationFromJd).toHaveBeenCalledTimes(2);
    expect(secondPayload?.idempotencyKey).toBe(firstPayload?.idempotencyKey);
    expect(output.conversationId).toBe("agent_conv_retry");
  });
});
