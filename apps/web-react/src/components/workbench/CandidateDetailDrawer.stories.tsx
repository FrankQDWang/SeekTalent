import type { Meta, StoryObj } from "@storybook/react-vite";
import { CandidateCard } from "./CandidateCard";
import { DetailApprovalQueue } from "./DetailApprovalQueue";

function CandidateDetailDrawerSummary() {
  const candidate = {
    candidateId: "candidate_001",
    displayName: "候选人 A",
    headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
    matchSummary: "主导过 Agent 工具调用平台和 RAG 检索链路。",
    sourceKind: "liepin" as const,
    status: "reviewing",
  };
  return (
    <aside
      aria-label="候选人详情"
      style={{
        background: "var(--st-panel)",
        border: "1px solid var(--st-border)",
        borderRadius: "var(--st-radius-md)",
        maxWidth: 420,
        padding: "16px",
      }}
    >
      <CandidateCard candidate={candidate} selected />
      <DetailApprovalQueue
        approvals={[
          {
            approvalId: "approval_001",
            candidateId: "candidate_001",
            reason: "读取完整简历以确认最近项目职责。",
            status: "pending",
          },
        ]}
        candidates={[candidate]}
      />
    </aside>
  );
}

const meta = {
  title: "Workbench/CandidateDetailDrawer",
  component: CandidateDetailDrawerSummary,
} satisfies Meta<typeof CandidateDetailDrawerSummary>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Summary: Story = {};
