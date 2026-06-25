import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkbenchV2TranscriptEvent } from "../../lib/api/workbenchV2Types";
import { TranscriptV2 } from "./TranscriptV2";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TranscriptV2", () => {
  it("renders pure chat turns as normal transcript messages", () => {
    expect.hasAssertions();

    render(
      <TranscriptV2
        events={[
          transcriptEvent({
            eventId: "event_user",
            role: "user",
            step: 1,
            type: "user_message",
            payload: { text: "你好" },
          }),
          transcriptEvent({
            eventId: "event_assistant",
            role: "assistant",
            step: 2,
            type: "assistant_message",
            payload: { text: "你好，我可以帮你整理招聘需求。" },
          }),
        ]}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(within(transcript).getByText("你好")).toBeVisible();
    expect(
      within(transcript).getByText("你好，我可以帮你整理招聘需求。"),
    ).toBeVisible();
    expect(within(transcript).queryByText(/已处理/)).not.toBeInTheDocument();
  });

  it("keeps long JD text visible without collapsible run-group chrome", () => {
    expect.hasAssertions();
    const longJd = [
      "上海 AI Agent 平台工程师",
      "负责 RAG、workflow orchestration、生产可观测性和评测体系。",
      "要求 Python 后端经验，熟悉异步任务、结构化输出、复杂系统排障。",
      "加分项：候选人搜索、招聘工作台、B 端权限和审计经验。",
    ].join("\n\n");

    render(
      <TranscriptV2
        events={[
          transcriptEvent({
            eventId: "event_long_jd",
            step: 1,
            type: "user_message",
            payload: { text: longJd },
          }),
        ]}
      />,
    );

    const message = within(
      screen.getByRole("article", { name: "用户消息" }),
    ).getByText(/上海 AI Agent 平台工程师/);
    expect(message).toBeVisible();
    expect(message.textContent).toBe(longJd);
    expect(
      screen.queryByRole("button", { name: /已处理/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /展开/ }),
    ).not.toBeInTheDocument();
  });

  it("renders requirement forms inline inside the transcript", () => {
    expect.hasAssertions();
    const onRequirementAction = vi.fn();

    render(
      <TranscriptV2
        events={[
          transcriptEvent({ eventId: "event_user", step: 1 }),
          transcriptEvent({
            eventId: "event_requirement",
            step: 2,
            type: "requirement_form",
            role: "assistant",
            payload: requirementPayload(),
          }),
        ]}
        onRequirementAction={onRequirementAction}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(
      within(transcript).getByRole("region", { name: "需求确认" }),
    ).toBeVisible();
    expect(
      within(transcript).getByRole("checkbox", { name: /Python 后端经验/ }),
    ).toBeVisible();
    expect(within(transcript).queryByText(/已处理/)).not.toBeInTheDocument();
  });

  it("renders compact runtime and error events without raw payload details", () => {
    expect.hasAssertions();

    render(
      <TranscriptV2
        events={[
          transcriptEvent({
            eventId: "event_status",
            step: 1,
            type: "assistant_status",
            role: "assistant",
            payload: { text: "正在思考\n正在思考" },
          }),
          transcriptEvent({
            eventId: "event_progress",
            step: 2,
            type: "runtime_progress",
            role: "runtime",
            payload: {
              summary: "正在检索候选人",
              provider: "internal-provider",
              tool: "source_search",
            },
          }),
          transcriptEvent({
            eventId: "event_error",
            step: 3,
            type: "error",
            role: "system",
            status: "failed",
            payload: {
              message: "检索失败，请稍后重试。",
              provider: "internal-provider",
            },
          }),
        ]}
      />,
    );

    expect(screen.getAllByText("正在思考")).toHaveLength(1);
    expect(screen.getByText("正在检索候选人")).toBeVisible();
    expect(screen.getByText("检索失败，请稍后重试。")).toBeVisible();
    expect(screen.queryByText("internal-provider")).not.toBeInTheDocument();
    expect(screen.queryByText("source_search")).not.toBeInTheDocument();
  });
});

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
