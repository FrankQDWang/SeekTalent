import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchRunningViewFixture,
  wtsStoryConversationSummariesFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { ConversationList } from "./ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "./ConversationScreen";
import { ConversationShell } from "./ConversationShell";

function CandidateQueueStory({ empty = false }: { empty?: boolean }) {
  const view = empty
    ? {
        ...agentWorkbenchRunningViewFixture,
        candidates: [],
      }
    : agentWorkbenchRunningViewFixture;

  return (
    <ConversationShell
      main={<ConversationScreen view={view} />}
      rail={
        <ConversationList
          conversations={wtsStoryConversationSummariesFixture}
          selectedConversationId="agent_conv_001"
        />
      }
      side={<ConversationScreenSide defaultTab="candidates" view={view} />}
    />
  );
}

const meta = {
  title: "Workbench/CandidateQueue",
  component: CandidateQueueStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof CandidateQueueStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  args: {
    empty: true,
  },
};

export const Loading: Story = {
  args: {
    empty: true,
  },
};

export const Error: Story = {
  args: {
    empty: true,
  },
};
