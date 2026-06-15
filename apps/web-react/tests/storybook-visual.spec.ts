import { expect, test, type Page } from "@playwright/test";

const visualStories = [
  {
    name: "primitives-controls-gallery",
    url: "/iframe.html?id=primitives-controlsgallery--controls-gallery",
  },
  {
    name: "primitives-dialog-open",
    url: "/iframe.html?id=primitives-controlsgallery--dialog-open",
  },
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
    name: "workbench-strategy-graph-large",
    url: "/iframe.html?id=workbench-strategygraphcanvas--large-search-strategy",
  },
  {
    name: "workbench-thinking-process",
    url: "/iframe.html?id=workbench-thinkingprocessrail--round-timeline",
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
] as const;

for (const story of visualStories) {
  test(`${story.name} matches the Storybook visual baseline`, async ({
    page,
  }) => {
    await page.goto(story.url);
    await page.waitForSelector("#storybook-root");
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    await waitForStoryReady(page);

    await expect(page.locator("#storybook-root")).toHaveScreenshot(
      `${story.name}.png`,
    );
  });
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

  await expect(firstGraph).toHaveAttribute("aria-busy", "false", {
    timeout: 15_000,
  });

  const graphHasTerminalState =
    (await firstGraph
      .locator(".strategy-graph__empty, .strategy-graph__error")
      .count()) > 0;

  if (!graphHasTerminalState) {
    await expect(page.locator(".react-flow__node").first()).toBeVisible({
      timeout: 15_000,
    });
  }
}
