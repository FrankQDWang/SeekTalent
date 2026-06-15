import type { Meta, StoryObj } from "@storybook/react-vite";
import { CandidateQueue } from "./CandidateQueue";

const candidates = [
  {
    candidateId: "candidate_001",
    displayName: "候选人 A",
    headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
    matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
    sourceKind: "liepin",
    status: "reviewing",
  },
  {
    candidateId: "candidate_002",
    displayName: "候选人 B",
    headline: "高级后端工程师 / 某企业协作产品 / 上海",
    matchSummary: "RAG 和搜索经验强，Agent 平台经验较少。",
    sourceKind: "cts",
    status: "new",
  },
] as const;

const meta = {
  title: "Workbench/CandidateQueue",
  component: CandidateQueue,
  args: {
    candidates,
    selectedCandidateId: "candidate_001",
    totalCount: 3,
  },
} satisfies Meta<typeof CandidateQueue>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Empty: Story = {
  args: {
    candidates: [],
    selectedCandidateId: null,
    totalCount: 0,
  },
};

export const Loading: Story = {
  args: {
    candidates: [],
    status: "loading",
  },
};

export const Error: Story = {
  args: {
    candidates: [],
    errorMessage: "候选人安全摘要读取失败。",
    status: "error",
  },
};
