import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";
import { Transcript } from "./Transcript";

const transcriptGroups: AgentWorkbenchTranscriptGroup[] = [
  {
    groupId: "group_run_001",
    title: "已处理 2m",
    status: "running",
    startedAt: "2026-06-13T09:28:03.000Z",
    completedAt: null,
    events: [
      {
        eventId: "evt_requirement_confirmed",
        itemId: "runtime_requirement",
        kind: "runtime.stageChanged",
        status: "completed",
        label: "确认检索需求",
        summary: "目标岗位、地点、经验和排除条件已确认。",
        payload: {
          kind: "runtime_stage",
          summary: "目标岗位、地点、经验和排除条件已确认。",
          sourceRuntimeRunId: "agent_run_001",
        },
        createdAt: "2026-06-13T09:28:08.000Z",
      },
      {
        eventId: "evt_source_search_001",
        itemId: "tool_source_search_001",
        kind: "sourceSearch.completed",
        status: "completed",
        label: "检索候选人来源",
        summary: "42 safe profile summaries matched source filters.",
        payload: {
          kind: "operation",
          activityId: "activity_source_search",
          itemId: "tool_source_search_001",
          sourceRuntimeRunId: "agent_run_001",
          summary: "42 profiles found, 12 moved to scoring.",
        },
        createdAt: "2026-06-13T09:29:18.000Z",
      },
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
    ],
  },
  {
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
          missingFromSeq: 1,
          nextAvailableSeq: 9,
        },
        createdAt: "2026-06-13T09:30:10.000Z",
      },
    ],
  },
];

describe("Transcript", () => {
  afterEach(() => cleanup());

  it("renders Codex-style run groups and keeps collapsed groups compact", async () => {
    expect.hasAssertions();

    const user = userEvent.setup();
    render(
      <Transcript
        defaultCollapsedGroupIds={["group_run_001"]}
        groups={transcriptGroups}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    const groupToggle = within(transcript).getByRole("button", {
      name: /已处理 2m/,
    });

    expect(groupToggle).toHaveAttribute("aria-expanded", "false");
    expect(
      within(transcript).queryByText("检索候选人来源"),
    ).not.toBeInTheDocument();

    await user.click(groupToggle);

    expect(groupToggle).toHaveAttribute("aria-expanded", "true");
    expect(within(transcript).getByText("确认检索需求")).toBeInTheDocument();
    expect(within(transcript).getByText("检索候选人来源")).toBeInTheDocument();
    expect(within(transcript).getByText("Agent response")).toBeInTheDocument();
  });

  it("expands and collapses structured operation event details", async () => {
    expect.hasAssertions();

    const user = userEvent.setup();
    render(<Transcript groups={transcriptGroups} />);

    const toolRow = screen.getByRole("article", { name: /检索候选人来源/ });
    const expandButton = within(toolRow).getByRole("button", {
      name: "展开检索候选人来源详情",
    });

    expect(
      within(toolRow).queryByText("tool_source_search_001"),
    ).not.toBeInTheDocument();

    await user.click(expandButton);

    expect(
      within(toolRow).getByText("tool_source_search_001"),
    ).toBeInTheDocument();
    expect(
      within(toolRow).getByText("42 profiles found, 12 moved to scoring."),
    ).toBeInTheDocument();

    await user.click(
      within(toolRow).getByRole("button", { name: "收起检索候选人来源详情" }),
    );

    expect(
      within(toolRow).queryByText("tool_source_search_001"),
    ).not.toBeInTheDocument();
  });

  it("renders context compaction as a divider cell", () => {
    expect.hasAssertions();

    render(<Transcript groups={transcriptGroups} />);

    const divider = screen.getByRole("separator", { name: "上下文已压缩" });
    expect(divider).toHaveTextContent("上下文已压缩");
    expect(divider).toHaveTextContent(
      "已保留需求、检索策略、候选人摘要和审批状态。",
    );
  });
});
