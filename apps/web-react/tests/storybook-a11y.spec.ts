import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { expect, type Page, test } from "@playwright/test";
import { failOnPageProblems } from "./pageProblems";

const require = createRequire(import.meta.url);
const axeSource = await readFile(
  require.resolve("axe-core/axe.min.js"),
  "utf8",
);

test.beforeEach(({ page }) => {
  failOnPageProblems(page);
});

const stories = [
  [
    "workbench shell figma reference",
    "/iframe.html?id=workbench-workbenchshell--figma-thumbnail-reference",
  ],
  ["home start initial", "/iframe.html?id=workbench-homestartpanel--initial"],
  [
    "composer requirement draft",
    "/iframe.html?id=workbench-composer--requirement-draft",
  ],
  [
    "requirement review",
    "/iframe.html?id=workbench-requirementreviewpanel--needs-confirmation",
  ],
  [
    "strategy graph",
    "/iframe.html?id=workbench-strategygraphcanvas--search-strategy",
  ],
  [
    "strategy graph canonical",
    "/iframe.html?id=workbench-strategygraphcanvas--canonical-runtime-swimlanes",
  ],
  [
    "strategy graph large",
    "/iframe.html?id=workbench-strategygraphcanvas--large-search-strategy",
  ],
  [
    "thinking process",
    "/iframe.html?id=workbench-thinkingprocessrail--round-timeline",
  ],
  ["candidate queue empty", "/iframe.html?id=workbench-candidatequeue--empty"],
  [
    "candidate queue populated",
    "/iframe.html?id=workbench-candidatequeue--populated",
  ],
  [
    "candidate queue loading",
    "/iframe.html?id=workbench-candidatequeue--loading",
  ],
  ["candidate queue error", "/iframe.html?id=workbench-candidatequeue--error"],
  [
    "candidate detail",
    "/iframe.html?id=workbench-candidatedetaildrawer--summary",
  ],
  [
    "resume full content",
    "/iframe.html?id=workbench-resumeevidencepanel--full-content",
  ],
  [
    "transcript collapsed run group",
    "/iframe.html?id=workbench-transcript--collapsed-run-group",
  ],
  [
    "transcript expanded run group",
    "/iframe.html?id=workbench-transcript--expanded-run-group",
  ],
  [
    "transcript tool detail",
    "/iframe.html?id=workbench-transcript--tool-read-details",
  ],
  [
    "transcript web running",
    "/iframe.html?id=workbench-transcript--web-search-running",
  ],
  [
    "transcript file complete",
    "/iframe.html?id=workbench-transcript--file-search-complete",
  ],
  [
    "transcript file running",
    "/iframe.html?id=workbench-transcript--file-read-running",
  ],
  [
    "transcript guided followup",
    "/iframe.html?id=workbench-transcript--guided-followup",
  ],
  [
    "conversation screen initial",
    "/iframe.html?id=workbench-conversationscreen--initial",
  ],
  [
    "conversation screen requirement review",
    "/iframe.html?id=workbench-conversationscreen--requirement-review",
  ],
  [
    "conversation screen running stream",
    "/iframe.html?id=workbench-conversationscreen--running-with-stream",
  ],
  [
    "conversation screen source expired",
    "/iframe.html?id=workbench-conversationscreen--source-expired",
  ],
  [
    "conversation screen permission denied",
    "/iframe.html?id=workbench-conversationscreen--permission-denied",
  ],
  [
    "conversation screen failed",
    "/iframe.html?id=workbench-conversationscreen--failed",
  ],
  [
    "conversation screen completed",
    "/iframe.html?id=workbench-conversationscreen--completed",
  ],
  [
    "conversation screen archived",
    "/iframe.html?id=workbench-conversationscreen--archived",
  ],
  [
    "conversation screen composed shell",
    "/iframe.html?id=workbench-conversationscreen--workbench-shell-composed",
  ],
] as const;

for (const [name, storyUrl] of stories) {
  test(`${name} story has no WCAG AA axe violations`, async ({ page }) => {
    await page.goto(storyUrl);
    await page.waitForSelector("#storybook-root");
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    await waitForStoryReady(page);
    await ensureAxe(page);

    const violations = await runAxe(page);

    expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
  });
}

async function ensureAxe(page: Page) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const ready = await page
        .evaluate(() => {
          const maybeWindow = window as unknown as {
            axe?: { run?: unknown };
          };
          return typeof maybeWindow.axe?.run === "function";
        })
        .catch(() => false);
      if (ready) {
        return;
      }

      await page.addScriptTag({ content: axeSource });

      const injected = await page
        .evaluate(() => {
          const maybeWindow = window as unknown as {
            axe?: { run?: unknown };
          };
          return typeof maybeWindow.axe?.run === "function";
        })
        .catch(() => false);
      if (injected) {
        return;
      }
    } catch (error) {
      if (!isRetryableAxeError(error) || attempt === 4) {
        throw error;
      }
    }
    await page.waitForTimeout(250);
  }
  throw new Error("axe.run was not available in the Storybook iframe.");
}

async function runAxe(page: Page) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      return await page.evaluate(async () => {
        type AxeViolation = {
          id: string;
          impact: string | null;
          nodes: Array<{ target: string[] }>;
        };
        const axe = (
          window as unknown as {
            axe: {
              run: (
                context: Document,
                options: unknown,
              ) => Promise<{ violations: AxeViolation[] }>;
            };
          }
        ).axe;
        const result = await axe.run(document, {
          runOnly: {
            type: "tag",
            values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
          },
        });
        return result.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          targets: violation.nodes.map((node) => node.target.join(" ")),
        }));
      });
    } catch (error) {
      if (isRetryableAxeError(error) && attempt < 4) {
        await ensureAxe(page);
        await page.waitForTimeout(250);
        continue;
      }
      throw error;
    }
  }
  return [];
}

function isRetryableAxeError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return (
    error.message.includes("Axe is already running") ||
    error.message.includes("Cannot read properties of undefined") ||
    error.message.includes("axe.run is not a function") ||
    error.message.includes("Execution context was destroyed")
  );
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

  const graphHasTerminalState =
    (await firstGraph.locator(".strategy-graph__empty").count()) > 0;

  if (!graphHasTerminalState) {
    await expect(
      firstGraph.locator(".strategy-graph-node").first(),
    ).toBeVisible({
      timeout: 15_000,
    });
  }
}
