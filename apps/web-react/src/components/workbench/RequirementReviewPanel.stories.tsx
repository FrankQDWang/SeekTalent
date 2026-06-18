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
      canConfirm: true,
      draftRevisionId: "draft_001",
      otherInputPrompt: "其他",
      sections: [
        {
          backendField: "must_have_capabilities",
          displayName: "必须满足",
          items: [
            {
              allowedActions: ["set_selected"],
              editable: true,
              enabled: true,
              itemId: "item_001",
              sectionId: "must_have_capabilities",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "Python 后端平台经验",
            },
          ],
          sectionId: "must_have_capabilities",
        },
      ],
      status: "needs_review",
      summary: "Python Agent 平台后端，优先上海，要求 RAG 与工具调用经验。",
      title: "资深 Python Agent 平台后端",
      unresolvedReviewItemCount: 0,
    },
  },
} satisfies Meta<typeof RequirementReviewPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const NeedsConfirmation: Story = {};
