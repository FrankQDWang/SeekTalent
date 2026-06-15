import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchArchivedViewFixture,
  agentWorkbenchCompletedViewFixture,
  agentWorkbenchFailedViewFixture,
  agentWorkbenchInitialViewFixture,
  agentWorkbenchPermissionDeniedViewFixture,
  agentWorkbenchRequirementReviewViewFixture,
  agentWorkbenchRunningViewFixture,
  agentWorkbenchSourceExpiredViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "./ConversationScreen";
import { ConversationShell } from "./ConversationShell";

const meta = {
  title: "Workbench/ConversationScreen",
  component: ConversationScreen,
  parameters: {
    layout: "fullscreen",
  },
  decorators: [
    (Story) => (
      <div style={{ minHeight: "720px" }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ConversationScreen>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Initial: Story = {
  args: {
    view: agentWorkbenchInitialViewFixture,
  },
};

export const RequirementReview: Story = {
  args: {
    view: agentWorkbenchRequirementReviewViewFixture,
  },
};

export const RunningWithStream: Story = {
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};

export const SourceExpired: Story = {
  args: {
    view: agentWorkbenchSourceExpiredViewFixture,
  },
};

export const PermissionDenied: Story = {
  args: {
    view: agentWorkbenchPermissionDeniedViewFixture,
  },
};

export const Failed: Story = {
  args: {
    view: agentWorkbenchFailedViewFixture,
  },
};

export const Completed: Story = {
  args: {
    view: agentWorkbenchCompletedViewFixture,
  },
};

export const Archived: Story = {
  args: {
    view: agentWorkbenchArchivedViewFixture,
  },
};

export const WorkbenchShellComposed: Story = {
  render: ({ view }) => (
    <ConversationShell
      main={<ConversationScreen view={view} />}
      rail={
        <nav aria-label="会话">
          <strong>Wide Talent Search</strong>
          <span>{view.conversation.title}</span>
        </nav>
      }
      side={<ConversationScreenSide view={view} />}
    />
  ),
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};
