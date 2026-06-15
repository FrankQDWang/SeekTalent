import type { Meta, StoryObj } from "@storybook/react-vite";
import { RequirementReviewPanel } from "./RequirementReviewPanel";

const meta = {
  title: "Workbench/RequirementReviewPanel",
  component: RequirementReviewPanel,
  args: {
    pendingActions: {
      allowed: ["confirm_requirements"],
      pendingCommandCount: 0,
      pendingMemoryReviewCount: 0,
      pendingRequirementReviewCount: 1,
      primary: "confirm_requirements",
    },
    requirementDraft: {
      draftRevisionId: "draft_001",
      summary: "Python Agent 平台后端，优先上海，要求 RAG 与工具调用经验。",
      title: "资深 Python Agent 平台后端",
    },
  },
} satisfies Meta<typeof RequirementReviewPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const NeedsConfirmation: Story = {};
