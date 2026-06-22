import { expect, type Page, test } from "@playwright/test";

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
