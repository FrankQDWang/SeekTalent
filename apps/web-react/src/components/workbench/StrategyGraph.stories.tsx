import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchEmptyStrategyGraphFixture,
  agentWorkbenchLargeGraphFixture,
  agentWorkbenchSearchStrategyGraphFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { StrategyGraph } from "./StrategyGraph";

const meta = {
  title: "Workbench/StrategyGraphCanvas",
  component: StrategyGraph,
  parameters: {
    layout: "fullscreen",
  },
  decorators: [
    (Story) => (
      <div
        style={{
          background: "var(--st-canvas)",
          minHeight: "720px",
          padding: "24px",
        }}
      >
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof StrategyGraph>;

export default meta;

type Story = StoryObj<typeof meta>;

export const SearchStrategy: Story = {
  name: "SearchStrategy",
  args: {
    graph: agentWorkbenchSearchStrategyGraphFixture,
  },
};

export const LargeSearchStrategy: Story = {
  name: "LargeSearchStrategy",
  args: {
    graph: agentWorkbenchLargeGraphFixture,
  },
};

export const Empty: Story = {
  args: {
    graph: agentWorkbenchEmptyStrategyGraphFixture,
  },
};
