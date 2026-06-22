import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    class FakeEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;

      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      readonly url: string;
      closed = false;
      onerror: ((this: EventSource, ev: Event) => unknown) | null = null;
      onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null =
        null;
      onopen: ((this: EventSource, ev: Event) => unknown) | null = null;
      readyState = 1;
      withCredentials = false;

      constructor(url: string) {
        super();
        this.url = url;
        window.__agentWorkbenchEventSources.push(
          this as unknown as EventSource & { closed?: boolean },
        );
      }

      close() {
        this.closed = true;
      }
    }

    window.__agentWorkbenchEventSources = [];
    window.EventSource = FakeEventSource as unknown as typeof EventSource;
  });

  await page.route("**/api/agent/workbench/conversations", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        conversations: [conversationSnapshot.conversation],
      },
    });
  });
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: conversationSnapshot,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1/candidates/candidate_001/detail",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: candidateDetailSnapshot,
      });
    },
  );
});

test("renders live workbench graph and opens semantic stream", async ({
  page,
}, testInfo) => {
  await page.goto("/conversations/agent_conv_1");

  if (testInfo.project.name.includes("mobile")) {
    await expect(page.getByRole("tab", { name: "Chat" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Graph" })).toBeVisible();
    await page.getByRole("tab", { name: "Graph" }).click();
  } else {
    await expect(
      page.getByRole("complementary", { name: "会话列表" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "资深 Python 后端" }),
    ).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "检索策略图" })).toBeVisible();
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.getByText("需求拆解")).toBeVisible();
  await expect(page.getByText("第 1 轮检索")).toBeVisible();
  if (testInfo.project.name.includes("mobile")) {
    await page.getByRole("tab", { name: "Candidates" }).click();
    await expect(
      page
        .getByRole("tabpanel", { name: "Candidates" })
        .getByText("第一轮正在运行"),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Graph" }).click();
  }
  await expect
    .poll(() => page.locator(".react-flow__node").count())
    .toBeGreaterThan(0);

  if (testInfo.project.name.includes("mobile")) {
    await page.getByRole("tab", { name: "Candidates" }).click();
  }
  await page.getByRole("tab", { name: "候选人" }).click();
  await page.getByRole("button", { name: "查看详情" }).first().click();
  await expect(page.getByRole("dialog", { name: "候选人详情" })).toBeVisible();
  await expect(page.getByText("工作经历")).toBeVisible();
  await expect(
    page.getByText("最近一段经历覆盖 Agent 工具调用平台。"),
  ).toBeVisible();

  await expect
    .poll(async () =>
      page.evaluate(() => window.__agentWorkbenchEventSources[0]?.url ?? ""),
    )
    .toContain(
      "/api/agent/workbench/conversations/agent_conv_1/events/stream?after_seq=4",
    );

  await page.evaluate(() => {
    const source = window.__agentWorkbenchEventSources[0];
    source.dispatchEvent(
      new MessageEvent("agent_workbench_event", {
        data: JSON.stringify({
          schemaVersion: "agent.workbench.stream.v1",
          conversationId: "agent_conv_1",
          seq: 5,
          kind: "strategyGraph.changed",
          payload: {
            payloadType: "strategyGraph.changed",
            kind: "strategy_graph",
            itemId: "graph_2",
          },
          createdAt: "2026-06-12T12:01:00+00:00",
        }),
      }),
    );
  });

  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath(`workbench-live-${testInfo.project.name}.png`),
  });
});

test("submits recruiter actions through the Workbench BFF routes", async ({
  page,
}) => {
  let submittedMessage: Record<string, unknown> | null = null;
  let requirementOperations: Record<string, unknown> | null = null;
  let amendedRequirement: Record<string, unknown> | null = null;
  let confirmedRequirements: Record<string, unknown> | null = null;
  const latestSubmittedMessage = () => submittedMessage;
  const latestRequirementOperations = () => requirementOperations;
  const latestAmendedRequirement = () => amendedRequirement;
  const latestConfirmedRequirements = () => confirmedRequirements;

  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1/messages",
    async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      const text = typeof body.text === "string" ? body.text : "";
      submittedMessage = body;
      await route.fulfill({
        contentType: "application/json",
        json: {
          ...conversationSnapshot,
          messages: [
            ...conversationSnapshot.messages,
            {
              createdAt: "2026-06-12T12:02:00+00:00",
              messageId: "msg_2",
              messageType: "userText",
              payload: { kind: "empty" },
              role: "user",
              seq: 2,
              text,
            },
          ],
          streamCursor: {
            ...conversationSnapshot.streamCursor,
            latestMessageSeq: 2,
            latestStreamSeq: 5,
            snapshotSeq: 5,
            viewRevision: 5,
          },
        },
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1/requirements/operations",
    async (route) => {
      requirementOperations = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        contentType: "application/json",
        json: conversationSnapshot,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1/requirements/amend-from-text",
    async (route) => {
      amendedRequirement = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        contentType: "application/json",
        json: conversationSnapshot,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_1/requirements/confirm",
    async (route) => {
      confirmedRequirements = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        contentType: "application/json",
        json: {
          ...conversationSnapshot,
          conversation: {
            ...conversationSnapshot.conversation,
            workflowStartIntentId: "workflow_intent_1",
            workflowStartReasonCode: null,
            workflowStartState: "queued",
          },
          pendingActions: {
            ...conversationSnapshot.pendingActions,
            allowed: ["submit_message"],
            primary: "workflow_start_queued",
          },
          streamCursor: {
            ...conversationSnapshot.streamCursor,
            latestStreamSeq: 6,
            snapshotSeq: 6,
            viewRevision: 6,
          },
        },
      });
    },
  );

  await page.goto("/conversations/agent_conv_1");
  await page.getByRole("button", { name: /Python 后端平台经验/ }).click();

  await expect
    .poll(() => latestRequirementOperations())
    .toMatchObject({
      draftRevisionId: "draft_1",
      expectedDraftRevisionId: "draft_1",
      operations: [
        {
          itemId: "item_1",
          op: "set_selected",
          selected: false,
        },
      ],
    });
  const operationsIdempotencyKey =
    latestRequirementOperations()?.idempotencyKey;
  expect(
    typeof operationsIdempotencyKey === "string"
      ? operationsIdempotencyKey
      : "",
  ).toContain("workbench:requirement-update:");

  await page.getByLabel("其他").fill("补充评测平台经验");

  await page.getByPlaceholder("输入下一步要求").fill("继续补充评测平台经验");
  await page.getByRole("button", { name: "发送" }).click();

  await expect
    .poll(() => submittedMessage)
    .toMatchObject({
      messageType: "userText",
      text: "继续补充评测平台经验",
    });
  const messageIdempotencyKey = latestSubmittedMessage()?.idempotencyKey;
  expect(
    typeof messageIdempotencyKey === "string" ? messageIdempotencyKey : "",
  ).toContain("workbench:message:");
  await expect(page.getByPlaceholder("输入下一步要求")).toHaveValue("");

  await page.getByRole("button", { name: "确认需求" }).click();

  await expect
    .poll(() => latestAmendedRequirement())
    .toMatchObject({
      draftRevisionId: "draft_1",
      expectedDraftRevisionId: "draft_1",
      text: "补充评测平台经验",
    });
  const amendIdempotencyKey = latestAmendedRequirement()?.idempotencyKey;
  expect(
    typeof amendIdempotencyKey === "string" ? amendIdempotencyKey : "",
  ).toContain("workbench:requirement-amend:");

  await expect
    .poll(() => confirmedRequirements)
    .toMatchObject({
      draftRevisionId: "draft_1",
      expectedDraftRevisionId: "draft_1",
    });
  const confirmIdempotencyKey = latestConfirmedRequirements()?.idempotencyKey;
  expect(
    typeof confirmIdempotencyKey === "string" ? confirmIdempotencyKey : "",
  ).toContain("workbench:confirm-requirements:");
});

test("starts a new workbench conversation from the home JD entry", async ({
  page,
}) => {
  let createdConversationRequest: Record<string, unknown> | null = null;
  let submittedJdRequest: Record<string, unknown> | null = null;
  const latestCreatedConversationRequest = () => createdConversationRequest;
  const latestSubmittedJdRequest = () => submittedJdRequest;
  const createdConversationSnapshot = {
    ...conversationSnapshot,
    conversation: {
      ...conversationSnapshot.conversation,
      conversationId: "agent_conv_created",
      runtimeRunId: null,
      status: "needs_confirmation",
      title: "AI Agent 平台工程师",
    },
    candidates: [],
    runtime: null,
    strategyGraph: { edges: [], nodes: [] },
    thinkingProcess: { activeRoundNo: null, rounds: [] },
  };

  await page.route("**/api/agent/conversations", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    createdConversationRequest = route.request().postDataJSON() as Record<
      string,
      unknown
    >;
    await route.fulfill({
      contentType: "application/json",
      json: {
        conversation: {
          conversationId: "agent_conv_created",
          title: "AI Agent 平台工程师",
        },
      },
      status: 201,
    });
  });
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_created",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: createdConversationSnapshot,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/conversations/agent_conv_created/messages",
    async (route) => {
      submittedJdRequest = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        contentType: "application/json",
        json: createdConversationSnapshot,
      });
    },
  );

  const jobDescription =
    "AI Agent 平台工程师 寻找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。";

  await page.goto("/");
  await page.getByLabel("岗位名称和岗位JD").fill(jobDescription);
  await page.getByRole("button", { name: "开始寻才" }).click();

  await expect
    .poll(() => latestCreatedConversationRequest())
    .toMatchObject({ title: jobDescription });
  await expect
    .poll(() => latestSubmittedJdRequest())
    .toMatchObject({
      jobTitle: null,
      messageType: "submitJd",
      text: jobDescription,
    });
  expect(latestSubmittedJdRequest()).not.toHaveProperty("sourceKinds");
  const idempotencyKey = latestSubmittedJdRequest()?.idempotencyKey;
  expect(typeof idempotencyKey === "string" ? idempotencyKey : "").toContain(
    "workbench:submit-jd:",
  );
  await expect(page).toHaveURL(/\/conversations\/agent_conv_created$/);
  await expect(page.getByRole("button", { name: "确认需求" })).toBeVisible();
});

declare global {
  interface Window {
    __agentWorkbenchEventSources: Array<EventSource & { closed?: boolean }>;
  }
}

const conversationSnapshot = {
  schemaVersion: "agent.workbench.view.v2",
  conversation: {
    conversationId: "agent_conv_1",
    title: "资深 Python 后端",
    status: "running",
    isArchived: false,
    runtimeRunId: "runtime_1",
    workbenchSessionId: "session_1",
    updatedAt: "2026-06-12T12:00:00+00:00",
  },
  messages: [
    {
      messageId: "msg_1",
      seq: 1,
      role: "user",
      messageType: "user_text",
      text: "寻找资深 Python 后端",
      payload: { kind: "job_request", jobTitle: "资深 Python 后端" },
      createdAt: "2026-06-12T12:00:00+00:00",
    },
  ],
  activities: [
    {
      activityId: "activity_1",
      seq: 1,
      activityType: "runtime_event",
      status: "running",
      title: "第 1 轮检索",
      summary: "正在检索候选人",
      sourceRuntimeRunId: "runtime_1",
      payload: {
        kind: "runtime_round",
        stage: "round",
        status: "running",
        roundNo: 1,
        queryTerms: ["AI agent", "LLM"],
        keywordQuery: "AI agent LLM",
      },
      updatedAt: "2026-06-12T12:00:10+00:00",
    },
  ],
  transcriptGroups: [
    {
      groupId: "conversation:agent_conv_1:segment:1",
      title: "已处理",
      status: "running",
      startedAt: "2026-06-12T12:00:00+00:00",
      completedAt: null,
      events: [
        {
          eventId: "message:msg_1:completed",
          itemId: "msg_1",
          kind: "message.completed",
          status: "completed",
          label: "User message",
          summary: "寻找资深 Python 后端",
          payload: { kind: "message", messageId: "msg_1" },
          createdAt: "2026-06-12T12:00:00+00:00",
        },
      ],
    },
  ],
  requirementDraft: {
    canConfirm: true,
    draftRevisionId: "draft_1",
    otherInputPrompt: "其他",
    sections: [
      {
        backendField: "must_have_capabilities",
        displayName: "必须满足",
        items: [
          {
            allowedActions: ["set_selected"],
            editable: true,
            enabled: true,
            itemId: "item_1",
            sectionId: "must_have_capabilities",
            selected: true,
            source: "extracted",
            status: "resolved",
            text: "Python 后端平台经验",
          },
        ],
        sectionId: "must_have_capabilities",
      },
    ],
    status: "needs_review",
    summary: "Python 后端 / RAG / 平台工程",
    title: "资深 Python 后端",
    unresolvedReviewItemCount: 0,
  },
  runtime: {
    runtimeRunId: "runtime_1",
    status: "running",
    currentStage: "round",
    currentRound: 1,
    latestEventSeq: 7,
  },
  strategyGraph: {
    nodes: [
      {
        nodeId: "requirements",
        kind: "requirements",
        label: "需求拆解",
        summary: "已确认岗位要求",
        status: "completed",
        sourceKind: "all",
      },
      {
        nodeId: "activity_1",
        kind: "activity",
        label: "第 1 轮检索",
        summary: "正在检索候选人",
        status: "running",
        sourceKind: "liepin",
        activityId: "activity_1",
      },
    ],
    edges: [
      {
        edgeId: "requirements->activity_1",
        fromNodeId: "requirements",
        toNodeId: "activity_1",
        label: "生成检索策略",
      },
    ],
  },
  thinkingProcess: {
    activeRoundNo: 1,
    rounds: [
      {
        roundNo: 1,
        status: "running",
        cards: [
          {
            title: "关键词",
            text: "AI agent LLM",
            terms: ["AI agent", "LLM"],
          },
          {
            title: "observation",
            text: "第一轮正在运行",
            terms: [],
          },
          {
            title: "反思和下一轮变更",
            text: "等待第一轮结果",
            terms: [],
          },
        ],
      },
    ],
  },
  sourceConnections: [],
  candidates: [
    {
      candidateId: "candidate_001",
      rank: 1,
      displayName: "候选人 A",
      headline: "平台工程负责人 / 上海 / Python + RAG",
      company: "某 AI Infra 公司",
      location: "上海",
      education: "本科",
      experienceYears: 10,
      sourceKinds: ["cts", "liepin"],
      matchScore: 92,
      matchSummary: "Agent 工具调用平台和 RAG 链路证据完整。",
      status: "running",
      detailAvailability: "available",
      accessState: "allowed",
      evidenceLevel: "detail",
    },
  ],
  detailApprovals: [],
  reviewArtifacts: [],
  finalSummary: null,
  pendingActions: {
    primary: "确认需求后开始检索",
    allowed: ["submit_message", "confirm_requirements"],
    pendingCommandCount: 0,
    pendingRequirementReviewCount: 0,
    pendingMemoryReviewCount: 0,
  },
  streamCursor: {
    latestMessageSeq: 1,
    latestActivitySeq: 1,
    latestRuntimeEventSeq: 7,
    latestStreamSeq: 4,
    snapshotSeq: 4,
    viewRevision: 4,
  },
  reasonCode: null,
};

const candidateDetailSnapshot = {
  accessState: "allowed",
  candidateId: "candidate_001",
  detailAvailability: "available",
  displayName: "候选人 A",
  evidence: [
    "最近一段经历覆盖 Agent 工具调用平台。",
    "项目经验包含 RAG 检索链路和评测平台。",
  ],
  evidenceLevel: "detail",
  headline: "平台工程负责人 / 上海 / Python + RAG",
  matchScore: 92,
  reasonCode: null,
  sections: [
    {
      title: "工作经历",
      items: [
        "某 AI Infra 公司平台工程负责人，负责工具调用平台和权限边界。",
        "主导 RAG 检索链路重构，覆盖召回、排序、评测和灰度发布。",
      ],
    },
  ],
  sourceKinds: ["cts", "liepin"],
};
