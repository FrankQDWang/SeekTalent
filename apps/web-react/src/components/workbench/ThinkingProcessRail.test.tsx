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
    expect(within(thinkingPanel).getByText("关键词")).toBeInTheDocument();
    expect(within(thinkingPanel).getByText("observation")).toBeInTheDocument();
    expect(
      within(thinkingPanel).getByText("反思和下一轮变更"),
    ).toBeInTheDocument();
    expect(
      within(thinkingPanel).getByText("workflow orchestration"),
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
});
