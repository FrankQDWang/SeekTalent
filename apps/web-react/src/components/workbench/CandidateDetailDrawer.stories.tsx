import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  agentWorkbenchCandidateApprovalRequiredDetailFixture,
  agentWorkbenchCandidateDetailFixture,
  agentWorkbenchRunningViewFixture,
  wtsStoryConversationSummariesFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { CandidateDetailDrawer } from "./CandidateDetailDrawer";
import { ConversationList } from "./ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
} from "./ConversationScreen";
import { ConversationShell } from "./ConversationShell";

const candidate = agentWorkbenchRunningViewFixture.candidates[0] ?? null;

function CandidateDetailDrawerStory({
  mode = "summary",
}: {
  mode?: "approval" | "error" | "loading" | "summary";
}) {
  const detail =
    mode === "approval"
      ? agentWorkbenchCandidateApprovalRequiredDetailFixture
      : mode === "summary"
        ? agentWorkbenchCandidateDetailFixture
        : null;

  return (
    <>
      <ConversationShell
        main={<ConversationScreen view={agentWorkbenchRunningViewFixture} />}
        rail={
          <ConversationList
            conversations={wtsStoryConversationSummariesFixture}
            selectedConversationId="agent_conv_001"
          />
        }
        side={
          <ConversationScreenSide
            defaultTab="candidates"
            view={agentWorkbenchRunningViewFixture}
          />
        }
      />
      <CandidateDetailDrawer
        candidate={candidate}
        detail={detail}
        errorMessage="候选人详情读取失败，请稍后重试。"
        onClose={() => undefined}
        onRetry={() => undefined}
        open
        status={
          mode === "loading" ? "loading" : mode === "error" ? "error" : "ready"
        }
      />
    </>
  );
}

const meta = {
  title: "Workbench/CandidateDetailDrawer",
  component: CandidateDetailDrawerStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof CandidateDetailDrawerStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Summary: Story = {};

export const ApprovalRequired: Story = {
  args: {
    mode: "approval",
  },
};

export const Loading: Story = {
  args: {
    mode: "loading",
  },
};

export const Error: Story = {
  args: {
    mode: "error",
  },
};
