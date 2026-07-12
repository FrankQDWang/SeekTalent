import { expect, test, type Page, type Route } from "@playwright/test";
import type {
  WorkbenchV2Conversation,
  WorkbenchV2ConversationView,
  WorkbenchV2TranscriptEvent,
} from "../src/lib/api/workbenchV2Types";
import { failOnPageProblems } from "./pageProblems";

test.use({
  viewport: { width: 1100, height: 820 },
});

test.beforeEach(({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop-chromium",
    "Workbench v2 E2E coverage runs on the desktop project.",
  );
  failOnPageProblems(page);
});

test("Workbench v2 supports chat, JD form, confirmation, progress, and refresh", async ({
  page,
}) => {
  const requests: {
    created: Record<string, unknown>[];
    messages: Record<string, unknown>[];
    requirementActions: Record<string, unknown>[];
  } = {
    created: [],
    messages: [],
    requirementActions: [],
  };
  let view = pureChatView();

  await mockWorkbenchV2Routes(
    page,
    requests,
    () => view,
    (nextView) => {
      view = nextView;
    },
  );

  await page.goto("/conversations/new");
  await page.getByLabel("消息、JD 或招聘需求").fill("你好");
  await page.getByRole("button", { name: "开始寻才" }).click();

  await expect.poll(() => requests.created).toHaveLength(1);
  expect(requests.created[0]).toMatchObject({ message: "你好" });
  await expect(page).toHaveURL(/\/conversations\/agentv2_e2e$/);
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("你好");
  await expect(page.getByText("可以，我会按普通对话继续。")).toBeVisible();
  await expect(page.getByText("已处理")).toHaveCount(0);

  const jobDescription =
    "数据科学家，负责 SQL、Python、A/B Testing，杭州，5年以上。";
  await page.getByLabel("下一步要求").fill(jobDescription);
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => requests.messages).toHaveLength(1);
  expect(requests.messages[0]).toMatchObject({ message: jobDescription });
  await expect(page.getByRole("region", { name: "需求确认" })).toBeVisible();
  await expectDocumentNotScrollable(page);

  const sql = page.getByRole("checkbox", { name: /SQL/ });
  await sql.focus();
  await page.keyboard.press("Space");
  await expect(sql).not.toBeChecked();
  await expect
    .poll(() => requests.requirementActions.at(-1))
    .toMatchObject({
      action: "set_selected",
      itemId: "item_sql",
      selected: false,
    });

  await page.getByRole("button", { name: "确认需求" }).click();
  await expect
    .poll(() => requests.requirementActions.at(-1))
    .toMatchObject({ action: "confirm" });
  await expect(
    page.getByRole("complementary", { name: "运行状态" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("complementary", { name: "运行详情" }),
  ).toBeVisible();
  const rightRail = page.getByRole("complementary", { name: "运行右栏" });
  await expect(rightRail.getByRole("tab", { name: "候选人" })).toBeVisible();
  await expect(rightRail.getByRole("tab", { name: "思考过程" })).toBeVisible();
  await rightRail.getByRole("tab", { name: "思考过程" }).click();
  const thinkingPanel = rightRail.getByRole("tabpanel", { name: "思考过程" });
  const paths = thinkingPanel.getByRole("group", { name: "检索路径" });
  await expect(paths.getByRole("group", { name: "主路径" })).toBeVisible();
  await expect(paths.getByRole("group", { name: "扩展路径" })).toHaveCount(0);
  await expect(page.getByText("query_e2e_main")).toHaveCount(0);
  await expect(page.getByText("term_group_e2e_main")).toHaveCount(0);
  await expect(page.getByText("run_e2e")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "检索策略图" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText(/queued -> running|运行进度/);
  await expectDocumentNotScrollable(page);

  await page
    .getByPlaceholder("输入消息、JD 或下一步招聘需求")
    .fill("现在进度如何");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => requests.messages).toHaveLength(2);
  expect(requests.messages[1]).toMatchObject({ message: "现在进度如何" });
  await expect(page.getByText("现在进度如何")).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText(/当前状态 running，进度 25%/);

  const supplementalRequirement =
    "补充：候选人优先有淘宝或天猫业务经验，下一轮生效";
  await page
    .getByPlaceholder("输入消息、JD 或下一步招聘需求")
    .fill(supplementalRequirement);
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => requests.messages).toHaveLength(3);
  expect(requests.messages[2]).toMatchObject({
    message: supplementalRequirement,
  });
  await expect(page.getByText(supplementalRequirement)).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("已记录补充要求，将在下一轮检索时使用。");

  await page
    .getByPlaceholder("输入消息、JD 或下一步招聘需求")
    .fill("请总结这次 run 的结果。");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => requests.messages).toHaveLength(4);
  await expect(page.getByText("请总结这次 run 的结果。")).toBeVisible();
  await expect(page.getByText("运行结果")).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("本次运行完成，筛选出 2 位候选人。");
  await expectDocumentNotScrollable(page);

  await page.reload();

  await expect(page).toHaveURL(/\/conversations\/agentv2_e2e$/);
  const confirmedRequirements = page.getByRole("region", { name: "需求确认" });
  await expect(confirmedRequirements).toBeVisible();
  await expect(confirmedRequirements.getByText("需求已确认")).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "运行状态" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("complementary", { name: "运行详情" }),
  ).toBeVisible();
  await expect(rightRail.getByRole("tab", { name: "候选人" })).toBeVisible();
  await expect(rightRail.getByRole("tab", { name: "思考过程" })).toBeVisible();
  await expect(page.getByText("run_e2e")).toHaveCount(0);
  await expectDocumentNotScrollable(page);
  await expectTranscriptOrder(page, [
    "你好",
    "数据科学家",
    "需求确认",
    "现在进度如何",
    "当前状态 running，进度 25%",
    supplementalRequirement,
    "已记录补充要求，将在下一轮检索时使用。",
    "请总结这次 run 的结果。",
    "本次运行完成，筛选出 2 位候选人。",
  ]);
});

test("Workbench v2 renders submitted turns before slow POST responses finish", async ({
  page,
}) => {
  let view = pureChatView();
  const pendingRoutes: {
    create?: Route;
    message?: Route;
  } = {};

  await page.route("**/api/agent/workbench/conversations**", (route) => {
    throw new Error(
      `Workbench v2 flow must not call old Workbench route: ${route.request().url()}`,
    );
  });
  await page.route("**/api/agent/workbench/v2/conversations", async (route) => {
    if (route.request().method() === "POST") {
      pendingRoutes.create = route;
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        schemaVersion: "agent.workbench.v2.list",
        conversations: [conversationListSummary(view)],
      },
    });
  });
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_e2e",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: view,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_e2e/messages",
    (route) => {
      pendingRoutes.message = route;
    },
  );

  await page.goto("/conversations/new");
  await page.getByLabel("消息、JD 或招聘需求").fill("你好");
  await page.getByRole("button", { name: "开始寻才" }).click();

  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("你好");
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("正在思考");
  await expect(page.getByRole("button", { name: "处理中" })).toHaveCount(0);

  const pendingCreateRoute = pendingRoutes.create;
  if (pendingCreateRoute === undefined) {
    throw new Error("Expected pending create request.");
  }
  await pendingCreateRoute.fulfill({
    contentType: "application/json",
    json: view,
    status: 201,
  });
  await expect(page).toHaveURL(/\/conversations\/agentv2_e2e$/);

  const slowMessage = "补充一段较慢处理的 JD：需要 Python、SQL、A/B Testing。";
  await page
    .getByPlaceholder("输入消息、JD 或下一步招聘需求")
    .fill(slowMessage);
  await page.getByRole("button", { name: "发送" }).click();

  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText(slowMessage);
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("正在思考");
  await expect(page.getByRole("button", { name: "处理中" })).toHaveCount(0);

  const pendingMessageRoute = pendingRoutes.message;
  if (pendingMessageRoute === undefined) {
    throw new Error("Expected pending message request.");
  }
  view = requirementReviewView();
  await pendingMessageRoute.fulfill({
    contentType: "application/json",
    json: view,
  });
  await expect(page.getByRole("region", { name: "需求确认" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText("数据科学家");
});

async function mockWorkbenchV2Routes(
  page: Page,
  requests: {
    created: Record<string, unknown>[];
    messages: Record<string, unknown>[];
    requirementActions: Record<string, unknown>[];
  },
  currentView: () => WorkbenchV2ConversationView,
  setCurrentView: (view: WorkbenchV2ConversationView) => void,
) {
  await page.route("**/api/agent/workbench/conversations**", (route) => {
    throw new Error(
      `Workbench v2 flow must not call old Workbench route: ${route.request().url()}`,
    );
  });
  await page.route("**/api/agent/workbench/v2/conversations", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      requests.created.push(body);
      const nextView = pureChatView();
      setCurrentView(nextView);
      await route.fulfill({
        contentType: "application/json",
        json: nextView,
        status: 201,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: {
        schemaVersion: "agent.workbench.v2.list",
        conversations: [conversationListSummary(currentView())],
      },
    });
  });
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_e2e",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: currentView(),
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_e2e/messages",
    async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      requests.messages.push(body);
      const message = typeof body.message === "string" ? body.message : "";
      const nextView = viewForMessageStep(requests.messages.length, message);
      setCurrentView(nextView);
      await route.fulfill({
        contentType: "application/json",
        json: nextView,
      });
    },
  );
  await page.route(
    "**/api/agent/workbench/v2/conversations/agentv2_e2e/requirement-actions",
    async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      requests.requirementActions.push(body);
      const nextView =
        body.action === "confirm" ? runtimeRunningView() : sqlDeselectedView();
      setCurrentView(nextView);
      await route.fulfill({
        contentType: "application/json",
        json: nextView,
      });
    },
  );
}

function viewForMessageStep(
  messageStep: number,
  message: string,
): WorkbenchV2ConversationView {
  switch (messageStep) {
    case 1:
      return requirementReviewView();
    case 2:
      return progressQuestionView();
    case 3:
      return supplementalRequirementView(message);
    case 4:
      return completedSummaryView();
    default:
      return progressQuestionView();
  }
}

async function expectTranscriptOrder(page: Page, texts: string[]) {
  await expect
    .poll(async () => {
      const transcriptText = await page
        .getByRole("region", { name: "Agent transcript" })
        .innerText();
      const indexes = texts.map((text) => transcriptText.indexOf(text));
      return indexes.every(
        (index, position) =>
          index >= 0 && (position === 0 || index > indexes[position - 1]),
      );
    })
    .toBe(true);
}

async function expectDocumentNotScrollable(page: Page) {
  await expect
    .poll(async () =>
      page.evaluate(() => ({
        scrollHeight: Math.max(
          document.documentElement.scrollHeight,
          document.body.scrollHeight,
        ),
        shellScrollTop:
          document.querySelector(".conversation-shell")?.scrollTop ?? -1,
        shellTop: Math.round(
          document.querySelector(".conversation-shell")?.getBoundingClientRect()
            .top ?? -1,
        ),
        scrollY: window.scrollY,
        viewportHeight: window.innerHeight,
      })),
    )
    .toEqual({
      scrollHeight: 820,
      shellScrollTop: 0,
      shellTop: 0,
      scrollY: 0,
      viewportHeight: 820,
    });
}

function pureChatView(): WorkbenchV2ConversationView {
  return view({
    conversation: conversation({
      title: "你好",
      runtimeState: "idle",
      updatedAt: "2026-06-25T02:00:02.000000+00:00",
    }),
    transcriptEvents: [
      event({
        eventId: "event_user_hello",
        step: 1,
        type: "user_message",
        role: "user",
        payload: { text: "你好" },
      }),
      event({
        eventId: "event_assistant_hello",
        step: 2,
        type: "assistant_message",
        role: "assistant",
        payload: { text: "可以，我会按普通对话继续。" },
      }),
    ],
  });
}

function requirementReviewView(): WorkbenchV2ConversationView {
  const requirement = requirementPayload({ sqlSelected: true });
  return view({
    conversation: conversation({
      title: "数据科学家",
      runtimeState: "idle",
      updatedAt: "2026-06-25T02:00:04.000000+00:00",
    }),
    requirementForm: requirement,
    transcriptEvents: [
      ...pureChatView().transcriptEvents,
      event({
        eventId: "event_user_jd",
        step: 3,
        type: "user_message",
        role: "user",
        payload: {
          text: "数据科学家，负责 SQL、Python、A/B Testing，杭州，5年以上。",
        },
      }),
      event({
        eventId: "event_requirement_form",
        step: 4,
        type: "requirement_form",
        role: "assistant",
        payload: requirement,
      }),
    ],
  });
}

function sqlDeselectedView(): WorkbenchV2ConversationView {
  const requirement = requirementPayload({ sqlSelected: false });
  return {
    ...requirementReviewView(),
    requirementForm: requirement,
    transcriptEvents: requirementReviewView().transcriptEvents.map((item) =>
      item.eventId === "event_requirement_form"
        ? { ...item, payload: requirement }
        : item,
    ),
  };
}

function runtimeRunningView(): WorkbenchV2ConversationView {
  const requirement = requirementPayload({
    readonly: true,
    sqlSelected: false,
  });
  return view({
    conversation: conversation({
      title: "数据科学家",
      runtimeRunId: "run_e2e",
      runtimeState: "running",
      updatedAt: "2026-06-25T02:00:08.000000+00:00",
    }),
    requirementForm: requirement,
    runtime: { state: "running", runtimeRunId: "run_e2e" },
    ...workflowSurface("queued -> running：候选人检索进度 25%"),
    transcriptEvents: [
      ...pureChatView().transcriptEvents,
      event({
        eventId: "event_user_jd",
        step: 3,
        type: "user_message",
        role: "user",
        payload: {
          text: "数据科学家，负责 SQL、Python、A/B Testing，杭州，5年以上。",
        },
      }),
      event({
        eventId: "event_requirement_confirmed",
        step: 4,
        type: "requirement_form_confirmed",
        role: "assistant",
        payload: requirement,
      }),
      event({
        eventId: "event_runtime_progress",
        step: 5,
        type: "runtime_progress",
        role: "runtime",
        status: "running",
        payload: { summary: "queued -> running：候选人检索进度 25%" },
      }),
    ],
  });
}

function progressQuestionView(): WorkbenchV2ConversationView {
  return view({
    ...runtimeRunningView(),
    conversation: conversation({
      title: "数据科学家",
      runtimeRunId: "run_e2e",
      runtimeState: "running",
      updatedAt: "2026-06-25T02:00:12.000000+00:00",
    }),
    runtime: { state: "running", runtimeRunId: "run_e2e" },
    ...workflowSurface("当前状态 running，进度 25%。"),
    transcriptEvents: [
      ...runtimeRunningView().transcriptEvents,
      event({
        eventId: "event_user_progress_question",
        step: 6,
        type: "user_message",
        role: "user",
        payload: { text: "现在进度如何" },
      }),
      event({
        eventId: "event_assistant_progress_answer",
        step: 7,
        type: "assistant_message",
        role: "assistant",
        payload: { text: "当前状态 running，进度 25%。" },
      }),
    ],
  });
}

function supplementalRequirementView(
  supplementalRequirement: string,
): WorkbenchV2ConversationView {
  return view({
    ...progressQuestionView(),
    transcriptEvents: [
      ...progressQuestionView().transcriptEvents,
      event({
        eventId: "event_user_supplemental_requirement",
        step: 8,
        type: "user_message",
        role: "user",
        payload: { text: supplementalRequirement },
      }),
      event({
        eventId: "event_runtime_next_round_requirement",
        step: 9,
        type: "runtime_progress",
        role: "runtime",
        status: "completed",
        payload: {
          summary: "已记录补充要求，将在下一轮检索时使用。",
          supplementalRequirement,
        },
      }),
      event({
        eventId: "event_assistant_supplemental_requirement",
        step: 10,
        type: "assistant_message",
        role: "assistant",
        payload: { text: "已记录补充要求，将在下一轮检索时使用。" },
      }),
    ],
  });
}

function completedSummaryView(): WorkbenchV2ConversationView {
  return view({
    ...progressQuestionView(),
    conversation: conversation({
      title: "数据科学家",
      runtimeRunId: "run_e2e",
      runtimeState: "completed",
      updatedAt: "2026-06-25T02:00:16.000000+00:00",
    }),
    runtime: { state: "completed", runtimeRunId: "run_e2e" },
    ...workflowSurface("本次运行完成，筛选出 2 位候选人。", "completed"),
    transcriptEvents: [
      ...supplementalRequirementView(
        "补充：候选人优先有淘宝或天猫业务经验，下一轮生效",
      ).transcriptEvents,
      event({
        eventId: "event_user_summary_question",
        step: 11,
        type: "user_message",
        role: "user",
        payload: { text: "请总结这次 run 的结果。" },
      }),
      event({
        eventId: "event_runtime_result",
        step: 12,
        type: "runtime_result",
        role: "runtime",
        status: "completed",
        payload: { summary: "本次运行完成，筛选出 2 位候选人。" },
      }),
      event({
        eventId: "event_assistant_summary",
        step: 13,
        type: "assistant_message",
        role: "assistant",
        payload: { text: "本次运行完成，筛选出 2 位候选人。" },
      }),
    ],
  });
}

function view(
  overrides: Partial<WorkbenchV2ConversationView> = {},
): WorkbenchV2ConversationView {
  return {
    schemaVersion: "agent.workbench.v2",
    conversation: conversation(),
    transcriptEvents: [],
    requirementForm: null,
    runtime: null,
    ...overrides,
  };
}

function conversation(
  overrides: Partial<WorkbenchV2Conversation> = {},
): WorkbenchV2Conversation {
  return {
    conversationId: "agentv2_e2e",
    title: "Workbench v2 E2E",
    runtimeState: "idle",
    runtimeRunId: null,
    createdAt: "2026-06-25T02:00:00.000000+00:00",
    updatedAt: "2026-06-25T02:00:00.000000+00:00",
    ...overrides,
  };
}

function workflowSurface(
  summary: string,
  status: "running" | "completed" = "running",
): Pick<
  WorkbenchV2ConversationView,
  "strategyGraph" | "thinkingProcess" | "candidates"
> {
  return {
    strategyGraph: {
      nodes: [
        {
          nodeId: "v2-requirements",
          kind: "requirements",
          label: "需求确认",
          summary: "需求已确认",
          status: "completed",
          sourceKind: "all",
        },
        {
          nodeId: "v2-source-search",
          kind: "phase",
          label: "候选人检索",
          summary,
          status,
          sourceKind: "all",
          stage: status === "completed" ? "completed" : "source_search",
        },
      ],
      edges: [
        {
          edgeId: "v2-edge-requirements-source-search",
          fromNodeId: "v2-requirements",
          toNodeId: "v2-source-search",
        },
      ],
    },
    thinkingProcess: {
      activeRoundNo: 1,
      rounds: [
        {
          roundNo: 1,
          status,
          queryGroups: [
            {
              queryInstanceId: "query_e2e_main",
              termGroupKey: "term_group_e2e_main",
              queryRole: "exploit",
              laneType: "exploit",
              queryTerms: ["SQL", "Python", "A/B Testing"],
              keywordQuery: "SQL AND Python",
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
              queryInstanceId: "query_e2e_explore",
              termGroupKey: "term_group_e2e_explore",
              queryRole: "explore",
              laneType: "generic_explore",
              queryTerms: ["experiment design"],
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

function event(
  overrides: Partial<WorkbenchV2TranscriptEvent>,
): WorkbenchV2TranscriptEvent {
  return {
    eventId: "event",
    step: 1,
    type: "assistant_message",
    role: "assistant",
    status: "completed",
    payload: {},
    createdAt: "2026-06-25T02:00:00.000000+00:00",
    ...overrides,
  };
}

function conversationListSummary(view: WorkbenchV2ConversationView) {
  return {
    conversationId: view.conversation.conversationId,
    title: view.conversation.title,
    status: view.conversation.runtimeState,
    updatedAt: view.conversation.updatedAt,
  };
}

function requirementPayload({
  readonly = false,
  sqlSelected,
}: {
  readonly?: boolean;
  sqlSelected: boolean;
}) {
  return {
    readonly,
    draft: {
      sections: [
        {
          section_id: "core_skills",
          display_name: "核心能力",
          items: [
            {
              item_id: "item_sql",
              text: "SQL 数据分析",
              selected: sqlSelected,
              allowed_actions: [
                "select",
                "edit",
                "delete",
                "move_to_preferred_capabilities",
              ],
              status: "active",
            },
            {
              item_id: "item_python",
              text: "Python 建模",
              selected: true,
              allowed_actions: [
                "select",
                "edit",
                "delete",
                "move_to_preferred_capabilities",
              ],
              status: "active",
            },
            ...[
              "A/B Testing 实验设计",
              "因果推断",
              "机器学习建模",
              "指标体系建设",
              "数据产品落地",
              "跨团队沟通",
              "五年以上数据科学经验",
              "杭州 base",
              "运筹优化",
              "归因分析",
              "商业分析表达",
              "资源整合能力",
            ].map((text, index) => ({
              item_id: `item_extra_${String(index)}`,
              text,
              selected: true,
              allowed_actions: [
                "select",
                "edit",
                "delete",
                "move_to_preferred_capabilities",
              ],
              status: "active",
            })),
          ],
        },
      ],
      other_input_prompt: "补充其他要求",
      can_confirm: true,
    },
  };
}
