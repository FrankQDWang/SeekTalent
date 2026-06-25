import { expect, test } from "@playwright/test";
import { agentWorkbenchRequirementReviewViewFixture } from "../src/test/fixtures/agentWorkbenchBff";
import { failOnPageProblems } from "./pageProblems";

test.use({
  viewport: { width: 900, height: 800 },
});

test.beforeEach(({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop-chromium",
    "Desktop first-turn gate runs on the desktop project.",
  );
  failOnPageProblems(page);
});

test("starts clean v2 first-turn conversations without legacy JD routes", async ({
  page,
}) => {
  const createRequests: Record<string, unknown>[] = [];
  const firstTurnProgressSnapshot = v2FirstTurnSnapshot("agentv2_created_1");
  const secondFirstTurnSnapshot = v2FirstTurnSnapshot("agentv2_created_2");
  const queuedWorkflowSnapshot = queuedSnapshot(
    "agent_conv_queued_empty_graph",
  );
  const snapshots = new Map<string, unknown>([
    ["agentv2_created_1", firstTurnProgressSnapshot],
    ["agentv2_created_2", secondFirstTurnSnapshot],
    ["agent_conv_queued_empty_graph", queuedWorkflowSnapshot],
  ]);

  await page.route("**/api/agent/workbench/conversations", async (route) => {
    if (route.request().method() === "POST") {
      throw new Error(
        "Workbench v2 start must not use the old Workbench create route.",
      );
    }
    await route.fulfill({
      contentType: "application/json",
      json: { conversations: [] },
    });
  });
  await page.route("**/api/agent/workbench/conversations/from-jd", (route) => {
    throw new Error(
      `Workbench v2 start must not call legacy from-jd route: ${route.request().url()}`,
    );
  });
  await page.route("**/api/agent/workbench/v2/conversations", async (route) => {
    if (route.request().method() === "POST") {
      const request = route.request().postDataJSON() as Record<string, unknown>;
      createRequests.push(request);
      const snapshot =
        createRequests.length === 1
          ? firstTurnProgressSnapshot
          : secondFirstTurnSnapshot;
      await route.fulfill({
        contentType: "application/json",
        json: snapshot,
        status: 201,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: {
        schemaVersion: "agent.workbench.v2.list",
        conversations: [...snapshots.values()]
          .filter(isWorkbenchV2Snapshot)
          .map((snapshot) => ({
            conversationId: snapshot.conversation.conversationId,
            title: snapshot.conversation.title,
            status: snapshot.conversation.runtimeState,
            updatedAt: snapshot.conversation.updatedAt,
          })),
      },
    });
  });
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_*",
    async (route) => {
      const conversationId = route.request().url().split("/").at(-1) ?? "";
      await route.fulfill({
        contentType: "application/json",
        json: snapshots.get(conversationId),
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_*",
    async (route) => {
      const conversationId = route.request().url().split("/").at(-1) ?? "";
      await route.fulfill({
        contentType: "application/json",
        json: snapshots.get(conversationId),
      });
    },
  );
  await page.route("**/events/stream?*", async (route) => {
    await route.fulfill({
      contentType: "text/event-stream",
      body: "",
    });
  });

  const jobDescription =
    "上海 AI Agent 平台工程师，要求 Python 后端、RAG 和 workflow orchestration。";

  await page.goto("/");
  await page.getByLabel("消息、JD 或招聘需求").fill(jobDescription);
  await page.getByLabel("消息、JD 或招聘需求").press("Enter");

  await expect.poll(() => createRequests.length).toBe(1);
  expect(createRequests[0]).toMatchObject({
    message: jobDescription,
  });
  expect(createRequests[0]).not.toHaveProperty("sourceKinds");
  expect(typeof createRequests[0].idempotencyKey).toBe("string");
  await expect(page).toHaveURL(/\/conversations\/agentv2_created_1$/);
  await expect(page.getByLabel("Agent transcript")).toContainText("正在思考");
  await expect(page.getByText("已处理")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "确认需求" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "检索策略图" })).toHaveCount(0);

  await page.goto("/");
  await page.getByLabel("消息、JD 或招聘需求").fill(jobDescription);
  await page.getByLabel("消息、JD 或招聘需求").press("Enter");

  await expect.poll(() => createRequests.length).toBe(2);
  expect(createRequests[1]).toMatchObject({
    message: jobDescription,
  });
  await expect(page).toHaveURL(/\/conversations\/agentv2_created_2$/);
  await expect(page.getByRole("button", { name: "确认需求" })).toHaveCount(0);

  await page.goto("/conversations/agent_conv_queued_empty_graph");
  await expect(page.getByRole("region", { name: "检索策略图" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => {
        const shell = document.querySelector(".conversation-shell");
        return shell?.scrollWidth ?? 0;
      }),
    )
    .toBeGreaterThan(900);
});

function v2FirstTurnSnapshot(conversationId: string) {
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: {
      conversationId,
      title: "AI Agent 平台工程师",
      runtimeState: "idle",
      runtimeRunId: null,
      createdAt: "2026-06-13T09:30:00.000Z",
      updatedAt: "2026-06-13T09:30:04.000Z",
    },
    transcriptEvents: [
      {
        createdAt: "2026-06-13T09:30:00.000Z",
        eventId: `${conversationId}:message:first_turn`,
        payload: {
          text: "上海 AI Agent 平台工程师，要求 Python 后端、RAG 和 workflow orchestration。",
        },
        role: "user",
        status: "completed",
        step: 1,
        type: "user_message",
      },
      {
        createdAt: "2026-06-13T09:30:04.000Z",
        eventId: `${conversationId}:status:extract_requirements`,
        payload: { summary: "正在思考" },
        role: "assistant",
        status: "running",
        step: 2,
        type: "assistant_status",
      },
    ],
    requirementForm: null,
    runtime: null,
  };
}

function isWorkbenchV2Snapshot(snapshot: unknown): snapshot is {
  conversation: {
    conversationId: string;
    runtimeState: string;
    title: string;
    updatedAt: string;
  };
} {
  return (
    typeof snapshot === "object" &&
    snapshot !== null &&
    "schemaVersion" in snapshot &&
    snapshot.schemaVersion === "agent.workbench.v2"
  );
}

function firstTurnSnapshot(conversationId: string) {
  return {
    ...agentWorkbenchRequirementReviewViewFixture,
    conversation: {
      ...agentWorkbenchRequirementReviewViewFixture.conversation,
      conversationId,
      runtimeRunId: null,
      status: "needs_confirmation",
      title: "AI Agent 平台工程师",
      workflowStartState: "not_started",
    },
    candidates: [],
    detailApprovals: [],
    messages: [
      {
        createdAt: "2026-06-13T09:30:00.000Z",
        messageId: `${conversationId}_msg_user`,
        messageType: "userText",
        payload: { kind: "job_request", jobTitle: null },
        role: "user",
        seq: 1,
        text: "上海 AI Agent 平台工程师，要求 Python 后端、RAG 和 workflow orchestration。",
      },
    ],
    pendingActions: {
      ...agentWorkbenchRequirementReviewViewFixture.pendingActions,
      allowed: ["submit_message"],
      pendingRequirementReviewCount: 0,
      primary: null,
    },
    requirementDraft: null,
    runtime: null,
    strategyGraph: { edges: [], nodes: [] },
    thinkingProcess: { activeRoundNo: null, rounds: [] },
    transcriptGroups: [
      {
        completedAt: null,
        events: [
          {
            createdAt: "2026-06-13T09:30:00.000Z",
            eventId: `${conversationId}:message:first_turn`,
            itemId: `${conversationId}_msg_user`,
            kind: "message.completed",
            label: "User message",
            payload: {
              kind: "message",
              messageId: `${conversationId}_msg_user`,
            },
            status: "completed",
            summary:
              "上海 AI Agent 平台工程师，要求 Python 后端、RAG 和 workflow orchestration。",
          },
          {
            createdAt: "2026-06-13T09:30:04.000Z",
            eventId: `${conversationId}:operation:extract_requirements`,
            itemId: `${conversationId}_operation_extract_requirements`,
            kind: "operation.started",
            label: "正在处理需求",
            payload: {
              kind: "operation",
              itemId: `${conversationId}_operation_extract_requirements`,
              summary: "正在思考",
            },
            status: "running",
            summary: "正在思考",
          },
        ],
        groupId: `conversation:${conversationId}:segment:1`,
        startedAt: "2026-06-13T09:30:00.000Z",
        status: "running",
        title: "已处理",
      },
    ],
  };
}

function queuedSnapshot(conversationId: string) {
  return {
    ...firstTurnSnapshot(conversationId),
    conversation: {
      ...firstTurnSnapshot(conversationId).conversation,
      workflowStartIntentId: "workflow_intent_queued",
      workflowStartState: "queued",
    },
    pendingActions: {
      ...firstTurnSnapshot(conversationId).pendingActions,
      primary: "workflow_start_queued",
    },
  };
}
