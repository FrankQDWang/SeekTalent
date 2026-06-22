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

  it("submits other requirement text before confirming when the amendment succeeds", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAddOther = vi.fn(() => Promise.resolve());
    const onConfirm = vi.fn();

    render(
      <RequirementReviewPanel
        onAddOther={onAddOther}
        onConfirm={onConfirm}
        pendingActions={pendingActions}
        requirementDraft={draft}
      />,
    );

    await user.type(screen.getByLabelText("其他补充要求"), "补充评测平台经验");
    await user.click(screen.getByRole("button", { name: "确认需求" }));

    expect(onAddOther).toHaveBeenCalledWith("补充评测平台经验");
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("其他补充要求")).toHaveValue("");
  });

  it("keeps other requirement text and does not confirm when amendment fails", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAddOther = vi.fn(() => Promise.reject(new Error("failed")));
    const onConfirm = vi.fn();

    render(
      <RequirementReviewPanel
        onAddOther={onAddOther}
        onConfirm={onConfirm}
        pendingActions={pendingActions}
        requirementDraft={draft}
      />,
    );

    await user.type(screen.getByLabelText("其他补充要求"), "补充评测平台经验");
    await user.click(screen.getByRole("button", { name: "确认需求" }));

    expect(onAddOther).toHaveBeenCalledWith("补充评测平台经验");
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByLabelText("其他补充要求")).toHaveValue(
      "补充评测平台经验",
    );
  });
});
