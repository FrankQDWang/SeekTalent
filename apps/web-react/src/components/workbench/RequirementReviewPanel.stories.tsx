import type { Meta, StoryObj } from "@storybook/react-vite";
import { agentWorkbenchRequirementReviewViewFixture } from "../../test/fixtures/agentWorkbenchBff";
import { ConversationList } from "./ConversationList";
import { ConversationShell } from "./ConversationShell";
import { RequirementReviewPanel } from "./RequirementReviewPanel";

function RequirementReviewPanelStory() {
  const view = agentWorkbenchRequirementReviewViewFixture;
  return (
    <ConversationShell
      main={
        <div className="requirement-review-story">
          <RequirementReviewPanel
            onConfirm={() => undefined}
            pendingActions={view.pendingActions}
            requirementDraft={view.requirementDraft}
          />
        </div>
      }
      rail={<ConversationList conversations={[]} />}
    />
  );
}

const meta = {
  title: "Workbench/RequirementReviewPanel",
  component: RequirementReviewPanelStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof RequirementReviewPanelStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const NeedsConfirmation: Story = {};
