import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkbenchV2TranscriptEvent } from "../../lib/api/workbenchV2Types";
import { RequirementFormEvent } from "./RequirementFormEvent";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RequirementFormEvent", () => {
  it("renders the requirement draft inline from snake_case fields", () => {
    expect.hasAssertions();

    render(<RequirementFormEvent event={requirementEvent()} />);

    const form = screen.getByRole("region", { name: "需求确认" });
    expect(within(form).getByText("核心条件")).toBeVisible();
    expect(within(form).getByText("Python 后端经验")).toBeVisible();
    expect(within(form).queryByText("已选择")).not.toBeInTheDocument();
    expect(within(form).queryByText("未选择")).not.toBeInTheDocument();
    expect(
      within(form).getByRole("checkbox", { name: /Python 后端经验/ }),
    ).toBeChecked();
    expect(within(form).getByLabelText("补充其他要求")).toBeVisible();
    expect(
      within(form).getByRole("button", { name: "确认需求" }),
    ).toBeVisible();
  });

  it("allows a backend select-capable checkbox to be unchecked with set_selected", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAction = vi.fn(() => Promise.resolve());

    render(
      <RequirementFormEvent event={requirementEvent()} onAction={onAction} />,
    );

    const checkbox = screen.getByRole("checkbox", { name: /Python 后端经验/ });
    expect(checkbox).toBeEnabled();

    await user.click(checkbox);

    expect(onAction).toHaveBeenCalledWith({
      action: "set_selected",
      itemId: "item_python",
      selected: false,
    });
  });

  it("submits add-other text inline and clears the input after success", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAction = vi.fn(() => Promise.resolve());

    render(
      <RequirementFormEvent event={requirementEvent()} onAction={onAction} />,
    );

    const input = screen.getByLabelText("补充其他要求");
    await user.type(input, "需要 LangGraph 生产经验");
    await user.click(screen.getByRole("button", { name: "添加补充要求" }));

    expect(onAction).toHaveBeenCalledWith({
      action: "add_other",
      text: "需要 LangGraph 生产经验",
    });
    expect(input).toHaveValue("");
  });

  it("submits the confirm action inline", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAction = vi.fn();

    render(
      <RequirementFormEvent event={requirementEvent()} onAction={onAction} />,
    );

    await user.click(screen.getByRole("button", { name: "确认需求" }));

    expect(onAction).toHaveBeenCalledWith({ action: "confirm" });
  });

  it("renders confirmed forms as compact readonly summaries", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onAction = vi.fn();

    render(
      <RequirementFormEvent
        event={requirementEvent({
          type: "requirement_form_confirmed",
          payload: {
            ...requirementPayload(),
            readonly: true,
          },
        })}
        onAction={onAction}
      />,
    );

    expect(screen.getByText("Python 后端经验")).toBeVisible();
    expect(screen.getByText("需求已确认")).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: /Python 后端经验/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("补充其他要求")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "添加补充要求" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByText("Python 后端经验"));
    expect(onAction).not.toHaveBeenCalled();
  });

  it("keeps checkbox semantics with a visible checkbox mark inside the pill", () => {
    expect.hasAssertions();

    render(<RequirementFormEvent event={requirementEvent()} />);

    const checkbox = screen.getByRole("checkbox", { name: /Python 后端经验/ });
    expect(checkbox.nextElementSibling).toHaveClass(
      "requirement-form-event__box",
    );
    expect(normalizedCssRulesText()).toContain(
      ".requirement-form-event__item:has(input:focus-visible)",
    );
    expect(normalizedCssRulesText()).toContain(
      ".requirement-form-event__box { align-items: center;",
    );
    expect(normalizedCssRulesText()).toContain(
      '.requirement-form-event__item[data-selected="true"] .requirement-form-event__box::after',
    );
    expect(normalizedCssRulesText()).not.toContain(
      ".requirement-form-event__box { display: none;",
    );
  });
});

function requirementEvent(
  overrides: Partial<WorkbenchV2TranscriptEvent> = {},
): WorkbenchV2TranscriptEvent {
  return {
    eventId: "event_requirement",
    step: 3,
    type: "requirement_form",
    role: "assistant",
    status: "pending",
    payload: requirementPayload(),
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function requirementPayload() {
  return {
    draft: {
      sections: [
        {
          section_id: "core",
          display_name: "核心条件",
          items: [
            {
              item_id: "item_python",
              text: "Python 后端经验",
              selected: true,
              allowed_actions: [
                "select",
                "edit",
                "delete",
                "move_to_preferred_capabilities",
              ],
              status: "active",
            },
          ],
        },
      ],
      other_input_prompt: "补充其他要求",
      can_confirm: true,
    },
  };
}

function normalizedCssRulesText(): string {
  return readFileSync(
    join(process.cwd(), "src/components/workbench/RequirementFormEvent.css"),
    "utf8",
  ).replaceAll(/\s+/g, " ");
}
