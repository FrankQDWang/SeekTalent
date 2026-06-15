import type { Meta, StoryObj } from "@storybook/react-vite";
import { DetailApprovalQueue } from "./DetailApprovalQueue";

const candidates = [
  {
    candidateId: "candidate_001",
    displayName: "候选人 A",
    headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
    matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
    sourceKind: "liepin",
    status: "reviewing",
  },
] as const;

const approvals = [
  {
    approvalId: "approval_candidate_001",
    candidateId: "candidate_001",
    status: "pending",
    reason: "读取完整简历详情以确认最近项目。",
  },
] as const;

const meta = {
  title: "Workbench/DetailApprovalQueue",
  component: DetailApprovalQueue,
  args: {
    approvals,
    candidates,
  },
} satisfies Meta<typeof DetailApprovalQueue>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  args: {
    approvals: [],
    candidates: [],
  },
};

export const Loading: Story = {
  args: {
    approvals: [],
    candidates: [],
    status: "loading",
  },
};

export const Error: Story = {
  args: {
    approvals: [],
    candidates: [],
    errorMessage: "审批状态读取失败。",
    status: "error",
  },
};
