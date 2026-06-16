import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FinalShortlist } from "./FinalShortlist";

describe("FinalShortlist", () => {
  afterEach(() => cleanup());

  it("renders the empty final state", () => {
    expect.hasAssertions();

    render(<FinalShortlist summary={null} />);

    expect(screen.getByRole("status")).toHaveTextContent("最终名单尚未生成");
  });

  it("renders the final safe summary", () => {
    expect.hasAssertions();

    render(
      <FinalShortlist
        summary={{
          summaryId: "summary_001",
          text: "候选人 A 同时匹配 Agent 工具调用平台、RAG 和 Python 后端经验。",
        }}
      />,
    );

    const panel = screen.getByRole("region", { name: "最终候选名单" });
    expect(within(panel).getByText("最终安全摘要")).toBeInTheDocument();
    expect(
      within(panel).getByText(
        "候选人 A 同时匹配 Agent 工具调用平台、RAG 和 Python 后端经验。",
      ),
    ).toBeInTheDocument();
  });
});
