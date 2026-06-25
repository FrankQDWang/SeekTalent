import { expect, test, type Page } from "@playwright/test";
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
  ).toContainText("running");
  await expect(
    page.getByRole("region", { name: "Agent transcript" }),
  ).toContainText(/queued -> running|运行进度/);

  await page
    .getByPlaceholder("输入消息、JD 或下一步招聘需求")
    .fill("现在进度如何");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => requests.messages).toHaveLength(2);
  expect(requests.messages[1]).toMatchObject({ message: "现在进度如何" });
  await expect(page.getByText("现在进度如何")).toBeVisible();
  await expect(page.getByText(/当前状态 running，进度 25%/)).toBeVisible();

  await page.reload();

  await expect(page).toHaveURL(/\/conversations\/agentv2_e2e$/);
  await expect(page.getByRole("region", { name: "需求确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "需求已确认" })).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "运行状态" }),
  ).toContainText("running");
  await expectTranscriptOrder(page, [
    "你好",
    "数据科学家",
    "需求确认",
    "现在进度如何",
    "当前状态 running，进度 25%",
  ]);
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
      const nextView =
        message === "现在进度如何"
          ? progressQuestionView()
          : requirementReviewView();
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
          ],
        },
      ],
      other_input_prompt: "补充其他要求",
      can_confirm: true,
    },
  };
}
