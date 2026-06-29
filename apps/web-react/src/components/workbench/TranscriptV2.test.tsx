import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
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

  it("only expands the latest requirement form snapshot", () => {
    expect.hasAssertions();

    render(
      <TranscriptV2
        events={[
          transcriptEvent({
            eventId: "event_requirement_original",
            step: 1,
            type: "requirement_form",
            role: "assistant",
            payload: requirementPayload({ selected: true }),
          }),
          transcriptEvent({
            eventId: "event_requirement_updated",
            step: 2,
            type: "requirement_form",
            role: "assistant",
            payload: requirementPayload({ selected: false }),
          }),
        ]}
      />,
    );

    expect(screen.getAllByRole("region", { name: "需求确认" })).toHaveLength(1);
    expect(
      screen.queryByText("需求表单已更新，显示最新版本。"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /Python 后端经验/ }),
    ).not.toBeChecked();
  });

  it("keeps requirement form anchored when a later snapshot updates checkbox state", () => {
    expect.hasAssertions();
    const userEvent = transcriptEvent({
      eventId: "event_user",
      step: 1,
      type: "user_message",
      role: "user",
      payload: { text: "这是 JD" },
    });
    const originalForm = transcriptEvent({
      eventId: "event_requirement_original",
      step: 2,
      type: "requirement_form",
      role: "assistant",
      payload: requirementPayload({ selected: true }),
    });
    const statusEvent = transcriptEvent({
      eventId: "event_status_after_form",
      step: 3,
      type: "assistant_status",
      role: "assistant",
      payload: { text: "已记录修改" },
    });
    const updatedForm = transcriptEvent({
      eventId: "event_requirement_updated",
      step: 4,
      type: "requirement_form",
      role: "assistant",
      payload: requirementPayload({ selected: false }),
    });

    const { container } = render(
      <TranscriptV2
        events={[userEvent, originalForm, statusEvent, updatedForm]}
      />,
    );

    const children = Array.from(
      container.querySelector(".transcript-v2")?.children ?? [],
    );
    const requirementIndex = children.findIndex((child) =>
      child.classList.contains("requirement-form-event"),
    );
    const statusIndex = children.findIndex((child) =>
      child.textContent?.includes("已记录修改"),
    );

    expect(requirementIndex).toBeGreaterThan(-1);
    expect(statusIndex).toBeGreaterThan(-1);
    expect(requirementIndex).toBeLessThan(statusIndex);
    expect(
      screen.getByRole("checkbox", { name: /Python 后端经验/ }),
    ).not.toBeChecked();
  });

  it("does not force-scroll when only a requirement form snapshot changes", () => {
    expect.hasAssertions();
    const userEvent = transcriptEvent({
      eventId: "event_user",
      step: 1,
      type: "user_message",
      role: "user",
      payload: { text: "这是 JD" },
    });
    const originalForm = transcriptEvent({
      eventId: "event_requirement_original",
      step: 2,
      type: "requirement_form",
      role: "assistant",
      payload: requirementPayload({ selected: true }),
    });
    const statusEvent = transcriptEvent({
      eventId: "event_status_after_form",
      step: 3,
      type: "assistant_status",
      role: "assistant",
      payload: { text: "已记录修改" },
    });
    const { rerender } = render(
      <TranscriptV2 events={[userEvent, originalForm, statusEvent]} />,
    );
    const transcript = screen.getByRole("region", {
      name: "Agent transcript",
    });
    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1000 });
    transcript.scrollTop = 600;
    fireEvent.scroll(transcript);

    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1300 });
    rerender(
      <TranscriptV2
        events={[
          userEvent,
          originalForm,
          statusEvent,
          transcriptEvent({
            eventId: "event_requirement_updated",
            step: 4,
            type: "requirement_form",
            role: "assistant",
            payload: requirementPayload({ selected: false }),
          }),
        ]}
      />,
    );

    expect(transcript.scrollTop).toBe(600);
    expect(
      screen.getByRole("checkbox", { name: /Python 后端经验/ }),
    ).not.toBeChecked();
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
              runtimeRunId: "run_secret",
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
    expect(screen.queryByText("run_secret")).not.toBeInTheDocument();
  });

  it("does not render empty runtime result placeholders as transcript content", () => {
    expect.hasAssertions();

    render(
      <TranscriptV2
        events={[
          transcriptEvent({
            eventId: "event_result_empty",
            step: 1,
            type: "runtime_result",
            role: "runtime",
            payload: {
              state: "idle",
              summary: "当前还没有运行结果。",
            },
          }),
        ]}
      />,
    );

    expect(screen.queryByText("运行结果")).not.toBeInTheDocument();
    expect(screen.queryByText("当前还没有运行结果。")).not.toBeInTheDocument();
  });

  it("keeps following the newest transcript event while the user is at the bottom", () => {
    expect.hasAssertions();
    const firstEvent = transcriptEvent({
      eventId: "event_user",
      step: 1,
      payload: { text: "现在进度如何？" },
    });
    const { rerender } = render(<TranscriptV2 events={[firstEvent]} />);
    const transcript = screen.getByRole("region", {
      name: "Agent transcript",
    });
    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1000 });
    transcript.scrollTop = 600;
    fireEvent.scroll(transcript);

    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1400 });
    rerender(
      <TranscriptV2
        events={[
          firstEvent,
          transcriptEvent({
            eventId: "event_assistant",
            role: "assistant",
            step: 2,
            type: "assistant_message",
            payload: { text: "当前招聘流程失败。" },
          }),
        ]}
      />,
    );

    expect(transcript.scrollTop).toBe(1400);
  });

  it("does not force-scroll when the user is reading older transcript content", () => {
    expect.hasAssertions();
    const firstEvent = transcriptEvent({
      eventId: "event_user",
      step: 1,
      payload: { text: "现在进度如何？" },
    });
    const { rerender } = render(<TranscriptV2 events={[firstEvent]} />);
    const transcript = screen.getByRole("region", {
      name: "Agent transcript",
    });
    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1000 });
    transcript.scrollTop = 100;
    fireEvent.scroll(transcript);

    setScrollMetrics(transcript, { clientHeight: 400, scrollHeight: 1400 });
    rerender(
      <TranscriptV2
        events={[
          firstEvent,
          transcriptEvent({
            eventId: "event_assistant",
            role: "assistant",
            step: 2,
            type: "assistant_message",
            payload: { text: "当前招聘流程失败。" },
          }),
        ]}
      />,
    );

    expect(transcript.scrollTop).toBe(100);
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

function setScrollMetrics(
  element: HTMLElement,
  {
    clientHeight,
    scrollHeight,
  }: {
    clientHeight: number;
    scrollHeight: number;
  },
) {
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    value: clientHeight,
  });
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
}

function requirementPayload({ selected = true }: { selected?: boolean } = {}) {
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
              selected,
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
