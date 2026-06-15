import type { Meta, StoryObj } from "@storybook/react-vite";
import { Check, LoaderCircle, Trash2 } from "lucide-react";
import { Button } from "./Button";

const meta = {
  title: "Primitives/Button",
  component: Button,
  args: {
    children: "确认需求",
  },
} satisfies Meta<typeof Button>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Primary: Story = {
  args: {
    icon: <Check aria-hidden="true" />,
    tone: "primary",
  },
};

export const Secondary: Story = {
  args: {
    children: "查看候选人",
    tone: "secondary",
  },
};

export const Danger: Story = {
  args: {
    children: "删除会话",
    icon: <Trash2 aria-hidden="true" />,
    tone: "danger",
  },
};

export const Loading: Story = {
  args: {
    children: "同步状态",
    icon: <LoaderCircle aria-hidden="true" />,
    loading: true,
    tone: "primary",
  },
};

export const Disabled: Story = {
  args: {
    children: "等待来源",
    disabled: true,
    tone: "secondary",
  },
};
