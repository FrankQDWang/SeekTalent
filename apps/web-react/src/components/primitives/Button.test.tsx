import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

describe("Button", () => {
  it("renders the requested visual tone", () => {
    expect.hasAssertions();

    render(<Button tone="primary">确认需求</Button>);

    expect(screen.getByRole("button", { name: "确认需求" })).toHaveAttribute(
      "data-tone",
      "primary",
    );
  });

  it("disables while loading and keeps a status label", () => {
    expect.hasAssertions();

    render(<Button loading>保存更改</Button>);

    expect(screen.getByRole("button", { name: "处理中" })).toBeDisabled();
  });

  it("preserves click behavior when enabled", async () => {
    expect.hasAssertions();

    const user = userEvent.setup();
    const onClick = vi.fn();

    render(<Button onClick={onClick}>新建任务</Button>);

    await user.click(screen.getByRole("button", { name: "新建任务" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
