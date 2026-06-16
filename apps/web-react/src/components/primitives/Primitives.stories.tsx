import type { Meta, StoryObj } from "@storybook/react-vite";
import { Bell, Check, Search } from "lucide-react";
import { useState } from "react";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { FieldInput } from "./FieldInput";
import { FieldSelect } from "./FieldSelect";
import { FieldTextarea } from "./FieldTextarea";
import { Skeleton } from "./Skeleton";
import { Tabs } from "./Tabs";
import { Toast } from "./Toast";

function PrimitiveControlsGallery() {
  const [tab, setTab] = useState("candidates");
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <section
      aria-label="Primitives controls gallery"
      style={{
        background: "var(--st-panel)",
        display: "grid",
        gap: "16px",
        maxWidth: 820,
        minHeight: 520,
        padding: "20px",
      }}
    >
      <header>
        <h2 style={{ fontSize: 18, margin: 0 }}>Workbench primitives</h2>
      </header>
      <div
        style={{
          background: "var(--st-surface)",
          border: "1px solid var(--st-border)",
          borderRadius: "var(--st-radius-md)",
          display: "grid",
          gap: "16px",
          padding: "16px",
        }}
      >
        <Tabs
          ariaLabel="右栏视图"
          onValueChange={setTab}
          tabs={[
            { label: "候选人", value: "candidates" },
            { label: "思考过程", value: "thinking" },
            { label: "最终名单", value: "final" },
          ]}
          value={tab}
        />
        <div
          style={{
            display: "grid",
            gap: "12px",
            gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
          }}
        >
          <FieldInput label="职位名称" placeholder="AI Agent 平台工程师" />
          <FieldSelect
            label="来源"
            options={[
              { label: "全部来源", value: "all" },
              { label: "CTS", value: "cts" },
              { label: "猎聘", value: "liepin" },
            ]}
          />
        </div>
        <FieldTextarea
          label="补充要求"
          placeholder="继续补充岗位要求"
          rows={3}
        />
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
          <Button icon={<Search aria-hidden="true" size={16} />} tone="primary">
            开始检索
          </Button>
          <Button icon={<Check aria-hidden="true" size={16} />}>
            确认需求
          </Button>
          <Button
            icon={<Bell aria-hidden="true" size={16} />}
            onClick={() => setDialogOpen(true)}
          >
            打开审批
          </Button>
        </div>
        <Toast tone="warning" title="来源已过期">
          重新授权后继续检索。
        </Toast>
        <Skeleton aria-label="候选人列表加载中" lines={3} />
      </div>
      <Dialog
        onClose={() => setDialogOpen(false)}
        open={dialogOpen}
        title="审批详情"
      >
        读取完整简历需要确认。
      </Dialog>
    </section>
  );
}

function PrimitiveDialogOpen() {
  return (
    <section
      aria-label="Dialog primitive story"
      style={{ minHeight: 360, padding: 20 }}
    >
      <h2 style={{ fontSize: 18, margin: 0 }}>Dialog primitive</h2>
      <Dialog onClose={() => undefined} open title="审批详情">
        读取完整简历需要确认。
      </Dialog>
    </section>
  );
}

const meta = {
  title: "Primitives/ControlsGallery",
  component: PrimitiveControlsGallery,
} satisfies Meta<typeof PrimitiveControlsGallery>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ControlsGallery: Story = {};

export const DialogOpen: Story = {
  render: () => <PrimitiveDialogOpen />,
};
