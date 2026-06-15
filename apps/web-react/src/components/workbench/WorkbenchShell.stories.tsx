import type { Meta, StoryObj } from "@storybook/react-vite";
import { CandidateQueue } from "./CandidateQueue";
import { ConversationShell } from "./ConversationShell";
import { StrategyGraph } from "./StrategyGraph";
import { Transcript } from "./Transcript";
import type { AgentStrategyGraph } from "../../lib/strategy-graph/graphProjection";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";

const graph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求确认",
      status: "completed",
      summary: "Python 后端 / Agent 平台 / 上海",
      sourceKind: "all",
    },
    {
      nodeId: "search",
      kind: "activity",
      label: "检索策略",
      status: "running",
      summary: "扩展 RAG 与工具调用关键词",
      sourceKind: "liepin",
    },
  ],
  edges: [
    {
      edgeId: "requirements-search",
      fromNodeId: "requirements",
      toNodeId: "search",
      label: "生成检索",
      status: "running",
    },
  ],
};

const groups: AgentWorkbenchTranscriptGroup[] = [
  {
    completedAt: null,
    events: [
      {
        createdAt: "2026-06-13T09:28:06.000Z",
        eventId: "evt_shell_message",
        itemId: "msg_shell",
        kind: "message.completed",
        label: "Agent response",
        payload: {
          kind: "message",
          messageId: "msg_shell",
          summary: "已启动第一轮候选人检索。",
        },
        status: "completed",
        summary: "已启动第一轮候选人检索。",
      },
    ],
    groupId: "group_shell",
    startedAt: "2026-06-13T09:28:03.000Z",
    status: "running",
    title: "运行中",
  },
];

const meta = {
  title: "Workbench/WorkbenchShell",
  component: ConversationShell,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ConversationShell>;

export default meta;

type Story = StoryObj<typeof meta>;

export const FigmaThumbnailReference: Story = {
  args: {
    main: (
      <div className="conversation-view">
        <div className="conversation-view__workspace">
          <StrategyGraph graph={graph} />
          <Transcript groups={groups} />
        </div>
      </div>
    ),
    rail: (
      <nav aria-label="会话">
        <strong>Wide Talent Search</strong>
      </nav>
    ),
    side: (
      <CandidateQueue
        candidates={[
          {
            candidateId: "candidate_001",
            displayName: "候选人 A",
            headline: "平台后端负责人 / 上海",
            matchSummary: "Agent 平台和 RAG 检索经验匹配。",
            sourceKind: "liepin",
            status: "reviewing",
          },
        ]}
      />
    ),
  },
};
