import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";
import { FieldInput } from "./FieldInput";
import { FieldSelect } from "./FieldSelect";
import { Skeleton } from "./Skeleton";
import { Tabs } from "./Tabs";
import { Toast } from "./Toast";

describe("Workbench primitives", () => {
  it("renders typed field controls with stable labels and states", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onInputChange = vi.fn();
    const onSelectChange = vi.fn();

    render(
      <>
        <FieldInput
          label="职位名称"
          onChange={(event) => {
            onInputChange(event.currentTarget.value);
          }}
          placeholder="AI Agent 平台工程师"
        />
        <FieldSelect
          label="来源"
          onChange={(event) => {
            onSelectChange(event.currentTarget.value);
          }}
          options={[
            { label: "全部来源", value: "all" },
            { label: "猎聘", value: "liepin" },
          ]}
        />
      </>,
    );

    await user.type(screen.getByLabelText("职位名称"), "后端");
    await user.selectOptions(screen.getByLabelText("来源"), "liepin");

    expect(onInputChange).toHaveBeenLastCalledWith("后端");
    expect(onSelectChange).toHaveBeenLastCalledWith("liepin");
  });

  it("keeps tab state accessible and controlled by the selected value", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onValueChange = vi.fn();

    render(
      <Tabs
        ariaLabel="右栏视图"
        onValueChange={onValueChange}
        tabs={[
          { label: "候选人", value: "candidates" },
          { label: "思考过程", value: "thinking" },
        ]}
        value="thinking"
      />,
    );

    expect(screen.getByRole("tab", { name: "思考过程" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "候选人" }));

    expect(onValueChange).toHaveBeenCalledWith("candidates");
  });

  it("renders dialog, toast, and skeleton primitives with accessible states", () => {
    expect.hasAssertions();

    render(
      <>
        <Dialog onClose={() => undefined} open title="审批详情">
          读取完整简历需要确认。
        </Dialog>
        <Toast tone="warning" title="来源已过期">
          重新授权后继续检索。
        </Toast>
        <Skeleton aria-label="候选人列表加载中" lines={3} />
      </>,
    );

    expect(screen.getByRole("dialog", { name: "审批详情" })).toBeVisible();
    expect(screen.getByRole("status", { name: "来源已过期" })).toBeVisible();
    expect(screen.getByLabelText("候选人列表加载中")).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });
});
