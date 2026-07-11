import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  WorkbenchV2ConversationView,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import {
  ConversationScreenV2,
  ConversationScreenV2Side,
} from "./ConversationScreenV2";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConversationScreenV2", () => {
  it("renders pure chat without switching to the old workflow screen", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_user",
              step: 1,
              type: "user_message",
              payload: { text: "你好" },
            }),
            transcriptEvent({
              eventId: "event_assistant",
              step: 2,
              type: "assistant_message",
              role: "assistant",
              payload: { text: "可以，先告诉我招聘目标。" },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("你好")).toBeVisible();
    expect(screen.getByText("可以，先告诉我招聘目标。")).toBeVisible();
    expect(screen.queryByText(/已处理/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认需求" }),
    ).not.toBeInTheDocument();
  });

  it("keeps requirement forms in the transcript and wires actions", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onRequirementAction = vi.fn();

    render(
      <ConversationScreenV2
        onRequirementAction={onRequirementAction}
        view={conversationView({
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_requirement",
              step: 1,
              type: "requirement_form",
              role: "assistant",
              payload: requirementPayload(),
            }),
          ],
        })}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(
      within(transcript).getByRole("region", { name: "需求确认" }),
    ).toBeVisible();

    await user.click(
      within(transcript).getByRole("checkbox", { name: /Python 后端经验/ }),
    );

    expect(onRequirementAction).toHaveBeenCalledWith({
      action: "set_selected",
      itemId: "item_python",
      selected: false,
    });
  });

  it("keeps draft supplemental text when a checkbox update replaces the requirement form event", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onRequirementAction = vi.fn(() => Promise.resolve());
    const firstRequirementEvent = transcriptEvent({
      eventId: "event_requirement_1",
      step: 1,
      type: "requirement_form",
      role: "assistant",
      payload: requirementPayload(),
    });
    const revisedPayload = requirementPayload();
    const revisedFirstSection = revisedPayload.draft.sections[0];
    const revisedFirstItem = revisedFirstSection?.items[0];
    if (revisedFirstItem === undefined) {
      throw new Error("Requirement fixture is missing its first item");
    }
    revisedFirstItem.selected = false;
    const revisedRequirementEvent = transcriptEvent({
      eventId: "event_requirement_2",
      step: 2,
      type: "requirement_form",
      role: "assistant",
      payload: revisedPayload,
    });

    const { rerender } = render(
      <ConversationScreenV2
        onRequirementAction={onRequirementAction}
        view={conversationView({
          transcriptEvents: [firstRequirementEvent],
        })}
      />,
    );

    await user.type(
      screen.getByLabelText("补充其他要求"),
      "需要熟悉 AI 编程工具",
    );
    await user.click(screen.getByRole("checkbox", { name: /Python 后端经验/ }));

    rerender(
      <ConversationScreenV2
        onRequirementAction={onRequirementAction}
        view={conversationView({
          transcriptEvents: [firstRequirementEvent, revisedRequirementEvent],
        })}
      />,
    );

    expect(screen.getByLabelText("补充其他要求")).toHaveValue(
      "需要熟悉 AI 编程工具",
    );
  });

  it("keeps runtime progress inside the transcript without exposing internal runtime side state", () => {
    expect.hasAssertions();
    const view = conversationView({
      conversation: conversationSummary({
        runtimeState: "running",
        runtimeRunId: "run_123",
      }),
      runtime: { state: "running", runtimeRunId: "run_123" },
      transcriptEvents: [
        transcriptEvent({
          eventId: "event_progress",
          step: 1,
          type: "runtime_progress",
          role: "runtime",
          payload: { summary: "正在检索候选人" },
        }),
      ],
    });

    render(<ConversationScreenV2 view={view} />);

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(within(transcript).getByText("运行进度")).toBeVisible();
    expect(within(transcript).getByText("正在检索候选人")).toBeVisible();
    expect(
      screen.queryByRole("complementary", { name: "运行状态" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("run_123")).not.toBeInTheDocument();
  });

  it("shows optimistic submitted turns in the transcript while a request is pending", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        optimisticEvents={[
          transcriptEvent({
            eventId: "optimistic_user",
            step: 2,
            type: "user_message",
            status: "pending",
            payload: { text: "现在进度如何" },
          }),
          transcriptEvent({
            eventId: "optimistic_status",
            step: 3,
            type: "assistant_status",
            role: "assistant",
            status: "running",
            payload: { summary: "正在思考" },
          }),
        ]}
        submittingMessage
        view={conversationView()}
      />,
    );

    const transcript = screen.getByRole("region", { name: "Agent transcript" });
    expect(within(transcript).getByText("现在进度如何")).toBeVisible();
    expect(within(transcript).getByText("正在思考")).toBeVisible();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "处理中" }),
    ).not.toBeInTheDocument();
  });

  it("shows the strategy graph product surface after runtime starts", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          conversation: conversationSummary({
            runtimeState: "running",
            runtimeRunId: "run_123",
          }),
          runtime: { state: "running", runtimeRunId: "run_123" },
          ...workflowSurface("招聘流程运行中，当前阶段：候选人检索。"),
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: requirementPayload(),
            }),
            transcriptEvent({
              eventId: "event_progress",
              step: 2,
              type: "runtime_progress",
              role: "runtime",
              payload: { summary: "招聘流程运行中，当前阶段：候选人检索。" },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
    expect(
      screen.queryByRole("complementary", { name: "运行状态" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("run_123")).not.toBeInTheDocument();
  });

  it("renders workflow surfaces from the BFF view instead of local fixed projections", () => {
    expect.hasAssertions();
    const view = conversationView({
      conversation: conversationSummary({
        runtimeState: "running",
        runtimeRunId: "run_123",
      }),
      runtime: { state: "running", runtimeRunId: "run_123" },
      strategyGraph: {
        nodes: [
          {
            nodeId: "backend-fact",
            kind: "phase",
            label: "后端事实节点",
            summary: "后端投影的真实进度",
            roundNo: 1,
            laneType: null,
            phase: "source_result",
            stage: "source_result",
            status: "running",
            sourceKind: "liepin",
            activityId: null,
            messageId: null,
          },
        ],
        edges: [],
      },
      thinkingProcess: {
        activeRoundNo: 1,
        rounds: [
          {
            roundNo: 1,
            status: "running",
            queryGroups: [],
            cards: [
              {
                title: "observation",
                text: "右侧来自 BFF 的真实思考过程",
                terms: [],
              },
            ],
          },
        ],
      },
      candidates: [],
    });

    render(
      <>
        <ConversationScreenV2 view={view} />
        <ConversationScreenV2Side view={view} />
      </>,
    );

    expect(screen.getByText("后端投影的真实进度")).toBeVisible();
    expect(screen.getByText("右侧来自 BFF 的真实思考过程")).toBeVisible();
    expect(
      screen.queryByText("从可用招聘来源检索候选人。"),
    ).not.toBeInTheDocument();
  });

  it("shows the WTS right rail for workflow conversations with only candidate and thinking tabs", () => {
    expect.hasAssertions();

    const view = conversationView({
      ...workflowSurface("招聘流程运行中，当前阶段：候选人检索。"),
      candidates: [candidateSummary()],
      transcriptEvents: [
        transcriptEvent({
          eventId: "event_confirmed",
          step: 1,
          type: "requirement_form_confirmed",
          role: "assistant",
          payload: requirementPayload(),
        }),
      ],
    });

    render(
      <>
        <ConversationScreenV2 view={view} />
        <ConversationScreenV2Side view={view} />
      </>,
    );

    expect(screen.getByRole("region", { name: "对话" })).toBeVisible();
    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "运行右栏" }),
    ).toBeVisible();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "候选人",
      "思考过程",
    ]);
    expect(screen.getByRole("tab", { name: "候选人" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.queryByRole("complementary", { name: "运行状态" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("run_123")).not.toBeInTheDocument();
  });

  it("shows the WTS thinking tab by default when workflow conversations have no candidates", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2Side
        view={conversationView({
          ...workflowSurface("招聘流程运行中，当前阶段：候选人检索。"),
          candidates: [],
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: requirementPayload(),
            }),
          ],
        })}
      />,
    );

    expect(screen.getByRole("tab", { name: "思考过程" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps the WTS right rail visible after requirement confirmation with the exact empty thinking copy", () => {
    expect.hasAssertions();

    const view = conversationView({
      transcriptEvents: [
        transcriptEvent({
          eventId: "event_confirmed",
          step: 1,
          type: "requirement_form_confirmed",
          role: "assistant",
          payload: requirementPayload(),
        }),
      ],
    });

    render(
      <>
        <ConversationScreenV2 view={view} />
        <ConversationScreenV2Side view={view} />
      </>,
    );

    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "运行右栏" }),
    ).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(/^思考过程尚未生成$/);
    expect(
      screen.queryByRole("complementary", { name: "运行状态" }),
    ).not.toBeInTheDocument();
  });

  it("renders distinct WTS query groups without exposing internal identifiers", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2Side
        view={conversationView({
          thinkingProcess: {
            activeRoundNo: 2,
            rounds: [
              {
                roundNo: 2,
                status: "running",
                queryGroups: [
                  {
                    queryInstanceId: "query_exploit_1",
                    termGroupKey: "term_group_hidden_1",
                    queryRole: "exploit",
                    laneType: "exploit",
                    queryTerms: ["AI agent", "LLM"],
                    keywordQuery: "AI agent AND LLM",
                    lifecycle: "executed",
                    executionStatus: "completed",
                    attempted: true,
                    rawCandidateCount: 10,
                    uniqueCandidateCount: 7,
                    duplicateCandidateCount: 3,
                    executions: [],
                  },
                  {
                    queryInstanceId: "query_probe_1",
                    termGroupKey: "term_group_hidden_2",
                    queryRole: "probe",
                    laneType: "prf_probe",
                    queryTerms: ["RAG evaluation"],
                    keywordQuery: null,
                    lifecycle: "planned",
                    executionStatus: null,
                    attempted: false,
                    rawCandidateCount: 0,
                    uniqueCandidateCount: 0,
                    duplicateCandidateCount: 0,
                    executions: [],
                  },
                ],
                cards: [
                  {
                    title: "observation",
                    text: "初次搜索拿到 10 位新候选人。",
                    terms: [],
                  },
                  {
                    title: "反思和下一轮变更",
                    text: "下一轮加入 LangChain 和 RAG。",
                    terms: [],
                  },
                ],
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByRole("heading", { name: "第 2 轮" })).toBeVisible();
    const queryGroups = screen.getByRole("group", { name: "检索路径" });
    expect(
      within(queryGroups).getByRole("group", { name: "主路径" }),
    ).toHaveTextContent("主路径AI agent、LLM");
    expect(
      within(queryGroups).getByRole("group", { name: "扩展路径" }),
    ).toHaveTextContent("扩展路径RAG evaluation");
    expect(within(queryGroups).queryByText("关键词")).toBeNull();
    expect(within(queryGroups).queryByText("AI agent AND LLM")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "observation（结果）" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "反思和下一轮变更" }),
    ).toBeVisible();
    expect(screen.queryByText("query_exploit_1")).toBeNull();
    expect(screen.queryByText("term_group_hidden_1")).toBeNull();
    expect(screen.queryByRole("heading", { name: "reflection" })).toBeNull();
  });

  it("expands the strategy graph surface as soon as requirements are confirmed", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          ...workflowSurface("需求已确认，正在排队启动检索。"),
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: {
                ...requirementPayload(),
                readonly: true,
              },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
  });

  it("renders a strategy placeholder when confirmed requirements arrive before graph nodes", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          conversation: conversationSummary({
            runtimeState: "queued",
            runtimeRunId: "run_queued",
          }),
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: {
                ...requirementPayload(),
                readonly: true,
              },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
    expect(screen.getByText("先聊一下候选人搜索")).toBeVisible();
  });

  it("uses the confirmed runtime job title for the strategy graph root after pure chat", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          conversation: conversationSummary({
            title: "你好",
            runtimeState: "queued",
            runtimeRunId: "run_queued",
          }),
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: {
                ...requirementPayload(),
                readonly: true,
                runtimeInput: {
                  jobTitle: "数据科学家",
                  jd: "负责业务指标体系建设、SQL/Python 数据分析和 A/B Testing。",
                  notes: "base 杭州",
                },
              },
            }),
          ],
        })}
      />,
    );

    const strategyGraph = screen.getByRole("region", { name: "检索策略图" });
    expect(within(strategyGraph).getByText("数据科学家")).toBeVisible();
    expect(within(strategyGraph).queryByText("你好")).not.toBeInTheDocument();
  });

  it("does not let empty runtime results pollute the strategy graph summary", () => {
    expect.hasAssertions();

    render(
      <ConversationScreenV2
        view={conversationView({
          ...workflowSurface("需求已确认，正在排队启动检索。"),
          transcriptEvents: [
            transcriptEvent({
              eventId: "event_confirmed",
              step: 1,
              type: "requirement_form_confirmed",
              role: "assistant",
              payload: {
                ...requirementPayload(),
                readonly: true,
              },
            }),
            transcriptEvent({
              eventId: "event_result_empty",
              step: 2,
              type: "runtime_result",
              role: "runtime",
              payload: {
                state: "idle",
                summary: "当前还没有运行结果。",
              },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "检索策略图" })).toBeVisible();
    expect(screen.queryByText("当前还没有运行结果。")).not.toBeInTheDocument();
  });

  it("submits a generic message through the composer", async () => {
    expect.hasAssertions();
    const user = userEvent.setup();
    const onSubmitMessage = vi.fn(() => Promise.resolve());

    render(
      <ConversationScreenV2
        onSubmitMessage={onSubmitMessage}
        view={conversationView()}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("输入消息、JD 或下一步招聘需求"),
      "继续帮我找候选人",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onSubmitMessage).toHaveBeenCalledWith("继续帮我找候选人");
  });
});

function conversationView(
  overrides: Partial<WorkbenchV2ConversationView> = {},
): WorkbenchV2ConversationView {
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: conversationSummary(),
    transcriptEvents: [transcriptEvent()],
    requirementForm: null,
    runtime: null,
    ...overrides,
  };
}

function conversationSummary(
  overrides: Partial<WorkbenchV2ConversationView["conversation"]> = {},
): WorkbenchV2ConversationView["conversation"] {
  return {
    conversationId: "agentv2_1",
    title: "先聊一下候选人搜索",
    runtimeState: "idle",
    runtimeRunId: null,
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    updatedAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function transcriptEvent(
  overrides: Partial<WorkbenchV2TranscriptEvent> = {},
): WorkbenchV2TranscriptEvent {
  return {
    eventId: "event_1",
    step: 1,
    type: "user_message",
    role: "user",
    status: "completed",
    payload: { text: "先聊一下候选人搜索" },
    createdAt: "2026-06-25T01:02:03.000004+00:00",
    ...overrides,
  };
}

function requirementPayload() {
  return {
    draft: {
      sections: [
        {
          section_id: "core",
          display_name: "核心条件",
          items: [
            {
              item_id: "item_python",
              text: "Python 后端经验",
              selected: true,
              allowed_actions: ["set_selected"],
              status: "active",
            },
          ],
        },
      ],
      other_input_prompt: "补充其他要求",
      can_confirm: true,
    },
  };
}

function candidateSummary(): NonNullable<
  WorkbenchV2ConversationView["candidates"]
>[number] {
  return {
    candidateId: "candidate_001",
    rank: 1,
    displayName: "候选人 A",
    headline: "资深体验设计工程师",
    company: "小米科技",
    location: "上海",
    education: "本科",
    experienceYears: 10,
    sourceKinds: ["liepin"],
    matchScore: 90,
    matchSummary: "交互设计功底扎实，能独立负责大型复杂项目的设计。",
    status: "reviewing",
    detailAvailability: "redacted",
    accessState: "redacted",
    evidenceLevel: "summary",
  };
}

function workflowSurface(
  summary: string,
): Pick<
  WorkbenchV2ConversationView,
  "strategyGraph" | "thinkingProcess" | "candidates"
> {
  return {
    strategyGraph: {
      nodes: [
        {
          nodeId: "backend-requirements",
          kind: "requirements",
          label: "需求确认",
          summary: "数据科学家",
          roundNo: null,
          laneType: null,
          phase: null,
          stage: null,
          status: "completed",
          sourceKind: "all",
          activityId: null,
          messageId: null,
        },
        {
          nodeId: "backend-source",
          kind: "phase",
          label: "候选人检索",
          summary,
          roundNo: 1,
          laneType: null,
          phase: "source_result",
          stage: "source_result",
          status: "running",
          sourceKind: "liepin",
          activityId: null,
          messageId: null,
        },
      ],
      edges: [
        {
          edgeId: "backend-requirements->backend-source",
          fromNodeId: "backend-requirements",
          toNodeId: "backend-source",
          label: null,
        },
      ],
    },
    thinkingProcess: {
      activeRoundNo: 1,
      rounds: [
        {
          roundNo: 1,
          status: "running",
          queryGroups: [],
          cards: [
            {
              title: "observation",
              text: summary,
              terms: [],
            },
          ],
        },
      ],
    },
    candidates: [],
  };
}
