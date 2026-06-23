import { expect, test } from "@playwright/test";

test("renders the workbench shell", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/conversations\/new$/);

  if (!testInfo.project.name.includes("mobile")) {
    await expect(
      page.getByRole("complementary", { name: "会话列表" }),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("region", { name: "新建招聘任务" }),
  ).toBeVisible();
  await expect(page.getByLabel("岗位名称和岗位JD")).toBeVisible();
});
