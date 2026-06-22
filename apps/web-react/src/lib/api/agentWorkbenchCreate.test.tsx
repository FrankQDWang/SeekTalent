import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCreateAgentWorkbenchConversationFromJd } from "./agentWorkbench";
import { createAgentConversation, submitAgentWorkbenchMessage } from "./client";
import { createWorkbenchQueryClient } from "../query/client";
import { queryKeys } from "../query/keys";
import { agentWorkbenchRequirementReviewViewFixture } from "../../test/fixtures/agentWorkbenchBff";

vi.mock("./client", () => ({
  createAgentConversation: vi.fn(),
  submitAgentWorkbenchMessage: vi.fn(),
}));

describe("create Agent Workbench conversation from JD hook", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("creates a conversation, submits JD through the Workbench BFF, and seeds the view cache", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(createAgentConversation).mockResolvedValueOnce({
      conversation: {
        conversationId: "agent_conv_created",
        title: "AI Agent 平台工程师",
      },
    });
    vi.mocked(submitAgentWorkbenchMessage).mockResolvedValueOnce({
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

    expect(createAgentConversation).toHaveBeenCalledWith({
      title: "AI Agent 平台工程师",
    });
    expect(submitAgentWorkbenchMessage).toHaveBeenCalledWith(
      "agent_conv_created",
      expect.objectContaining({
        jobTitle: "AI Agent 平台工程师",
        messageType: "submitJd",
        text: "寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
      }),
    );
    const payload = vi.mocked(submitAgentWorkbenchMessage).mock.calls[0]?.[1];
    expect(payload?.idempotencyKey).toContain("workbench:submit-jd:");
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

    expect(createAgentConversation).not.toHaveBeenCalled();
    expect(submitAgentWorkbenchMessage).not.toHaveBeenCalled();
  });

  it("reuses the created conversation and submit idempotency key when JD submit is retried", async () => {
    expect.hasAssertions();
    const queryClient = createWorkbenchQueryClient();
    vi.mocked(createAgentConversation).mockResolvedValueOnce({
      conversation: {
        conversationId: "agent_conv_retry",
        title: "AI Agent 平台工程师",
      },
    });
    vi.mocked(submitAgentWorkbenchMessage)
      .mockRejectedValueOnce(new Error("submit failed"))
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
      "submit failed",
    );
    const firstPayload = vi.mocked(submitAgentWorkbenchMessage).mock
      .calls[0]?.[1];
    const output = await result.current.mutateAsync(input);
    const secondPayload = vi.mocked(submitAgentWorkbenchMessage).mock
      .calls[1]?.[1];

    expect(createAgentConversation).toHaveBeenCalledOnce();
    expect(submitAgentWorkbenchMessage).toHaveBeenNthCalledWith(
      1,
      "agent_conv_retry",
      expect.any(Object),
    );
    expect(submitAgentWorkbenchMessage).toHaveBeenNthCalledWith(
      2,
      "agent_conv_retry",
      expect.any(Object),
    );
    expect(secondPayload?.idempotencyKey).toBe(firstPayload?.idempotencyKey);
    expect(output.conversationId).toBe("agent_conv_retry");
  });
});
