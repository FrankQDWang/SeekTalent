import type { Meta, StoryObj } from "@storybook/react-vite";
import { CandidateCard } from "./CandidateCard";

const candidateFixture = {
  candidateId: "candidate_001",
  displayName: "候选人 A",
  headline: "平台后端负责人 / 某 AI Infra 公司 / 上海",
  matchSummary: "有 Agent 工具调用平台和 RAG 检索链路经验。",
  sourceKind: "liepin",
  status: "reviewing",
} as const;

const meta = {
  title: "Workbench/CandidateCard",
  component: CandidateCard,
  args: {
    candidate: candidateFixture,
  },
} satisfies Meta<typeof CandidateCard>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Populated: Story = {};

export const Selected: Story = {
  args: {
    selected: true,
  },
};
