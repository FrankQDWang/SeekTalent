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
          background: "#eef3ff",
          minHeight: "1082px",
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
    jobTitle: "AI Agent 平台工程师",
  },
};

export const CanonicalRuntimeSwimlanes: Story = {
  name: "CanonicalRuntimeSwimlanes",
  args: {
    graph: agentWorkbenchSearchStrategyGraphFixture,
    jobTitle: "AI Agent 平台工程师",
  },
};

export const LargeSearchStrategy: Story = {
  name: "LargeSearchStrategy",
  args: {
    graph: agentWorkbenchLargeGraphFixture,
    jobTitle: "AI Agent 平台工程师",
  },
};

export const Empty: Story = {
  args: {
    graph: agentWorkbenchEmptyStrategyGraphFixture,
  },
};
