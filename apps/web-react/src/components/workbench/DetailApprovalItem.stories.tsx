import type { Meta, StoryObj } from "@storybook/react-vite";
import { DetailApprovalItem } from "./DetailApprovalItem";

const candidate = {
  candidateId: "candidate_001",
  displayName: "候选人 A",
  headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
} as const;

const detailApproval = {
  approvalId: "approval_candidate_001",
  candidateId: "candidate_001",
  status: "pending",
  reason: "读取完整简历详情以确认最近项目。",
} as const;

const meta = {
  title: "Workbench/DetailApprovalItem",
  component: DetailApprovalItem,
  args: {
    approval: detailApproval,
    candidate,
  },
} satisfies Meta<typeof DetailApprovalItem>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Pending: Story = {};

export const Approved: Story = {
  args: {
    approval: {
      ...detailApproval,
      status: "approved",
      reason: "已批准读取安全详情快照。",
    },
  },
};
