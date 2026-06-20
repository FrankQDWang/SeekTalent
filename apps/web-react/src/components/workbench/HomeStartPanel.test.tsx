import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeStartPanel } from "./HomeStartPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HomeStartPanel", () => {
  it("keeps submit disabled until a job description is entered", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();

    render(<HomeStartPanel onSubmit={vi.fn()} />);

    expect(screen.getByRole("button", { name: "开始寻才" })).toBeDisabled();

    await user.type(
      screen.getByLabelText("职位描述"),
      "寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
    );

    expect(screen.getByRole("button", { name: "开始寻才" })).toBeEnabled();
  });

  it("submits the trimmed job title and job description", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());

    render(<HomeStartPanel onSubmit={onSubmit} />);

    await user.type(
      screen.getByLabelText("职位名称"),
      "  AI Agent 平台工程师 ",
    );
    await user.type(
      screen.getByLabelText("职位描述"),
      "  需要 Python 后端、RAG 和 workflow orchestration 经验。 ",
    );
    await user.click(screen.getByRole("button", { name: "开始寻才" }));

    expect(onSubmit).toHaveBeenCalledWith({
      jobDescription: "需要 Python 后端、RAG 和 workflow orchestration 经验。",
      jobTitle: "AI Agent 平台工程师",
    });
  });

  it("keeps the form content available when submit fails", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.reject(new Error("network failed")));

    render(<HomeStartPanel onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("职位名称"), "AI Agent 平台工程师");
    await user.type(
      screen.getByLabelText("职位描述"),
      "需要 Python 后端经验。",
    );
    await user.click(screen.getByRole("button", { name: "开始寻才" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "请求失败，请稍后重试。",
    );
    expect(screen.getByLabelText("职位名称")).toHaveValue(
      "AI Agent 平台工程师",
    );
    expect(screen.getByLabelText("职位描述")).toHaveValue(
      "需要 Python 后端经验。",
    );
  });
});
