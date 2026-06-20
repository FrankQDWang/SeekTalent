import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchCandidateApprovalRequiredDetailFixture,
  agentWorkbenchCandidateDetailFixture,
  agentWorkbenchRunningViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { CandidateDetailDrawer } from "./CandidateDetailDrawer";

const candidate = agentWorkbenchRunningViewFixture.candidates[0] ?? null;

const meta = {
  title: "Workbench/CandidateDetailDrawer",
  component: CandidateDetailDrawer,
  args: {
    candidate,
    onClose: () => undefined,
    open: true,
  },
  render: (args) => (
    <div
      style={{
        minHeight: "720px",
        position: "relative",
        width: "100%",
      }}
    >
      <CandidateDetailDrawer {...args} />
    </div>
  ),
} satisfies Meta<typeof CandidateDetailDrawer>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Summary: Story = {
  args: {
    detail: agentWorkbenchCandidateDetailFixture,
    status: "ready",
  },
};

export const ApprovalRequired: Story = {
  args: {
    detail: agentWorkbenchCandidateApprovalRequiredDetailFixture,
    status: "ready",
  },
};

export const Loading: Story = {
  args: {
    detail: null,
    status: "loading",
  },
};

export const Error: Story = {
  args: {
    detail: null,
    errorMessage: "候选人详情读取失败，请稍后重试。",
    onRetry: () => undefined,
    status: "error",
  },
};
