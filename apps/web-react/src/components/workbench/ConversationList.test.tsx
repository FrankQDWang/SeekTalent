import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationList } from "./ConversationList";

vi.mock("../../lib/api/agentWorkbench", () => ({
  useAgentWorkbenchConversations: vi.fn(),
}));

afterEach(() => {
  cleanup();
});

describe("ConversationList", () => {
  it("renders v2 summaries with the shared conversation shape", () => {
    expect.hasAssertions();

    render(
      <ConversationList
        conversations={[
          {
            conversationId: "agentv2_1",
            title: "你好",
            status: "idle",
          },
          {
            conversationId: "agentv2_2",
            title: "上海 AI Agent 平台工程师",
            status: "running",
          },
        ]}
        selectedConversationId="agentv2_2"
      />,
    );

    expect(screen.getByRole("link", { name: /你好/ })).toHaveAttribute(
      "href",
      "/conversations/agentv2_1",
    );
    expect(
      screen.getByRole("link", { name: /上海 AI Agent 平台工程师/ }),
    ).toHaveAttribute("aria-current", "page");
  });
});
