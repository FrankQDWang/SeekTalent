import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ConversationShell } from "./ConversationShell";

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

  it("supports compact and closed session rail modes", async () => {
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

    await user.click(screen.getByRole("button", { name: "缩小会话列表" }));
    expect(shell).toHaveAttribute("data-rail", "compact");
    expect(screen.getByRole("button", { name: "展开会话列表" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "关闭会话列表" }));
    expect(shell).toHaveAttribute("data-rail", "closed");
    expect(screen.queryByLabelText("会话列表")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开会话列表" }));
    expect(shell).toHaveAttribute("data-rail", "expanded");
    expect(screen.getByLabelText("会话列表")).toHaveTextContent(
      "Conversations",
    );
  });
});
