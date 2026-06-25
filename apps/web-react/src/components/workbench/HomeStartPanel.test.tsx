import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeStartPanel } from "./HomeStartPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HomeStartPanel", () => {
  it("keeps submit disabled until a message is entered", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();

    render(<HomeStartPanel onSubmit={vi.fn()} />);

    expect(screen.getByRole("button", { name: "开始寻才" })).toBeDisabled();

    await user.type(
      screen.getByLabelText("消息、JD 或招聘需求"),
      "你好，先聊一下招聘目标。",
    );

    expect(screen.getByRole("button", { name: "开始寻才" })).toBeEnabled();
  });

  it("submits the trimmed arbitrary message from the WTS single input", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());

    render(<HomeStartPanel onSubmit={onSubmit} />);

    await user.type(
      screen.getByLabelText("消息、JD 或招聘需求"),
      "  你好，先聊一下招聘目标。 ",
    );
    await user.click(screen.getByRole("button", { name: "开始寻才" }));

    expect(onSubmit).toHaveBeenCalledWith({
      message: "你好，先聊一下招聘目标。",
    });
  });

  it("keeps the form content available when submit fails", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.reject(new Error("network failed")));

    render(<HomeStartPanel onSubmit={onSubmit} />);

    await user.type(
      screen.getByLabelText("消息、JD 或招聘需求"),
      "你好，先聊一下招聘目标。",
    );
    await user.click(screen.getByRole("button", { name: "开始寻才" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "请求失败，请稍后重试。",
    );
    expect(screen.getByLabelText("消息、JD 或招聘需求")).toHaveValue(
      "你好，先聊一下招聘目标。",
    );
  });

  it("fills the WTS input from a suggestion card", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();

    render(<HomeStartPanel onSubmit={vi.fn()} />);

    const firstPrompt = screen.getAllByRole("button", {
      name: /AI Agent 平台工程师/,
    })[0];
    if (!firstPrompt) {
      throw new Error("Expected at least one WTS suggestion card.");
    }
    await user.click(firstPrompt);

    expect(screen.getByLabelText("消息、JD 或招聘需求")).toHaveValue(
      "上海 AI Agent 平台工程师，3 年以上 Python 后端经验，熟悉 RAG 和 workflow orchestration。",
    );
  });

  it("submits with Enter through the form and keeps Shift+Enter as a textarea newline", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());

    render(<HomeStartPanel onSubmit={onSubmit} />);

    const textarea = screen.getByLabelText("消息、JD 或招聘需求");
    await user.type(textarea, "第一行{Shift>}{Enter}{/Shift}第二行");
    expect(textarea).toHaveValue("第一行\n第二行");

    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith({
      message: "第一行\n第二行",
    });
  });
});
