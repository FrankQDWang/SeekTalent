import { cleanup, render, screen } from "@testing-library/react";
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
});
