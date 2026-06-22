import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { agentWorkbenchRequirementReviewViewFixture } from "../../test/fixtures/agentWorkbenchBff";
import { RequirementReviewPanel } from "./RequirementReviewPanel";

const draft = agentWorkbenchRequirementReviewViewFixture.requirementDraft;
const pendingActions =
  agentWorkbenchRequirementReviewViewFixture.pendingActions;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RequirementReviewPanel", () => {
  it("renders selectable requirement draft items", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onToggleItem = vi.fn();

    render(
      <RequirementReviewPanel
        onToggleItem={onToggleItem}
        pendingActions={pendingActions}
        requirementDraft={draft}
      />,
    );

    await user.click(screen.getByRole("button", { name: /交互设计功底扎实/ }));

    expect(screen.getByText("必须满足")).toBeVisible();
    expect(onToggleItem).toHaveBeenCalledWith(
      expect.objectContaining({ itemId: "item_001" }),
      false,
    );
  });

  it("submits other requirement text only after the mutation succeeds", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAddOther = vi.fn(() => Promise.resolve());

    render(
      <RequirementReviewPanel
        onAddOther={onAddOther}
        pendingActions={pendingActions}
        requirementDraft={draft}
      />,
    );

    await user.type(screen.getByLabelText("其他补充要求"), "补充评测平台经验");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(onAddOther).toHaveBeenCalledWith("补充评测平台经验");
    expect(screen.getByLabelText("其他补充要求")).toHaveValue("");
  });

  it("keeps other requirement text when mutation fails", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAddOther = vi.fn(() => Promise.reject(new Error("failed")));

    render(
      <RequirementReviewPanel
        onAddOther={onAddOther}
        pendingActions={pendingActions}
        requirementDraft={draft}
      />,
    );

    await user.type(screen.getByLabelText("其他补充要求"), "补充评测平台经验");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(onAddOther).toHaveBeenCalledWith("补充评测平台经验");
    expect(screen.getByLabelText("其他补充要求")).toHaveValue(
      "补充评测平台经验",
    );
  });
});
