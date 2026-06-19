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
});
