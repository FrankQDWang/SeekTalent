import type { Meta, StoryObj } from "@storybook/react-vite";
import { HomeStartPanel } from "./HomeStartPanel";

const meta = {
  title: "Workbench/HomeStartPanel",
  component: HomeStartPanel,
  args: {
    onSubmit: () => undefined,
  },
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof HomeStartPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Initial: Story = {};
