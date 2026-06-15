import type { Meta, StoryObj } from "@storybook/react-vite";
import { MessageComposer } from "./MessageComposer";

function HomeStartPanel() {
  return (
    <section
      aria-label="新建招聘任务"
      style={{
        background: "var(--st-canvas)",
        minHeight: 520,
        padding: "32px",
      }}
    >
      <div style={{ marginInline: "auto", maxWidth: 720 }}>
        <h2>Wide Talent Search</h2>
        <p style={{ color: "var(--st-ink)" }}>
          输入岗位目标后，工作台会进入需求确认和检索策略图。
        </p>
        <MessageComposer placeholder="例如：寻找资深 Python Agent 平台后端" />
      </div>
    </section>
  );
}

const meta = {
  title: "Workbench/HomeStartPanel",
  component: HomeStartPanel,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof HomeStartPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Initial: Story = {};
