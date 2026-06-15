import type { Meta, StoryObj } from "@storybook/react-vite";
import { MessageComposer } from "./MessageComposer";

function ComposerRequirementDraft() {
  return (
    <section
      aria-label="需求草稿"
      style={{
        background: "var(--st-panel)",
        border: "1px solid var(--st-border)",
        borderRadius: "var(--st-radius-md)",
        maxWidth: 760,
        padding: "20px",
      }}
    >
      <h2>资深 Python Agent 平台后端</h2>
      <p>硬性条件：Python、RAG、工具调用平台、上海。</p>
      <MessageComposer placeholder="继续补充岗位要求" />
    </section>
  );
}

const meta = {
  title: "Workbench/Composer",
  component: ComposerRequirementDraft,
} satisfies Meta<typeof ComposerRequirementDraft>;

export default meta;

type Story = StoryObj<typeof meta>;

export const RequirementDraft: Story = {};
