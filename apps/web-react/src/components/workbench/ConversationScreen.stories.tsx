import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchArchivedViewFixture,
  agentWorkbenchCompletedViewFixture,
  agentWorkbenchFailedViewFixture,
  agentWorkbenchInitialViewFixture,
  agentWorkbenchPermissionDeniedViewFixture,
  agentWorkbenchRequirementReviewViewFixture,
  agentWorkbenchRunningViewFixture,
  agentWorkbenchSourceExpiredViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "./ConversationScreen";
import { ConversationShell } from "./ConversationShell";

const firstTurnThinkingView = {
  ...agentWorkbenchRequirementReviewViewFixture,
  conversation: {
    ...agentWorkbenchRequirementReviewViewFixture.conversation,
    workflowStartState: "not_started" as const,
  },
  requirementDraft: null,
  pendingActions: {
    ...agentWorkbenchRequirementReviewViewFixture.pendingActions,
    allowed: ["submit_message"],
    pendingRequirementReviewCount: 0,
    primary: null,
  },
  transcriptGroups: [
    {
      completedAt: null,
      events: [
        {
          createdAt: "2026-06-13T09:30:00.000Z",
          eventId: "message:first_turn:completed",
          itemId: "msg_first_turn",
          kind: "message.completed" as const,
          label: "User message",
          payload: {
            kind: "message" as const,
            messageId: "msg_first_turn",
          },
          status: "completed" as const,
          summary:
            "上海 AI Agent 平台工程师，要求 Python 后端、RAG 和 workflow orchestration。",
        },
        {
          createdAt: "2026-06-13T09:30:04.000Z",
          eventId: "operation:extract_requirements:started",
          itemId: "operation_extract_requirements",
          kind: "operation.started" as const,
          label: "正在处理需求",
          payload: {
            kind: "operation" as const,
            itemId: "operation_extract_requirements",
            summary: "正在思考",
          },
          status: "running" as const,
          summary: "正在思考",
        },
      ],
      groupId: "conversation:agent_conv_first_turn:segment:1",
      startedAt: "2026-06-13T09:30:00.000Z",
      status: "running" as const,
      title: "已处理",
    },
  ],
};

const requirementReviewLongContentView = {
  ...agentWorkbenchRequirementReviewViewFixture,
  requirementDraft: longRequirementDraft(),
};

const postConfirmGraphView = {
  ...agentWorkbenchRequirementReviewViewFixture,
  candidates: agentWorkbenchRunningViewFixture.candidates,
  conversation: {
    ...agentWorkbenchRequirementReviewViewFixture.conversation,
    workflowStartState: "queued" as const,
  },
  pendingActions: {
    ...agentWorkbenchRequirementReviewViewFixture.pendingActions,
    allowed: ["submit_message"],
    pendingRequirementReviewCount: 0,
    primary: "workflow_start_queued",
  },
  requirementDraft: null,
  strategyGraph: agentWorkbenchRunningViewFixture.strategyGraph,
  thinkingProcess: agentWorkbenchRunningViewFixture.thinkingProcess,
};

const longTranscriptAndGraphView = {
  ...agentWorkbenchRunningViewFixture,
  transcriptGroups: longTranscriptGroups(),
};

function longRequirementDraft() {
  const draft = agentWorkbenchRequirementReviewViewFixture.requirementDraft;
  if (draft === null) {
    throw new Error("Requirement review story fixture must include a draft.");
  }
  return {
    ...draft,
    sections: draft.sections.map((section) => ({
      ...section,
      items: section.items.map((item, index) => ({
        ...item,
        text:
          index === 0
            ? "必须同时具备 AI Agent 平台工程、RAG 检索链路、workflow orchestration、生产可观测性、跨团队推动落地，以及能解释复杂技术取舍的沟通能力"
            : item.text,
      })),
    })),
  };
}

function longTranscriptGroups() {
  return Array.from({ length: 4 }).flatMap((_, groupIndex) =>
    agentWorkbenchRunningViewFixture.transcriptGroups.map((group) => {
      const copyId = String(groupIndex);
      return {
        ...group,
        groupId: `${group.groupId}:copy:${copyId}`,
        events: group.events.map((event) => ({
          ...event,
          eventId: `${event.eventId}:copy:${copyId}`,
          itemId: `${event.itemId}:copy:${copyId}`,
        })),
      };
    }),
  );
}

const meta = {
  title: "Workbench/ConversationScreen",
  component: ConversationScreen,
  parameters: {
    layout: "fullscreen",
  },
  decorators: [
    (Story) => (
      <div style={{ minHeight: "100vh" }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ConversationScreen>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Initial: Story = {
  args: {
    view: agentWorkbenchInitialViewFixture,
  },
};

export const RequirementReview: Story = {
  args: {
    view: agentWorkbenchRequirementReviewViewFixture,
  },
};

export const FirstTurnThinking: Story = {
  args: {
    view: firstTurnThinkingView,
  },
};

export const RequirementReviewLongContent: Story = {
  args: {
    view: requirementReviewLongContentView,
  },
};

export const PostConfirmGraph: Story = {
  args: {
    view: postConfirmGraphView,
  },
};

export const LongTranscriptAndGraph: Story = {
  args: {
    view: longTranscriptAndGraphView,
  },
};

export const RunningWithStream: Story = {
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};

export const SourceExpired: Story = {
  args: {
    view: agentWorkbenchSourceExpiredViewFixture,
  },
};

export const PermissionDenied: Story = {
  args: {
    view: agentWorkbenchPermissionDeniedViewFixture,
  },
};

export const Failed: Story = {
  args: {
    view: agentWorkbenchFailedViewFixture,
  },
};

export const Completed: Story = {
  args: {
    view: agentWorkbenchCompletedViewFixture,
  },
};

export const Archived: Story = {
  args: {
    view: agentWorkbenchArchivedViewFixture,
  },
};

export const ResizableLayout: Story = {
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};

export const WorkbenchShellComposed: Story = {
  render: ({ view }) => (
    <ConversationShell
      main={<ConversationScreen view={view} />}
      rail={
        <nav aria-label="会话">
          <strong>Wide Talent Search</strong>
          <span>{view.conversation.title}</span>
        </nav>
      }
      side={<ConversationScreenSide view={view} />}
    />
  ),
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};
