import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationShell } from "./ConversationShell";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: { children?: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

afterEach(() => {
  cleanup();
});

describe("ConversationShell", () => {
  it("omits the runtime detail rail when no side content exists", () => {
    expect.hasAssertions();

    render(
      <ConversationShell
        main={<div>Main</div>}
        rail={<div>Conversations</div>}
      />,
    );

    expect(screen.queryByLabelText("运行详情")).not.toBeInTheDocument();
  });

  it("renders the runtime detail rail when side content exists", () => {
    expect.hasAssertions();

    render(
      <ConversationShell
        main={<div>Main</div>}
        rail={<div>Conversations</div>}
        side={<div>Runtime details</div>}
      />,
    );

    expect(screen.getByLabelText("运行详情")).toHaveTextContent(
      "Runtime details",
    );
  });

  it("supports compact session rail mode", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();

    const { container } = render(
      <ConversationShell
        main={<div>Main</div>}
        rail={<div>Conversations</div>}
      />,
    );

    const shell = container.querySelector(".conversation-shell");
    expect(shell).toHaveAttribute("data-rail", "expanded");
    expect(screen.getByLabelText("会话列表")).toHaveTextContent(
      "Conversations",
    );
    expect(screen.getByText("新建任务")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "缩小会话列表" }));
    expect(shell).toHaveAttribute("data-rail", "compact");
    expect(screen.getByRole("button", { name: "展开会话列表" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "展开会话列表" }));
    expect(shell).toHaveAttribute("data-rail", "expanded");
    expect(screen.getByLabelText("会话列表")).toHaveTextContent(
      "Conversations",
    );
  });
});
