import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  WorkbenchV2ConversationView,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import {
  ConversationScreenV2,
  ConversationScreenV2Side,
  hasConversationV2RuntimeSurface,
} from "./ConversationScreenV2";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConversationScreenV2", () => {
  it("renders pure chat without switching to the old workflow screen", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_user",
              step: 1,
              type: "user_message",
              payload: { text: "你好" },
            }),
            transcriptEvent({
              eventId: "event_assistant",
              step: 2,
              type: "assistant_message",
              role: "assistant",
              payload: { text: "可以，先告诉我招聘目标。" },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("你好")).toBeVisible();
    expect(screen.getByText("可以，先告诉我招聘目标。")).toBeVisible();
    expect(screen.queryByText(/已处理/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认需求" }),
    ).not.toBeInTheDocument();
  });

  it("keeps requirement forms in the transcript and wires actions", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onRequirementAction = vi.fn();

    render(
      <ConversationScreenV2
        onRequirementAction={onRequirementAction}
        view={conversationView({
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_requirement",
              step: 1,
              type: "requirement_form",
              role: "assistant",
              payload: requirementPayload(),
            }),
          ],
        })}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(
      within(transcript).getByRole("region", { name: "需求确认" }),
    ).toBeVisible();

    await user.click(
      within(transcript).getByRole("checkbox", { name: /Python 后端经验/ }),
    );

    expect(onRequirementAction).toHaveBeenCalledWith({
      action: "set_selected",
      itemId: "item_python",
      selected: false,
    });
  });

  it("exposes runtime side surface state when runtime is active", () => {
    expect.hasAssertions();
    const view = conversationView({
      conversation: conversationSummary({
        runtimeState: "running",
        runtimeRunId: "run_123",
      }),
      runtime: { state: "running", runtimeRunId: "run_123" },
      transcriptEvents: [
        transcriptEvent({
          eventId: "event_progress",
          step: 1,
          type: "runtime_progress",
          role: "runtime",
          payload: { summary: "正在检索候选人" },
        }),
      ],
    });

    expect(hasConversationV2RuntimeSurface(view)).toBe(true);
    render(<ConversationScreenV2Side view={view} />);

    expect(
      screen.getByRole("complementary", { name: "运行状态" }),
    ).toBeVisible();
    expect(screen.getByText("running")).toBeVisible();
    expect(screen.getByText("run_123")).toBeVisible();
    expect(screen.getByText("正在检索候选人")).toBeVisible();
  });

  it("submits a generic message through the composer", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmitMessage = vi.fn(() => Promise.resolve());

    render(
      <ConversationScreenV2
        onSubmitMessage={onSubmitMessage}
        view={conversationView()}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("输入消息、JD 或下一步招聘需求"),
      "继续帮我找候选人",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onSubmitMessage).toHaveBeenCalledWith("继续帮我找候选人");
  });
});

function conversationView(
  overrides: Partial<WorkbenchV2ConversationView> = {},
): WorkbenchV2ConversationView {
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: conversationSummary(),
    transcriptEvents: [transcriptEvent()],
    requirementForm: null,
    runtime: null,
    ...overrides,
  };
}

function conversationSummary(
  overrides: Partial<WorkbenchV2ConversationView["conversation"]> = {},
): WorkbenchV2ConversationView["conversation"] {
  return {
    conversationId: "agentv2_1",
    title: "先聊一下候选人搜索",
    runtimeState: "idle",
    runtimeRunId: null,
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    updatedAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function transcriptEvent(
  overrides: Partial<WorkbenchV2TranscriptEvent> = {},
): WorkbenchV2TranscriptEvent {
  return {
    eventId: "event_1",
    step: 1,
    type: "user_message",
    role: "user",
    status: "completed",
    payload: { text: "先聊一下候选人搜索" },
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function requirementPayload() {
  return {
    draft: {
      sections: [
        {
          section_id: "core",
          display_name: "核心条件",
          items: [
            {
              item_id: "item_python",
              text: "Python 后端经验",
              selected: true,
              allowed_actions: ["set_selected"],
              status: "active",
            },
          ],
        },
      ],
      other_input_prompt: "补充其他要求",
      can_confirm: true,
    },
  };
}
