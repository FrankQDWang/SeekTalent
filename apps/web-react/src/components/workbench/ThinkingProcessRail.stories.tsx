import type { Meta, StoryObj } from "@storybook/react-vite";
import type { AgentWorkbenchConversationResponse } from "../../lib/api/agentWorkbenchTypes";
import {
  agentWorkbenchMultiRoundThinkingViewFixture,
  agentWorkbenchRunningViewFixture,
} from "../../test/fixtures/agentWorkbenchBff";
import { ThinkingProcessRail } from "./ThinkingProcessRail";

function ThinkingProcessRailStory({
  compactDualLane = false,
  empty = false,
  tab = "thinking",
}: {
  compactDualLane?: boolean;
  empty?: boolean;
  tab?: "candidates" | "thinking";
}) {
  const view = empty
    ? {
        ...agentWorkbenchRunningViewFixture,
        thinkingProcess: {
          activeRoundNo: null,
          rounds: [],
        },
      }
    : compactDualLane
      ? compactDualLaneThinkingView
      : agentWorkbenchMultiRoundThinkingViewFixture;

  return (
    <div
      style={{
        background: "#eef3ff",
        display: "grid",
        minHeight: "100vh",
        placeItems: "stretch end",
      }}
    >
      <div
        style={{
          maxWidth: "100vw",
          minHeight: "100vh",
          width: compactDualLane ? "100%" : 360,
        }}
      >
        <ThinkingProcessRail
          candidates={view.candidates}
          defaultTab={tab}
          thinkingProcess={view.thinkingProcess}
        />
      </div>
    </div>
  );
}

const compactDualLaneThinkingView: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchMultiRoundThinkingViewFixture,
  thinkingProcess: {
    activeRoundNo: 1,
    rounds: [
      {
        cards: [],
        queryGroups: [
          {
            attempted: true,
            duplicateCandidateCount: 37,
            executionStatus: "completed",
            executions: [
              {
                duplicateCandidateCount: 37,
                rawCandidateCount: 128,
                safeReasonCode: null,
                sourceKind: "liepin",
                status: "completed",
                uniqueCandidateCount: 91,
              },
            ],
            keywordQuery:
              "Agentic retrieval orchestration AND long-form evaluation systems",
            laneType: "exploit",
            lifecycle: "executed",
            queryInstanceId: "query_mobile_compact_main",
            queryRole: "exploit",
            queryTerms: [
              "production-grade retrieval orchestration",
              "long-context evaluation systems",
            ],
            rawCandidateCount: 128,
            termGroupKey: "term_group_mobile_compact_main",
            uniqueCandidateCount: 91,
          },
          {
            attempted: false,
            duplicateCandidateCount: 0,
            executionStatus: null,
            executions: [],
            keywordQuery:
              "Cross-functional platform reliability and policy enforcement",
            laneType: "prf_probe",
            lifecycle: "planned",
            queryInstanceId: "query_mobile_compact_probe",
            queryRole: "probe",
            queryTerms: ["cross-functional orchestration governance"],
            rawCandidateCount: 0,
            termGroupKey: "term_group_mobile_compact_probe",
            uniqueCandidateCount: 0,
          },
        ],
        roundNo: 1,
        status: "running",
      },
    ],
  },
};

const meta = {
  title: "Workbench/ThinkingProcessRail",
  component: ThinkingProcessRailStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ThinkingProcessRailStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Running: Story = {};

export const RoundTimeline: Story = {};

export const DualLaneCompactMobile: Story = {
  args: {
    compactDualLane: true,
  },
};

export const CandidatesTab: Story = {
  args: {
    tab: "candidates",
  },
};

export const EmptyThinking: Story = {
  args: {
    empty: true,
  },
};
