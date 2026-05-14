# User-Visible Behavior Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect the local CLI and workbench user journeys with deterministic E2E, fixture-backed Storybook states, and visual regression gates.

**Architecture:** Build safe fixture states once, reuse them across Playwright E2E, Storybook stories, and visual tests. Keep live provider smoke manual and separate from deterministic CI.

**Tech Stack:** Bun, Vite, React 19, TypeScript, Storybook, Playwright, Vitest, Testing Library, odiff-bin, pytest fixtures.

**Spec:** `docs/superpowers/specs/2026-05-13-user-visible-behavior-harness-design.md`

---

## File Structure

- Modify: `apps/web/package.json`
  - Add Storybook and E2E scripts.
- Add: `apps/web/.storybook/main.ts`
- Add: `apps/web/.storybook/preview.ts`
- Add: `apps/web/src/fixtures/workbenchFixtures.ts`
  - Safe committed UI fixtures.
- Add: `apps/web/e2e/workbenchFixtureApi.ts`
  - Playwright API route fixture helpers for deterministic workbench states.
- Add: `apps/web/src/stories/AppShell.stories.tsx`
- Add: `apps/web/src/stories/StrategyGraph.stories.tsx`
- Add: `apps/web/src/stories/NodeDetailPanel.stories.tsx`
- Add: `apps/web/e2e/workbench.spec.ts`
- Modify: `apps/web/playwright.config.ts`
  - Add deterministic E2E project or keep visual project separate with named config.
- Modify: `docs/ui.md`
  - Document E2E, Storybook, visual, and live smoke boundaries.
- Test: existing `apps/web/src/*.test.tsx` and new Playwright tests.

## Task 1: Add Safe Workbench Fixtures

**Files:**

- Add: `apps/web/src/fixtures/workbenchFixtures.ts`
- Modify: `apps/web/src/runStory.test.ts`

- [ ] **Step 1: Add fixture file**

  Create fixture exports:

  ```ts
  export const ctsOnlyWorkbenchFixture = {
    sessionId: "session_fixture_cts",
    jobTitle: "Python Agent Engineer",
    sources: [{ sourceKind: "cts", status: "completed" }],
    session: {
      sessionId: "session_fixture_cts",
      jobTitle: "Python Agent Engineer",
      jdText: "Build Python retrieval and agent workflows.",
      notes: "Focus on typed runtime and provider-safe automation.",
      status: "completed",
      sourceKinds: ["cts"],
    },
    eventsPage: {
      events: [
        {
          seq: 1,
          eventType: "workbench_note",
          payload: { message: "检索已完成，结果已整理到策略图和节点详情。", stage: "final" },
        },
      ],
      nextAfterSeq: 1,
    },
    candidatesPage: {
      candidates: [
        {
          reviewItemId: "candidate_fixture_1",
          displayName: "候选人 A",
          headline: "Python Agent Engineer",
          score: 91,
          stage: "shortlisted",
        },
      ],
    },
  } as const;

  export const ctsLiepinWorkbenchFixture = {
    sessionId: "session_fixture_liepin",
    jobTitle: "AI Recruiter Engineer",
    sources: [
      { sourceKind: "cts", status: "completed" },
      { sourceKind: "liepin", status: "blocked", authState: "login_required" },
    ],
    session: {
      sessionId: "session_fixture_liepin",
      jobTitle: "AI Recruiter Engineer",
      jdText: "Build local-first recruiting workflows.",
      notes: "Liepin login required before source run.",
      status: "blocked",
      sourceKinds: ["cts", "liepin"],
    },
    eventsPage: {
      events: [
        {
          seq: 1,
          eventType: "workbench_note",
          payload: { message: "猎聘需要重新登录后才能继续。", stage: "liepin_login" },
        },
      ],
      nextAfterSeq: 1,
    },
    candidatesPage: { candidates: [] },
  } as const;
  ```

  Expand the fixture with safe event payloads already used by `runStory` tests. Do not include real names, cookies, auth headers, storage state, raw provider payloads, or raw resume text.

- [ ] **Step 2: Add fixture safety test**

  In `runStory.test.ts`, add:

  ```ts
  it("committed workbench fixtures do not contain provider secrets", () => {
    const text = JSON.stringify([ctsOnlyWorkbenchFixture, ctsLiepinWorkbenchFixture]).toLowerCase();

    expect(text).not.toContain("cookie");
    expect(text).not.toContain("authorization");
    expect(text).not.toContain("storage_state");
    expect(text).not.toContain("raw provider");
    expect(text).not.toContain("cdp");
  });
  ```

- [ ] **Step 3: Run test**

  ```bash
  cd apps/web && bun run test -- --run src/runStory.test.ts
  ```

  Expected: pass.

- [ ] **Step 4: Commit**

  ```bash
  git add apps/web/src/fixtures/workbenchFixtures.ts apps/web/src/runStory.test.ts
  git commit -m "test: add safe workbench fixtures"
  ```

## Task 2: Add Storybook Component Isolation

**Files:**

- Modify: `apps/web/package.json`
- Add: `apps/web/.storybook/main.ts`
- Add: `apps/web/.storybook/preview.ts`
- Add: `apps/web/src/stories/AppShell.stories.tsx`
- Add: `apps/web/src/stories/StrategyGraph.stories.tsx`
- Add: `apps/web/src/stories/NodeDetailPanel.stories.tsx`

- [ ] **Step 1: Add scripts and dependencies**

  Add scripts:

  ```json
  {
    "storybook": "storybook dev -p 6006 --host 127.0.0.1",
    "build:storybook": "storybook build"
  }
  ```

  Install the Vite Storybook packages with Bun.

- [ ] **Step 2: Add Storybook config**

  `main.ts`:

  ```ts
  import type { StorybookConfig } from "@storybook/react-vite";

  const config: StorybookConfig = {
    stories: ["../src/stories/**/*.stories.@(ts|tsx)"],
    framework: "@storybook/react-vite",
    addons: ["@storybook/addon-a11y"],
  };

  export default config;
  ```

- [ ] **Step 3: Add stories**

  Stories must cover:

  - CTS-only strategy graph;
  - CTS+Liepin blocked/login state;
  - selected reflection node;
  - selected final shortlist node;
  - detail approval node;
  - empty and error states.

- [ ] **Step 4: Run Storybook build**

  ```bash
  cd apps/web && bun run build:storybook
  ```

  Expected: build succeeds.

- [ ] **Step 5: Commit**

  ```bash
  git add apps/web/package.json apps/web/bun.lock apps/web/.storybook apps/web/src/stories
  git commit -m "test: add workbench storybook stories"
  ```

## Task 3: Add Deterministic Workbench E2E

**Files:**

- Add: `apps/web/e2e/workbench.spec.ts`
- Add: `apps/web/e2e/workbenchFixtureApi.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/playwright.config.ts`

- [ ] **Step 1: Add script**

  Add:

  ```json
  {
    "test:e2e": "playwright test --config=playwright.config.ts --project=workbench-e2e"
  }
  ```

- [ ] **Step 2: Add Playwright project**

  Add a `workbench-e2e` project with Chromium and a deterministic dev-server setup. Keep it separate from visual baseline projects.

- [ ] **Step 3: Add fixture API helper**

  Add `apps/web/e2e/workbenchFixtureApi.ts`:

  ```ts
  import type { Page, Route } from "@playwright/test";

  import { ctsOnlyWorkbenchFixture } from "../src/fixtures/workbenchFixtures";

  const sessionId = ctsOnlyWorkbenchFixture.sessionId;

  async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  }

  export async function routeFixtureWorkbenchApi(page: Page): Promise<void> {
    await page.route("**/api/auth/me", (route) =>
      fulfillJson(route, { user: { email: "admin@example.com", displayName: "Admin" } }),
    );
    await page.route("**/api/workbench/sessions", async (route) => {
      if (route.request().method() === "POST") {
        await fulfillJson(route, {
          sessionId: "session_created_e2e",
          jobTitle: "Python Agent Engineer",
          status: "created",
          sourceKinds: ["cts"],
        });
        return;
      }
      await fulfillJson(route, { sessions: [ctsOnlyWorkbenchFixture.session] });
    });
    await page.route(`**/api/workbench/sessions/${sessionId}`, (route) =>
      fulfillJson(route, ctsOnlyWorkbenchFixture.session),
    );
    await page.route(`**/api/workbench/sessions/${sessionId}/events**`, (route) =>
      fulfillJson(route, ctsOnlyWorkbenchFixture.eventsPage),
    );
    await page.route(`**/api/workbench/sessions/${sessionId}/candidates**`, (route) =>
      fulfillJson(route, ctsOnlyWorkbenchFixture.candidatesPage),
    );
    await page.route("**/api/workbench/events**", (route) =>
      fulfillJson(route, ctsOnlyWorkbenchFixture.eventsPage),
    );
  }
  ```

- [ ] **Step 4: Write E2E tests**

  `apps/web/e2e/workbench.spec.ts` should split creation behavior from completed-run inspection. Completed-run assertions must use a fixture session, not a newly created empty session:

  ```ts
  import { expect, test } from "@playwright/test";

  import { routeFixtureWorkbenchApi } from "./workbenchFixtureApi";

  test("user can create a session through the setup path", async ({ page }) => {
    await routeFixtureWorkbenchApi(page);

    await page.goto("/setup");
    await page.getByLabel("Email").fill("admin@example.com");
    await page.getByLabel("Display name").fill("Admin");
    await page.getByLabel("Password").fill("correct horse battery staple");
    await page.getByRole("button", { name: "Create admin" }).click();

    await page.getByRole("button", { name: "New session" }).click();
    await page.getByLabel("Job title").fill("Python Agent Engineer");
    await page.getByLabel("JD").fill("Build Python retrieval and agent workflows.");
    await page.getByRole("button", { name: "Create session" }).click();

    await expect(page.getByText("Python Agent Engineer")).toBeVisible();
  });

  test("fixture completed run exposes running notes and final shortlist detail", async ({ page }) => {
    await routeFixtureWorkbenchApi(page);

    await page.goto("/sessions/session_fixture_cts");
    await page.getByText("运行笔记").waitFor();
    await page.getByText("节点详情").click();
    await expect(page.getByText("最终短名单")).toBeVisible();
  });
  ```

  The labels in this snippet match the current `apps/web/src/app.tsx` setup and session form. If implementation changes labels first, update the E2E to the new accessible labels in the same change.

- [ ] **Step 5: Run E2E**

  ```bash
  cd apps/web && bun run test:e2e
  ```

  Expected: pass.

- [ ] **Step 6: Commit**

  ```bash
  git add apps/web/package.json apps/web/playwright.config.ts apps/web/e2e/workbench.spec.ts apps/web/e2e/workbenchFixtureApi.ts
  git commit -m "test: add workbench e2e journey"
  ```

## Task 4: Expand Visual Regression Around Story States

**Files:**

- Modify: `apps/web/playwright.config.ts`
- Add or modify: `apps/web/e2e/workbench-visual.spec.ts`
- Modify: `docs/ui.md`

- [ ] **Step 1: Add visual states**

  Cover:

  - desktop app shell;
  - tablet node detail reachability;
  - CTS-only selected scoring node;
  - CTS+Liepin detail approval node;
  - login blocked source card.

- [ ] **Step 2: Run visual tests**

  ```bash
  cd apps/web && bun run test:visual
  ```

  Expected: pass against existing baselines or fail with intentional diffs.

- [ ] **Step 3: Update baselines after manual review**

  Only after confirming the diffs are intended:

  ```bash
  cd apps/web && UPDATE_VISUAL_BASELINES=1 bun run test:visual
  ```

- [ ] **Step 4: Document harness split**

  In `docs/ui.md`, describe:

  - unit tests;
  - deterministic E2E;
  - visual regression;
  - Storybook;
  - live Liepin smoke.

- [ ] **Step 5: Commit**

  ```bash
  git add apps/web/playwright.config.ts apps/web/e2e docs/ui.md
  git commit -m "test: expand workbench visual harness"
  ```

## Self-Review

- Spec coverage: CLI/Workbench E2E, Storybook, visual regression, safe fixtures, and live smoke separation are covered.
- Placeholder scan: every story/test surface is named with files and commands.
- Type consistency: fixture file is introduced before stories and E2E depend on it.
