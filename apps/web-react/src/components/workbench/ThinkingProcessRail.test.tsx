import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
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
      queryGroups: [
        {
          queryInstanceId: "query_exploit_1",
          termGroupKey: "term_group_hidden_1",
          queryRole: "exploit",
          laneType: "exploit",
          queryTerms: ["AI Agent", "RAG", "Python 后端"],
          keywordQuery: "AI Agent AND RAG",
          lifecycle: "executed",
          executionStatus: "completed",
          attempted: true,
          rawCandidateCount: 12,
          uniqueCandidateCount: 9,
          duplicateCandidateCount: 3,
          executions: [
            {
              sourceKind: "liepin",
              status: "completed",
              rawCandidateCount: 12,
              uniqueCandidateCount: 9,
              duplicateCandidateCount: 3,
              safeReasonCode: null,
            },
          ],
        },
        {
          queryInstanceId: "query_explore_1",
          termGroupKey: "term_group_hidden_2",
          queryRole: "explore",
          laneType: "generic_explore",
          queryTerms: ["workflow orchestration", "eval harness"],
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
          text: "覆盖面较好，强匹配候选人集中在平台后端和检索工程方向。",
          terms: ["searched: 42", "scored: 12"],
        },
        {
          title: "关键词",
          text: "旧关键词卡片不应显示",
          terms: ["flattened legacy query"],
        },
        {
          title: "反思和下一轮变更",
          text: "下一轮应增加工作流编排和评测相关关键词。",
          terms: ["drop: 纯前端"],
        },
      ],
    },
  ],
} as AgentWorkbenchThinkingProcess;

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
    sourceKinds: ["liepin"],
    matchScore: 92,
    matchSummary: "Agent 工具调用平台和 RAG 检索链路经验匹配。",
    status: "reviewing",
    detailAvailability: "redacted",
    accessState: "redacted",
    evidenceLevel: "summary",
  },
];

describe("ThinkingProcessRail", () => {
  afterEach(() => cleanup());

  it("switches between WTS candidate and thinking-process tabs", async () => {
    expect.hasAssertions();

    const user = userEvent.setup();
    render(
      <ThinkingProcessRail
        candidates={candidates}
        defaultTab="candidates"
        thinkingProcess={thinkingProcess}
      />,
    );

    expect(screen.getByRole("tab", { name: "候选人" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel", { name: "候选人" })).toHaveTextContent(
      "候选人 A",
    );

    await user.click(screen.getByRole("tab", { name: "思考过程" }));

    const thinkingPanel = screen.getByRole("tabpanel", { name: "思考过程" });
    expect(screen.getByRole("tab", { name: "思考过程" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const queryGroups = within(thinkingPanel).getByRole("group", {
      name: "检索路径",
    });
    expect(
      within(queryGroups).getByRole("group", { name: "主路径" }),
    ).toHaveTextContent("主路径AI Agent、RAG、Python 后端");
    expect(
      within(queryGroups).queryByRole("group", { name: "扩展路径" }),
    ).toBeNull();
    expect(within(queryGroups).queryByText("关键词")).toBeNull();
    expect(within(queryGroups).queryByText("AI Agent AND RAG")).toBeNull();
    expect(within(queryGroups).queryByText("已执行")).toBeNull();
    expect(within(queryGroups).queryByText("计划中")).toBeNull();
    expect(within(queryGroups).queryByText("猎聘")).toBeNull();
    expect(within(queryGroups).queryByText(/原始|新增|重复/)).toBeNull();
    expect(queryGroups.querySelector(".thinking-query-group")).toBeNull();
    expect(within(queryGroups).queryByText("query_exploit_1")).toBeNull();
    expect(within(queryGroups).queryByText("term_group_hidden_1")).toBeNull();
    expect(
      within(thinkingPanel).queryByText("旧关键词卡片不应显示"),
    ).toBeNull();
    expect(within(thinkingPanel).getByText("observation")).toBeInTheDocument();
    expect(
      within(thinkingPanel).getByText("反思和下一轮变更"),
    ).toBeInTheDocument();
  });

  it("renders running and empty thinking-process states without raw runtime payloads", () => {
    expect.hasAssertions();

    const { rerender } = render(
      <ThinkingProcessRail
        candidates={[]}
        defaultTab="thinking"
        thinkingProcess={thinkingProcess}
      />,
    );

    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(
      screen.queryByText(/rawRuntimePayload|RuntimeControlEvent|payload/i),
    ).not.toBeInTheDocument();

    rerender(
      <ThinkingProcessRail
        candidates={[]}
        defaultTab="thinking"
        thinkingProcess={{ activeRoundNo: null, rounds: [] }}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("思考过程尚未生成");
  });

  it("renders blocked and partial round status from BFF metadata", () => {
    expect.hasAssertions();

    render(
      <ThinkingProcessRail
        candidates={[]}
        defaultTab="thinking"
        thinkingProcess={{
          activeRoundNo: null,
          rounds: [
            {
              roundNo: 1,
              status: "blocked",
              queryGroups: [],
              cards: [
                {
                  title: "observation",
                  text: "来源授权阻塞，等待人工处理。",
                  terms: ["source: liepin"],
                },
              ],
            },
            {
              roundNo: 2,
              status: "partial",
              queryGroups: [],
              cards: [
                {
                  title: "反思和下一轮变更",
                  text: "猎聘返回了部分候选人。",
                  terms: ["source: liepin"],
                },
              ],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("已阻塞")).toBeInTheDocument();
    expect(screen.getByText("部分完成")).toBeInTheDocument();
    expect(screen.queryByText("待处理")).not.toBeInTheDocument();
  });

  it("deduplicates terms and renders at most one main and expansion path", () => {
    expect.hasAssertions();

    const firstRound = thinkingProcess.rounds[0];
    if (!firstRound) {
      throw new Error(
        "Expected the thinking-process fixture to include a round.",
      );
    }
    const firstQueryGroup = firstRound.queryGroups[0];
    if (!firstQueryGroup) {
      throw new Error(
        "Expected the thinking-process fixture to include a query group.",
      );
    }
    render(
      <ThinkingProcessRail
        candidates={[]}
        defaultTab="thinking"
        thinkingProcess={{
          activeRoundNo: 1,
          rounds: [
            {
              ...firstRound,
              roundNo: 2,
              queryGroups: [
                {
                  ...firstQueryGroup,
                  queryTerms: [" AI   Agent ", "ai agent", "RAG", "rag"],
                },
                {
                  ...firstQueryGroup,
                  queryInstanceId: "query_exploit_duplicate",
                  queryTerms: ["ignored main"],
                },
                {
                  ...firstQueryGroup,
                  queryInstanceId: "query_prf",
                  laneType: "prf_probe",
                  queryTerms: [
                    "AI Agent",
                    "  Vector   Search ",
                    "vector search",
                  ],
                },
                {
                  ...firstQueryGroup,
                  queryInstanceId: "query_explore_duplicate",
                  laneType: "generic_explore",
                  queryTerms: ["ignored expansion"],
                },
              ],
            },
          ],
        }}
      />,
    );

    expect(screen.getAllByRole("group", { name: /路径/ })).toHaveLength(3);
    expect(screen.getByRole("group", { name: "主路径" })).toHaveTextContent(
      "主路径AI Agent、RAG",
    );
    expect(screen.getByRole("group", { name: "扩展路径" })).toHaveTextContent(
      "扩展路径AI Agent、Vector Search",
    );
    expect(screen.queryByText(/ignored/)).toBeNull();
  });

  it("shows only the main path for a single lane", () => {
    const round = thinkingProcess.rounds[0];
    if (!round) throw new Error("Expected a round.");
    render(
      <ThinkingProcessRail
        candidates={[]}
        defaultTab="thinking"
        thinkingProcess={{
          activeRoundNo: 2,
          rounds: [
            {
              ...round,
              roundNo: 2,
              queryGroups: round.queryGroups.slice(0, 1),
            },
          ],
        }}
      />,
    );

    expect(screen.getByRole("group", { name: "主路径" })).toBeVisible();
    expect(screen.queryByRole("group", { name: "扩展路径" })).toBeNull();
  });
});
