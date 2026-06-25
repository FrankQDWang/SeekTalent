import type { Meta, StoryObj } from "@storybook/react-vite";
import { ConversationList } from "./ConversationList";
import { ConversationShell } from "./ConversationShell";
import { HomeStartPanel } from "./HomeStartPanel";

function ComposerRequirementDraft() {
  return (
    <ConversationShell
      main={
        <HomeStartPanel
          initialMessage={
            "1. 高级后端开发工程师\n2. 负责 AI Agent 平台后端服务和工具调用链路，要求熟悉 Python、RAG、工作流编排和工程化评测。\n3. 有搜索、推荐或候选人排序经验优先，上海团队协作。"
          }
          onSubmit={() => undefined}
        />
      }
      rail={<ConversationList conversations={[]} />}
    />
  );
}

const meta = {
  title: "Workbench/Composer",
  component: ComposerRequirementDraft,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ComposerRequirementDraft>;

export default meta;

type Story = StoryObj<typeof meta>;

export const RequirementDraft: Story = {};
