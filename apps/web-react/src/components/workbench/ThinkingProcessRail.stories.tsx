import type { Meta, StoryObj } from "@storybook/react-vite";
import type {
  AgentWorkbenchCandidateSummary,
  AgentWorkbenchThinkingProcess,
} from "../../lib/api/agentWorkbenchTypes";
import { ThinkingProcessRail } from "./ThinkingProcessRail";

const thinkingProcess: AgentWorkbenchThinkingProcess = {
  activeRoundNo: 1,
  rounds: [
    {
      roundNo: 1,
      status: "running",
      cards: [
        {
          title: "关键词",
          text: "AI Agent 平台工程 上海 Python RAG",
          terms: ["AI Agent", "RAG", "Python 后端", "工具调用"],
        },
        {
          title: "observation",
          text: "覆盖面较好，强匹配候选人集中在平台后端和检索工程方向。",
          terms: ["searched: 42", "scored: 12"],
        },
        {
          title: "反思和下一轮变更",
          text: "下一轮应增加工作流编排和评测相关关键词。",
          terms: ["workflow orchestration", "eval harness", "drop: 纯前端"],
        },
      ],
    },
  ],
};

const candidates: AgentWorkbenchCandidateSummary[] = [
  {
    candidateId: "candidate_001",
    rank: 1,
    displayName: "候选人 A",
    headline: "平台后端负责人",
    company: "某 AI Infra 公司",
    location: "上海",
    education: "本科",
    experienceYears: 10,
    sourceKinds: ["cts"],
    matchScore: 92,
    matchSummary: "Agent 工具调用平台和 RAG 检索链路经验匹配。",
    status: "reviewing",
    detailAvailability: "redacted",
    accessState: "redacted",
    evidenceLevel: "summary",
  },
];

const meta = {
  title: "Workbench/ThinkingProcessRail",
  component: ThinkingProcessRail,
  args: {
    candidates,
    defaultTab: "thinking",
    thinkingProcess,
  },
} satisfies Meta<typeof ThinkingProcessRail>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Running: Story = {};

export const RoundTimeline: Story = {};

export const CandidatesTab: Story = {
  args: {
    defaultTab: "candidates",
  },
};

export const EmptyThinking: Story = {
  args: {
    candidates: [],
    thinkingProcess: {
      activeRoundNo: null,
      rounds: [],
    },
  },
};
