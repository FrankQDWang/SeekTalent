import { expect, test } from "@playwright/test";

test("renders the workbench shell", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Wide Talent Search" }),
  ).toBeVisible();
  if (!testInfo.project.name.includes("mobile")) {
    await expect(
      page.getByRole("complementary", { name: "会话列表" }),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("region", { name: "新建招聘任务" }),
  ).toBeVisible();
  await expect(page.getByLabel("职位描述")).toBeVisible();
});
