import { expect, test, type Page } from "@playwright/test";
import { failOnPageProblems } from "./pageProblems";

test.beforeEach(({ page }) => {
  failOnPageProblems(page);
});

const visualStories = [
  {
    name: "workbench-shell-figma-reference",
    url: "/iframe.html?id=workbench-workbenchshell--figma-thumbnail-reference",
  },
  {
    name: "workbench-home-initial",
    url: "/iframe.html?id=workbench-homestartpanel--initial",
  },
  {
    name: "workbench-home-draft",
    url: "/iframe.html?id=workbench-composer--requirement-draft",
  },
  {
    name: "workbench-requirement-review",
    url: "/iframe.html?id=workbench-requirementreviewpanel--needs-confirmation",
  },
  {
    name: "workbench-strategy-graph",
    url: "/iframe.html?id=workbench-strategygraphcanvas--search-strategy",
  },
  {
    name: "workbench-strategy-graph-canonical",
    url: "/iframe.html?id=workbench-strategygraphcanvas--canonical-runtime-swimlanes",
  },
  {
    name: "workbench-strategy-graph-large",
    url: "/iframe.html?id=workbench-strategygraphcanvas--large-search-strategy",
  },
  {
    name: "workbench-thinking-process",
    url: "/iframe.html?id=workbench-thinkingprocessrail--round-timeline",
  },
  {
    name: "workbench-thinking-process-dual-lane-mobile",
    url: "/iframe.html?id=workbench-thinkingprocessrail--dual-lane-compact-mobile",
    viewport: { height: 900, width: 375 },
  },
  {
    name: "workbench-candidates-empty",
    url: "/iframe.html?id=workbench-candidatequeue--empty",
  },
  {
    name: "workbench-candidates-list",
    url: "/iframe.html?id=workbench-candidatequeue--populated",
  },
  {
    name: "workbench-candidates-loading",
    url: "/iframe.html?id=workbench-candidatequeue--loading",
  },
  {
    name: "workbench-candidates-error",
    url: "/iframe.html?id=workbench-candidatequeue--error",
  },
  {
    name: "workbench-candidate-detail",
    url: "/iframe.html?id=workbench-candidatedetaildrawer--summary",
  },
  {
    name: "workbench-resume-full",
    url: "/iframe.html?id=workbench-resumeevidencepanel--full-content",
  },
  {
    name: "workbench-transcript-collapsed",
    url: "/iframe.html?id=workbench-transcript--collapsed-run-group",
  },
  {
    name: "workbench-transcript-expanded",
    url: "/iframe.html?id=workbench-transcript--expanded-run-group",
  },
  {
    name: "workbench-transcript-tool-detail",
    url: "/iframe.html?id=workbench-transcript--tool-read-details",
  },
  {
    name: "workbench-transcript-web-running",
    url: "/iframe.html?id=workbench-transcript--web-search-running",
  },
  {
    name: "workbench-transcript-file-complete",
    url: "/iframe.html?id=workbench-transcript--file-search-complete",
  },
  {
    name: "workbench-transcript-file-running",
    url: "/iframe.html?id=workbench-transcript--file-read-running",
  },
  {
    name: "workbench-transcript-guided-followup",
    url: "/iframe.html?id=workbench-transcript--guided-followup",
  },
  {
    name: "workbench-first-turn-thinking",
    url: "/iframe.html?id=workbench-conversationscreen--first-turn-thinking",
  },
  {
    name: "workbench-requirement-review-long-content",
    url: "/iframe.html?id=workbench-conversationscreen--requirement-review-long-content",
  },
  {
    name: "workbench-post-confirm-graph",
    url: "/iframe.html?id=workbench-conversationscreen--post-confirm-graph",
  },
  {
    name: "workbench-long-transcript-and-graph",
    url: "/iframe.html?id=workbench-conversationscreen--long-transcript-and-graph",
  },
  {
    name: "workbench-resizable-layout",
    url: "/iframe.html?id=workbench-conversationscreen--resizable-layout",
  },
] as const;

for (const story of visualStories) {
  test(`${story.name} matches the Storybook visual baseline`, async ({
    page,
  }) => {
    if ("viewport" in story) {
      await page.setViewportSize(story.viewport);
    }
    await page.goto(story.url);
    await page.waitForSelector("#storybook-root");
    await waitForStoryRendered(page);
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    await waitForStoryReady(page);
    if (story.name === "workbench-resizable-layout") {
      await expectResizableLayoutFillsVisibleWorkspace(page);
    }
    if (story.name === "workbench-thinking-process-dual-lane-mobile") {
      await expectCompactDualLaneThinkingProcess(page);
    }

    await expect(page.locator("#storybook-root")).toHaveScreenshot(
      `${story.name}.png`,
    );
  });
}

async function expectCompactDualLaneThinkingProcess(page: Page) {
  const rail = page.getByRole("complementary", { name: "运行右栏" });
  await expect(rail).toBeVisible();

  const paths = rail.getByRole("group", { name: "检索路径" });
  await expect(paths.getByRole("group", { name: "主路径" })).toBeVisible();
  await expect(paths.getByRole("group", { name: "扩展路径" })).toBeVisible();
  await expect(
    paths.getByText(
      "production-grade retrieval orchestration、long-context evaluation systems",
    ),
  ).toBeVisible();
  await expect(
    paths.getByText("cross-functional orchestration governance"),
  ).toBeVisible();
  await expect(paths.getByText(/原始|新增|重复/)).toHaveCount(0);

  await expect
    .poll(() =>
      page.evaluate(() => ({
        documentFits: document.documentElement.scrollWidth <= window.innerWidth,
        railFits: (() => {
          const railElement = document.querySelector<HTMLElement>(
            ".thinking-process-rail",
          );
          return (
            railElement !== null &&
            railElement.scrollWidth <= railElement.clientWidth
          );
        })(),
      })),
    )
    .toEqual({ documentFits: true, railFits: true });
}

async function waitForStoryRendered(page: Page) {
  await page.waitForFunction(
    () => {
      const isVisible = (element: Element) => {
        const style = window.getComputedStyle(element);
        return (
          element.getClientRects().length > 0 &&
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          Number(style.opacity) !== 0
        );
      };
      const root = document.getElementById("storybook-root");
      if (root === null) {
        return false;
      }
      const visibleStorybookState = Array.from(
        document.querySelectorAll(
          ".sb-loader, .sb-preparing-story, .sb-errordisplay, .sb-nopreview",
        ),
      ).some(isVisible);
      const visibleStoryRoot = Array.from(root.children).some(isVisible);
      const storyContentSelector = [
        "article",
        "button",
        "canvas",
        "input",
        "main",
        "nav",
        "section",
        "svg",
        "textarea",
        "[role='dialog']",
        ".strategy-graph",
      ].join(",");
      const hasStoryContent =
        root.textContent.trim().length > 0 ||
        root.querySelector(storyContentSelector) != null;
      const centerElement = document.elementFromPoint(
        window.innerWidth / 2,
        window.innerHeight / 2,
      );
      const centerIsStorybookState =
        centerElement?.closest(
          ".sb-loader, .sb-preparing-story, .sb-errordisplay, .sb-nopreview",
        ) != null;

      return (
        document.body.classList.contains("sb-show-main") &&
        !visibleStorybookState &&
        visibleStoryRoot &&
        hasStoryContent &&
        !centerIsStorybookState
      );
    },
    undefined,
    { timeout: 15_000 },
  );
}

async function expectResizableLayoutFillsVisibleWorkspace(page: Page) {
  const viewport = page.viewportSize();
  const rootBox = await page.locator("#storybook-root").boundingBox();
  if (!viewport || !rootBox) {
    throw new Error("Storybook viewport was not available");
  }

  const graphBox = await page
    .locator(".conversation-view__panel--graph .strategy-graph")
    .boundingBox();
  if (!graphBox) {
    throw new Error("Desktop graph panel was not available");
  }

  expect(Math.abs(graphBox.y - rootBox.y)).toBeLessThanOrEqual(1);
  expect(
    Math.abs(graphBox.y + graphBox.height - (rootBox.y + rootBox.height)),
  ).toBeLessThanOrEqual(1);
}

async function waitForStoryReady(page: Page) {
  const graph = page.locator(".strategy-graph");
  if ((await graph.count()) === 0) {
    await page.waitForTimeout(100);
    return;
  }
  const firstGraph = graph.first();
  if (!(await firstGraph.isVisible())) {
    await page.waitForTimeout(100);
    return;
  }

  const nodeCount = await firstGraph.locator(".strategy-graph-node").count();
  const graphHasTerminalState =
    (await firstGraph.locator(".strategy-graph__empty").count()) > 0 ||
    ((await firstGraph.locator(".strategy-graph__job-card").count()) > 0 &&
      nodeCount === 0);

  if (!graphHasTerminalState) {
    await expect(
      firstGraph.locator(".strategy-graph-node").first(),
    ).toBeVisible({ timeout: 15_000 });
  }
}
