import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageComposer } from "./MessageComposer";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MessageComposer", () => {
  it("keeps typed text when async submit fails", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.reject(new Error("network failed")));

    render(<MessageComposer onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText("输入下一步要求"), "继续补充");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onSubmit).toHaveBeenCalledWith("继续补充");
    expect(screen.getByPlaceholderText("输入下一步要求")).toHaveValue(
      "继续补充",
    );
  });

  it("clears typed text after async submit succeeds", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());

    render(<MessageComposer onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText("输入下一步要求"), "继续补充");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onSubmit).toHaveBeenCalledWith("继续补充");
    expect(screen.getByPlaceholderText("输入下一步要求")).toHaveValue("");
  });

  it("submits with Enter repeatedly and keeps Shift+Enter as a newline", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmit = vi.fn(() => Promise.resolve());

    render(<MessageComposer onSubmit={onSubmit} />);

    const textarea = screen.getByPlaceholderText("输入下一步要求");
    await user.type(textarea, "第一行{Shift>}{Enter}{/Shift}第二行");
    expect(textarea).toHaveValue("第一行\n第二行");

    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("第一行\n第二行");
    expect(textarea).toHaveFocus();

    await user.type(textarea, "第二次");
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenLastCalledWith("第二次");
    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(textarea).toHaveValue("");
    expect(textarea).toHaveFocus();
  });
});
