import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchMultiRoundThinkingViewFixture,
  agentWorkbenchRunningViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { ThinkingProcessRail } from "./ThinkingProcessRail";

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
    : agentWorkbenchMultiRoundThinkingViewFixture;

  return (
    <div
      style={{
        background: "#eef3ff",
        display: "grid",
        minHeight: "100vh",
        placeItems: "stretch end",
      }}
    >
      <div style={{ maxWidth: "100vw", minHeight: "100vh", width: 360 }}>
        <ThinkingProcessRail
          candidates={view.candidates}
          defaultTab={tab}
          thinkingProcess={view.thinkingProcess}
        />
      </div>
    </div>
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
