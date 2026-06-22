import { expect, type Page, test } from "@playwright/test";
import { failOnPageProblems } from "./pageProblems";

test.beforeEach(({ page }) => {
  failOnPageProblems(page);
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
  await expect(
    graph.locator('.strategy-graph-node[data-source="liepin"]'),
  ).toHaveCount(4);
  await expect
    .poll(async () =>
      graph
        .getByLabel("检索策略图画布")
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    )
    .toBe(true);
  await expect(graph.getByText(/CTS/i)).toHaveCount(0);
  await expect(page.getByLabel("检索策略图控制")).toHaveCount(0);
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

  let candidateScope = page.locator("body");
  if ((page.viewportSize()?.width ?? Number.POSITIVE_INFINITY) <= 1080) {
    const candidatesTab = page.locator("#conversation-candidates-tab");
    await candidatesTab.click();
    await expect(candidatesTab).toHaveAttribute("aria-selected", "true");
    candidateScope = page.locator("#conversation-panel-candidates");
    await expect(candidateScope).toBeVisible();
  }
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

test("primitive tabs support keyboard navigation", async ({ page }) => {
  await openStory(
    page,
    "/iframe.html?id=primitives-controlsgallery--controls-gallery",
  );

  const candidatesTab = page.getByRole("tab", { name: "候选人" });
  await candidatesTab.focus();
  await candidatesTab.press("ArrowRight");

  await expect(page.getByRole("tab", { name: "思考过程" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "最终名单" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

async function openStory(page: Page, url: string) {
  await page.goto(url);
  await page.waitForSelector("#storybook-root");
  await expect(page.locator("#storybook-root")).toBeVisible();
}
