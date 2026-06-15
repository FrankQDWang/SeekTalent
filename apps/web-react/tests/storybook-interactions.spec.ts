import { expect, type Page, test } from "@playwright/test";

test("transcript collapsed run group expands and collapses", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-transcript--collapsed-run-group",
  );

  const groupToggle = page.locator(".transcript-run-group__toggle").first();
  await expect(
    page.getByRole("article", { name: "Agent response" }),
  ).toHaveCount(0);

  await groupToggle.click();
  await expect(
    page.getByRole("article", { name: "Agent response" }),
  ).toBeVisible();

  await groupToggle.click();
  await expect(
    page.getByRole("article", { name: "Agent response" }),
  ).toHaveCount(0);
});

test("transcript tool row exposes stable details", async ({ page }) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-transcript--file-read-running",
  );

  await page.locator(".transcript-tool-event__detail-toggle").click();

  const transcript = page.getByLabel("Agent transcript");
  await expect(transcript.getByText("summary", { exact: true })).toBeVisible();
  await expect(page.getByText("读取运行中")).toBeVisible();
});

test("thinking process rail switches to candidates", async ({ page }) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-thinkingprocessrail--round-timeline",
  );

  await page.getByRole("tab", { name: "候选人" }).click();

  await expect(page.getByRole("tabpanel", { name: "候选人" })).toBeVisible();
  await expect(page.getByRole("article", { name: "候选人 A" })).toBeVisible();
});

test("composer draft story accepts and clears submitted input", async ({
  page,
}) => {
  await openStory(
    page,
    "/iframe.html?id=workbench-composer--requirement-draft",
  );

  const composer = page.getByPlaceholder("继续补充岗位要求");
  await composer.fill("补充工具调用平台经验");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
  await page.getByRole("button", { name: "发送" }).click();

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
