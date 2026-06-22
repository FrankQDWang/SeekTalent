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

function ThinkingProcessRailStory({
  empty = false,
  tab = "thinking",
}: {
  empty?: boolean;
  tab?: "candidates" | "thinking";
}) {
  const view = empty
    ? {
        ...agentWorkbenchRunningViewFixture,
        thinkingProcess: {
          activeRoundNo: null,
          rounds: [],
        },
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
      side={<ConversationScreenSide defaultTab={tab} view={view} />}
    />
  );
}

const meta = {
  title: "Workbench/ThinkingProcessRail",
  component: ThinkingProcessRailStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ThinkingProcessRailStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Running: Story = {};

export const RoundTimeline: Story = {};

export const CandidatesTab: Story = {
  args: {
    tab: "candidates",
  },
};

export const EmptyThinking: Story = {
  args: {
    empty: true,
  },
};
