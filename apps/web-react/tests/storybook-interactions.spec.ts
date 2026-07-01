import { expect, type Page, test } from "@playwright/test";
import { failOnPageProblems } from "./pageProblems";

test.beforeEach(({ page }) => {
  failOnPageProblems(page);
});

test("storybook index only exposes product UI stories", async ({ request }) => {
  const response = await request.get("/index.json");

  expect(response.ok()).toBe(true);
  const storyIndex = (await response.json()) as {
    entries?: Record<string, { id?: string; title?: string }>;
    stories?: Record<string, { id?: string; title?: string }>;
  };
  const entries = Object.values(storyIndex.entries ?? storyIndex.stories ?? {});
  const primitiveEntries = entries.filter(
    (entry) =>
      entry.id?.startsWith("primitives-") ||
      entry.title?.startsWith("Primitives/"),
  );

  expect(primitiveEntries).toEqual([]);
});

test("manager clears stale status filters from shared story URLs", async ({
  page,
}) => {
  await page.goto(
    "/?path=/story/workbench-strategygraphcanvas--search-strategy&statuses=modified;new&tags=",
  );

  await expect
    .poll(() => new URL(page.url()).searchParams.has("statuses"))
    .toBe(false);
  await expect(page.getByText("No stories found")).toBeHidden();
  await expect(page.locator("#storybook-preview-iframe")).toBeVisible();
  await expect(
    page
      .frameLocator("#storybook-preview-iframe")
      .getByRole("region", { name: "检索策略图" }),
  ).toBeVisible();
});

test("strategy graph story covers canonical runtime swimlanes", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-strategygraphcanvas--canonical-runtime-swimlanes",
  );

  const graph = page.getByRole("region", { name: "检索策略图" });
  await expect(graph).toBeVisible();
  await expect(graph.getByText("AI Agent 平台工程师")).toBeVisible();
  await expect(graph.getByText("第 1 轮 · 查询包")).toBeVisible();
  await expect(graph.getByText("第 3 轮 · 猎聘检索")).toBeVisible();
  await expect(graph.getByText("第 4 轮 · Top Pool")).toBeVisible();
  await expect(graph.getByText("第 4 轮 · 下一轮策略")).toBeVisible();
  await expect(graph.locator(".strategy-graph__timeline")).toHaveCount(0);
  await expect(graph.getByText("第 1 轮", { exact: true })).toHaveCount(0);
  await expect(graph.getByText(/第 \d+ 轮检索中/)).toHaveCount(0);
  await expect(graph.getByText(/单轮检索|\d+ 轮检索/)).toHaveCount(0);
  await expect(
    graph.locator('.strategy-graph-node[data-source="liepin"]'),
  ).toHaveCount(4);
  await expect(
    graph.locator(
      '[data-edge-id="round:1:phase:feedback:all->round:2:phase:round_query:all"]',
    ),
  ).toHaveAttribute("d", /^M \d+ \d+ H \d+ V \d+ H \d+ V \d+ H \d+$/);
  await expect
    .poll(async () =>
      graph
        .getByLabel("检索策略图画布")
        .evaluate(
          (element) =>
            element.clientWidth > 0 &&
            element.scrollWidth >= element.clientWidth,
        ),
    )
    .toBe(true);
  await expect(graph.getByText(/CTS/i)).toHaveCount(0);
  await expect(graph.getByLabel("检索策略图控制")).toBeVisible();
});

test("thinking process rail story switches between candidate and thinking tabs", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-thinkingprocessrail--round-timeline",
  );

  const rail = page.getByRole("complementary", { name: "运行右栏" });
  await expect(rail).toBeVisible();
  await expect(rail.getByText("第 1 轮")).toBeVisible();
  await expect(rail.getByText("第 3 轮")).toBeVisible();
  await expect(rail.getByText("第 4 轮")).toBeVisible();

  await rail.getByRole("tab", { name: "候选人" }).click();
  await expect(rail.getByRole("region", { name: "候选人队列" })).toBeVisible();
  await expect(rail.getByRole("article", { name: "吴所谓" })).toBeVisible();

  await rail.getByRole("tab", { name: "思考过程" }).click();
  await expect(rail.getByRole("tabpanel", { name: "思考过程" })).toBeVisible();
  await expect(rail.getByText("反思和下一轮变更").first()).toBeVisible();
});

test("candidate queue loading and error stories render real states", async ({
  page,
}) => {
  await openStory(page, "/iframe.html?id=workbench-candidatequeue--loading");
  const loadingQueue = page.getByRole("region", { name: "候选人队列" });
  await expect(loadingQueue).toHaveAttribute("data-state", "loading");
  await expect(loadingQueue.getByText("读取中")).toBeVisible();

  await openStory(page, "/iframe.html?id=workbench-candidatequeue--error");
  const errorQueue = page.getByRole("region", { name: "候选人队列" });
  await expect(errorQueue).toHaveAttribute("data-state", "error");
  await expect(errorQueue.getByRole("alert")).toContainText(
    "候选人列表暂时不可用",
  );
});

test("transcript collapsed run group expands and collapses", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-transcript--collapsed-run-group",
  );

  const groupToggle = page.locator(".transcript-run-group__toggle").first();
  const messages = page.locator('.transcript-event[data-kind="message"]');
  await expect(messages).toHaveCount(0);

  await groupToggle.click();
  await expect(messages.first()).toBeVisible();

  await groupToggle.click();
  await expect(messages).toHaveCount(0);
});

test("transcript tool row exposes stable details", async ({ page }) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-transcript--tool-read-details",
  );

  const transcript = page.getByLabel("Agent transcript");
  const toolRow = transcript.getByRole("article", {
    name: "Loaded a toolread 2 files",
  });
  await expect(toolRow.getByText("tool_read_001")).toBeVisible();
  await expect(toolRow.getByText("读取 Fw Ceo Review 技能")).toBeVisible();
});

test("candidate queue story renders populated candidates", async ({ page }) => {
  await openStory(page, "/iframe.html?id=workbench-candidatequeue--populated");

  const candidateScope = page.locator("body");
  const innerCandidatesTab = candidateScope
    .getByRole("tab", { name: "候选人" })
    .first();
  if ((await innerCandidatesTab.count()) > 0) {
    await innerCandidatesTab.click();
    await expect(innerCandidatesTab).toHaveAttribute("aria-selected", "true");
  }

  const queue = candidateScope.getByRole("region", { name: "候选人队列" });
  await expect(queue).toBeVisible();
  await expect(queue.getByRole("article", { name: "吴所谓" })).toBeVisible();
  await expect(queue.getByRole("article", { name: "候选人 B" })).toBeVisible();
  await expect(
    queue.getByRole("button", { name: "查看详情" }).first(),
  ).toBeVisible();
});

test("composer draft story accepts and clears submitted input", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-composer--requirement-draft",
  );

  const composer = page.locator("textarea").first();
  await composer.fill("补充工具调用平台经验");
  const submitButton = page.locator('button[type="submit"]').first();
  await expect(submitButton).toBeEnabled();
  await submitButton.click();

  await expect(composer).toHaveValue("");
});

test("conversation screen first-turn contract stories render expected surfaces", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-conversationscreen--first-turn-thinking",
  );
  await expect(page.getByLabel("Agent transcript")).toContainText("正在思考");
  await expect(page.getByLabel("检索策略图")).toHaveCount(0);

  await openStory(
    page,
    "/iframe.html?id=workbench-conversationscreen--post-confirm-graph",
  );
  await expect(page.getByRole("region", { name: "检索策略图" })).toBeVisible();

  await openStory(
    page,
    "/iframe.html?id=workbench-conversationscreen--long-transcript-and-graph",
  );
  await expect(page.getByLabel("Agent transcript")).toBeVisible();
  await expect(page.getByRole("region", { name: "检索策略图" })).toBeVisible();
});

async function openStory(page: Page, url: string) {
  await page.goto(url);
  await page.waitForSelector("#storybook-root");
  await expect(page.locator("#storybook-root")).toBeVisible();
}
