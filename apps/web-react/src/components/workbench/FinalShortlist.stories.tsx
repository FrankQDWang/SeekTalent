import type { Meta, StoryObj } from "@storybook/react-vite";
import { FinalShortlist } from "./FinalShortlist";

const meta = {
  title: "Workbench/FinalShortlist",
  component: FinalShortlist,
  args: {
    summary: {
      summaryId: "summary_001",
      text: "候选人 A 同时匹配 Agent 工具调用平台、RAG 和 Python 后端经验。",
    },
  },
} satisfies Meta<typeof FinalShortlist>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  args: {
    summary: null,
  },
};

export const Loading: Story = {
  args: {
    loading: true,
    summary: null,
  },
};

export const Error: Story = {
  args: {
    status: "error",
    summary: null,
  },
};
