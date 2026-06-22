import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ReactNode } from "react";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";
import { Transcript } from "./Transcript";
import "./Transcript.stories.css";

const expandedGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_run_001",
    title: "已处理 2m 48s",
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
        summary:
          "我先做只读调研：一部分查 Codex/OpenAI 公开信息，另一部分读当前仓库里 agent/conversation/runtime 的后端。不会改文件或 git 状态。",
        payload: {
          kind: "message",
          messageId: "msg_assistant_001",
          summary:
            "我先做只读调研：一部分查 Codex/OpenAI 公开信息，另一部分读当前仓库里 agent/conversation/runtime 的后端。不会改文件或 git 状态。",
        },
        createdAt: "2026-06-13T09:28:06.000Z",
      },
      {
        eventId: "evt_source_search_001",
        itemId: "tool_source_search_001",
        kind: "sourceSearch.completed",
        status: "completed",
        label:
          "Loaded a tool{count, plural, one {已搜索网页 # 次} other {已搜索网页 # 次}}",
        summary: "",
        payload: {
          kind: "source_search",
          activityId: "activity_source_search",
          itemId: "tool_source_search_001",
          sourceRuntimeRunId: "agent_run_001",
          summary: "",
        },
        createdAt: "2026-06-13T09:29:18.000Z",
      },
      {
        eventId: "evt_assistant_002",
        itemId: "msg_assistant_002",
        kind: "message.completed",
        status: "completed",
        label: "Agent response",
        summary:
          "公开资料这边已经确认：Codex CLI 是开源的，但 Codex Desktop/App/Cloud 这些产品面不是“完整开源 UI”。我会读一下我们仓库后端，重点看 transcript/message/activity/runtime stream 是怎么落库和对外暴露的。",
        payload: {
          kind: "message",
          messageId: "msg_assistant_002",
          summary:
            "公开资料这边已经确认：Codex CLI 是开源的，但 Codex Desktop/App/Cloud 这些产品面不是“完整开源 UI”。我会读一下我们仓库后端，重点看 transcript/message/activity/runtime stream 是怎么落库和对外暴露的。",
        },
        createdAt: "2026-06-13T09:30:06.000Z",
      },
      {
        eventId: "evt_file_read_running",
        itemId: "file_read_running",
        kind: "tool.started",
        status: "running",
        label: "正在读取 service.py",
        summary: "",
        payload: {
          kind: "tool",
          itemId: "file_read_running",
          summary: "",
        },
        createdAt: "2026-06-13T09:31:22.000Z",
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
    title: "Loaded a toolread 2 files",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: "2026-06-13T09:28:22.000Z",
    events: [
      {
        eventId: "evt_tool_read",
        itemId: "tool_read_001",
        kind: "tool.completed",
        status: "completed",
        label: "Loaded a toolread 2 files",
        summary: "读取 Fw Ceo Review 技能",
        payload: {
          kind: "tool",
          itemId: "tool_read_001",
          summary: "Read common-safety.md\nRead SKILL.md",
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
    title: "已处理 1m 45s",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_web_search",
        itemId: "web_search_001",
        kind: "webSearch.started",
        status: "running",
        label:
          "Loaded a tool{count, plural, one {已搜索网页 # 次} other {已搜索网页 # 次}}",
        summary: "",
        payload: {
          kind: "source_search",
          itemId: "web_search_001",
          summary: "",
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
    title: "已处理 2m",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: "2026-06-13T09:28:22.000Z",
    events: [
      {
        eventId: "evt_file_search",
        itemId: "file_search_001",
        kind: "sourceSearch.completed",
        status: "completed",
        label: "Read 5 files, searched code和已列出文件",
        summary: "",
        payload: {
          kind: "source_search",
          itemId: "file_search_001",
          summary: "",
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
    title: "已处理 2m 48s",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_file_read",
        itemId: "file_read_001",
        kind: "tool.started",
        status: "running",
        label: "Read 5 files, searched code和已列出文件",
        summary: "",
        payload: {
          kind: "tool",
          itemId: "file_read_001",
          summary: "",
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
        summary: "请你看看 codex 的 rust 源码",
        payload: {
          kind: "message",
          messageId: "msg_followup",
          summary: "请你看看 codex 的 rust 源码",
        },
        createdAt: "2026-06-13T09:32:15.000Z",
      },
    ],
  },
];

const meta = {
  title: "Workbench/Transcript",
  component: Transcript,
  decorators: [
    (Story, context) => {
      const parameters = context.parameters as {
        codexReferenceVariant?: unknown;
      };
      const variant = codexReferenceVariant(parameters.codexReferenceVariant);

      if (variant === "window") {
        return (
          <CodexWindowFrame>
            <Story />
          </CodexWindowFrame>
        );
      }

      if (variant === "tool-detail") {
        return (
          <div className="codex-reference-frame codex-reference-frame--tool-detail">
            <Story />
          </div>
        );
      }

      return (
        <CodexSliceFrame guided={variant === "guided"}>
          <Story />
        </CodexSliceFrame>
      );
    },
  ],
  args: {
    groups: [...expandedGroups, contextGroup],
  },
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof Transcript>;

export default meta;

type Story = StoryObj<typeof meta>;
type CodexReferenceVariant = "guided" | "slice" | "tool-detail" | "window";

function codexReferenceVariant(value: unknown): CodexReferenceVariant {
  if (value === "guided" || value === "tool-detail" || value === "window") {
    return value;
  }
  return "slice";
}

export const ExpandedRunGroup: Story = {};
ExpandedRunGroup.parameters = {
  codexReferenceVariant: "window",
};

export const CollapsedRunGroup: Story = {
  args: {
    defaultCollapsedGroupIds: ["group_run_001"],
    groups: expandedGroups,
  },
  parameters: {
    codexReferenceVariant: "window",
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
  parameters: {
    codexReferenceVariant: "tool-detail",
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
  parameters: {
    codexReferenceVariant: "guided",
  },
};

function CodexWindowFrame({ children }: { children: ReactNode }) {
  return (
    <div className="codex-reference-frame codex-reference-frame--window">
      <div className="codex-reference-menu">
        <strong>Codex</strong>
        <span>File</span>
        <span>Edit</span>
        <span>View</span>
        <span>Window</span>
        <span>Help</span>
      </div>
      <aside aria-label="Codex thread list" className="codex-reference-sidebar">
        <div className="codex-reference-sidebar__row" data-active="true">
          architecture-research 如何设计
        </div>
        <div className="codex-reference-sidebar__list">
          {[
            "搜索",
            "计划",
            "设计稿截图",
            "transcript 组件",
            "visual diff",
            "本地验证",
            "设置",
          ].map((item) => (
            <div className="codex-reference-sidebar__row" key={item}>
              {item}
            </div>
          ))}
        </div>
      </aside>
      <main className="codex-reference-main">
        <div className="codex-reference-titlebar">
          <span>architecture-research 如何</span>
          <span>•••</span>
        </div>
        <div className="codex-reference-scroll">
          <div className="codex-reference-prompt">蜻是如何设计的?</div>
          <div className="codex-reference-transcript">{children}</div>
        </div>
        <div className="codex-reference-composer">要求和后续变更</div>
      </main>
      <aside aria-label="Codex context" className="codex-reference-context">
        <div className="codex-reference-context__panel">
          <strong>上下文</strong>
          <span>仓库 · 分支 · 当前模型</span>
          <span>只读调研</span>
        </div>
      </aside>
      <div className="codex-reference-dock">
        Finder Safari Chrome Terminal Codex
      </div>
    </div>
  );
}

function CodexSliceFrame({
  children,
  guided = false,
}: {
  children: ReactNode;
  guided?: boolean;
}) {
  return (
    <div
      className={[
        "codex-reference-frame codex-reference-frame--slice",
        guided ? "codex-reference-frame--guided" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="codex-reference-scroll">
        <div className="codex-reference-prompt">蜻是如何设计的?</div>
        <div className="codex-reference-transcript">{children}</div>
        {guided ? (
          <div className="codex-reference-prompt">
            请你看看 codex 的 rust 源码
          </div>
        ) : null}
      </div>
      <div className="codex-reference-composer">要求和后续变更</div>
    </div>
  );
}
