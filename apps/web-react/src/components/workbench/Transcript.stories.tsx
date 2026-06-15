import type { Meta, StoryObj } from "@storybook/react-vite";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";
import { Transcript } from "./Transcript";

const expandedGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_run_001",
    title: "已处理 2m",
    status: "running",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_assistant_001",
        itemId: "msg_assistant_001",
        kind: "message.completed",
        status: "completed",
        label: "Agent response",
        summary: "我会先确认硬性条件，再启动第一轮检索并记录策略变化。",
        payload: {
          kind: "message",
          messageId: "msg_assistant_001",
          summary: "我会先确认硬性条件，再启动第一轮检索并记录策略变化。",
        },
        createdAt: "2026-06-13T09:28:06.000Z",
      },
      {
        eventId: "evt_source_search_001",
        itemId: "tool_source_search_001",
        kind: "sourceSearch.completed",
        status: "completed",
        label: "检索候选人来源",
        summary: "42 safe profile summaries matched source filters.",
        payload: {
          kind: "tool",
          activityId: "activity_source_search",
          itemId: "tool_source_search_001",
          sourceRuntimeRunId: "agent_run_001",
          summary: "42 profiles found, 12 moved to scoring.",
        },
        createdAt: "2026-06-13T09:29:18.000Z",
      },
    ],
  },
];

const contextGroup: AgentWorkbenchTranscriptGroup = {
  groupId: "group_context_001",
  title: "上下文已压缩",
  status: "completed",
  startedAt: "2026-06-13T09:30:10.000Z",
  completedAt: "2026-06-13T09:30:10.000Z",
  events: [
    {
      eventId: "evt_context_compacted_001",
      itemId: "context_compaction_001",
      kind: "context.compacted",
      status: "completed",
      label: "上下文已压缩",
      summary: "已保留需求、检索策略、候选人摘要和审批状态。",
      payload: {
        kind: "context",
        summary: "已保留需求、检索策略、候选人摘要和审批状态。",
      },
      createdAt: "2026-06-13T09:30:10.000Z",
    },
  ],
};

const failedSourceSearchEvent = {
  eventId: "evt_source_search_failed",
  itemId: "tool_source_search_001",
  kind: "sourceSearch.failed",
  status: "failed",
  label: "来源检索失败",
  summary: "source_connection_expired",
  payload: {
    kind: "tool",
    itemId: "tool_source_search_001",
    summary: "No raw provider data was exposed.",
  },
  createdAt: "2026-06-13T09:29:18.000Z",
} satisfies AgentWorkbenchTranscriptGroup["events"][number];

const failedGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_failed_001",
    status: "failed",
    title: "已处理 2m",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: "2026-06-13T09:29:18.000Z",
    events: [failedSourceSearchEvent],
  },
];

const toolReadGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_tool_read",
    status: "completed",
    title: "读取文件",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: "2026-06-13T09:28:22.000Z",
    events: [
      {
        eventId: "evt_tool_read",
        itemId: "tool_read_001",
        kind: "tool.completed",
        status: "completed",
        label: "Read",
        summary: "src/seektalent_ui/agent_workbench_routes.py",
        payload: {
          kind: "tool",
          itemId: "tool_read_001",
          summary: "读取 BFF workbench routes 以确认 replay 生命周期。",
        },
        createdAt: "2026-06-13T09:28:22.000Z",
      },
    ],
  },
];

const webSearchRunningGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_web_search",
    status: "running",
    title: "网页检索",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_web_search",
        itemId: "web_search_001",
        kind: "webSearch.started",
        status: "running",
        label: "Web search",
        summary: "检索 Agent 平台后端候选人市场信号。",
        payload: {
          kind: "source_search",
          itemId: "web_search_001",
          summary: "搜索运行中",
        },
        createdAt: "2026-06-13T09:28:22.000Z",
      },
    ],
  },
];

const fileSearchCompleteGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_file_search",
    status: "completed",
    title: "文件检索",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: "2026-06-13T09:28:22.000Z",
    events: [
      {
        eventId: "evt_file_search",
        itemId: "file_search_001",
        kind: "sourceSearch.completed",
        status: "completed",
        label: "File search",
        summary: "找到 12 条安全候选人摘要。",
        payload: {
          kind: "source_search",
          itemId: "file_search_001",
          summary: "候选人摘要检索完成",
        },
        createdAt: "2026-06-13T09:28:22.000Z",
      },
    ],
  },
];

const fileReadRunningGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_file_read",
    status: "running",
    title: "读取资料",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_file_read",
        itemId: "file_read_001",
        kind: "tool.started",
        status: "running",
        label: "Read candidate summary",
        summary: "读取候选人安全摘要。",
        payload: {
          kind: "tool",
          itemId: "file_read_001",
          summary: "读取运行中",
        },
        createdAt: "2026-06-13T09:28:22.000Z",
      },
    ],
  },
];

const guidedFollowupGroups: AgentWorkbenchTranscriptGroup[] = [
  ...expandedGroups,
  {
    groupId: "group_followup",
    status: "completed",
    title: "建议下一步",
    startedAt: "2026-06-13T09:32:03.000Z",
    completedAt: "2026-06-13T09:32:15.000Z",
    events: [
      {
        eventId: "evt_followup",
        itemId: "msg_followup",
        kind: "message.completed",
        status: "completed",
        label: "Agent response",
        summary: "可以确认需求、查看候选人详情，或继续收紧检索关键词。",
        payload: {
          kind: "message",
          messageId: "msg_followup",
          summary: "可以确认需求、查看候选人详情，或继续收紧检索关键词。",
        },
        createdAt: "2026-06-13T09:32:15.000Z",
      },
    ],
  },
];

const meta = {
  title: "Workbench/Transcript",
  component: Transcript,
  args: {
    groups: [...expandedGroups, contextGroup],
  },
} satisfies Meta<typeof Transcript>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ExpandedRunGroup: Story = {};

export const CollapsedRunGroup: Story = {
  args: {
    defaultCollapsedGroupIds: ["group_run_001"],
    groups: expandedGroups,
  },
};

export const ToolFailed: Story = {
  args: {
    groups: failedGroups,
  },
};

export const ContextDivider: Story = {
  args: {
    groups: [contextGroup],
  },
};

export const ToolReadDetails: Story = {
  args: {
    groups: toolReadGroups,
  },
  play: ({ canvasElement }) => {
    const button = canvasElement.querySelector<HTMLButtonElement>(
      ".transcript-tool-event__detail-toggle",
    );
    button?.click();
  },
};

export const WebSearchRunning: Story = {
  args: {
    groups: webSearchRunningGroups,
  },
};

export const FileSearchComplete: Story = {
  args: {
    groups: fileSearchCompleteGroups,
  },
};

export const FileReadRunning: Story = {
  args: {
    groups: fileReadRunningGroups,
  },
};

export const GuidedFollowup: Story = {
  args: {
    groups: guidedFollowupGroups,
  },
};
