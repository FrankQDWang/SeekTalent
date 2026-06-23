# Phase 1: Resizable Chat↔Graph Panels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the fixed CSS Grid chat↔graph split with `react-resizable-panels` v4 (Group/Panel/Separator), making panel widths user-draggable and persistent.

**Architecture:** Minimal surgical change to `ConversationScreen.tsx` — swap the `data-workflow-surface="visible"` grid for a `Group` with two `Panel`s and one `Separator`. No route changes, no animations, no Pretext.

**Tech Stack:** React 19, `react-resizable-panels@^4.11.2`

**Base branch:** `codex/conversation-agent-controlled-orchestration` (476288f2)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/web-react/package.json` | Modify | Add `react-resizable-panels` dependency |
| `apps/web-react/src/components/workbench/ConversationScreen.tsx` | Modify | Import Group/Panel/Separator, replace CSS grid in `!compactWorkspace && workflowSurfaceVisible` path |
| `apps/web-react/src/components/workbench/ConversationScreen.css` | Modify | Remove `grid-template-columns` rules, add workspace-group/panel/separator styles |
| `apps/web-react/src/components/workbench/ConversationScreen.test.tsx` | Modify | Add "renders resize separator" test |
| `apps/web-react/src/components/workbench/ConversationScreen.stories.tsx` | Modify | Add `ResizableLayout` story |
| `apps/web-react/tests/storybook-visual.spec.ts` | Modify | Add `workbench-resizable-layout` entry |

---

### Task 1: Install react-resizable-panels

**Files:**
- Modify: `apps/web-react/package.json`

- [ ] **Step 1: Install the package**

```bash
cd apps/web-react && pnpm add react-resizable-panels
```

Expected output: package added to `package.json` dependencies.

- [ ] **Step 2: Commit**

```bash
git add apps/web-react/package.json apps/web-react/pnpm-lock.yaml
git commit -m "chore: add react-resizable-panels"
```

---

### Task 2: Replace CSS Grid with Group/Panel/Separator in ConversationScreen

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.tsx`

- [ ] **Step 1: Add imports**

Add to the import block in `ConversationScreen.tsx`:

```tsx
import { Group, Panel, Separator } from "react-resizable-panels";
```

- [ ] **Step 2: Replace the workspace rendering for non-compact + visible workflow surface**

In the `ConversationScreen` component's return block, find the `<div className="conversation-view__workspace">` section. The current code conditionally renders a grid when `workflowSurfaceVisible` is true and compact mode is off.

Replace the current workspace content block (lines 97-162) with:

```tsx
        {compactWorkspace && workflowSurfaceVisible ? (
          <div
            className="conversation-view__workspace"
            data-active-panel={activePanel}
            data-workflow-surface="visible"
          >
            <section
              aria-labelledby="conversation-chat-tab"
              className="conversation-view__panel conversation-view__panel--chat"
              data-panel="chat"
              id="conversation-panel-chat"
              role="tabpanel"
            >
              <Transcript groups={view.transcriptGroups}>
                {requirementReviewPanel}
              </Transcript>
              <MessageComposer
                disabled={!view.pendingActions.allowed.includes("submit_message")}
                loading={submittingMessage}
                onSubmit={onSubmitMessage}
              />
            </section>
            <section
              aria-labelledby="conversation-graph-tab"
              className="conversation-view__panel conversation-view__panel--graph"
              data-panel="graph"
              id="conversation-panel-graph"
              role="tabpanel"
            >
              {shouldMountGraph ? (
                <StrategyGraph
                  graph={view.strategyGraph}
                  jobTitle={view.conversation.title}
                  key={activePanel === "graph" ? "graph-active" : "graph-inactive"}
                />
              ) : null}
            </section>
            <section
              aria-labelledby="conversation-candidates-tab"
              className="conversation-view__panel conversation-view__panel--candidates"
              data-panel="candidates"
              id="conversation-panel-candidates"
              role="tabpanel"
            >
              <ConversationScreenSide
                onViewCandidateDetails={onViewCandidateDetails}
                view={view}
              />
            </section>
            <section
              aria-labelledby="conversation-final-tab"
              className="conversation-view__panel conversation-view__panel--final"
              data-panel="final"
              id="conversation-panel-final"
              role="tabpanel"
            >
              <FinalReviewPanel view={view} />
            </section>
          </div>
        ) : workflowSurfaceVisible ? (
          <Group autoSaveId="chat-graph-layout" className="workspace-group" direction="horizontal">
            <Panel className="workspace-panel workspace-panel--chat" defaultSize={386} minSize={280} maxSize="50%">
              <section
                aria-labelledby="conversation-chat-tab"
                className="conversation-view__panel conversation-view__panel--chat"
                data-panel="chat"
                id="conversation-panel-chat"
                role="tabpanel"
              >
                <Transcript groups={view.transcriptGroups}>
                  {requirementReviewPanel}
                </Transcript>
                <MessageComposer
                  disabled={!view.pendingActions.allowed.includes("submit_message")}
                  loading={submittingMessage}
                  onSubmit={onSubmitMessage}
                />
              </section>
            </Panel>
            <Separator className="workspace-separator" />
            <Panel className="workspace-panel workspace-panel--graph" minSize={400}>
              <section
                aria-labelledby="conversation-graph-tab"
                className="conversation-view__panel conversation-view__panel--graph"
                data-panel="graph"
                id="conversation-panel-graph"
                role="tabpanel"
              >
                {shouldMountGraph ? (
                  <StrategyGraph
                    graph={view.strategyGraph}
                    jobTitle={view.conversation.title}
                    key="graph-active"
                  />
                ) : null}
              </section>
            </Panel>
          </Group>
        ) : (
          <div
            className="conversation-view__workspace"
            data-workflow-surface="hidden"
          >
            <section
              aria-labelledby="conversation-chat-tab"
              className="conversation-view__panel conversation-view__panel--chat"
              data-panel="chat"
              id="conversation-panel-chat"
              role="tabpanel"
            >
              <Transcript groups={view.transcriptGroups}>
                {requirementReviewPanel}
              </Transcript>
              <MessageComposer
                disabled={!view.pendingActions.allowed.includes("submit_message")}
                loading={submittingMessage}
                onSubmit={onSubmitMessage}
              />
            </section>
          </div>
        )}
```

- [ ] **Step 3: Verify imports are correct**

The component needs `useCompactWorkspace` (already used), `hasConversationWorkflowSurface` (already used). No new hooks needed.

- [ ] **Step 4: Run type check**

```bash
cd apps/web-react && npx tsc -b --pretty false
```

Expected: No type errors.

- [ ] **Step 5: Run tests**

```bash
cd apps/web-react && npx vitest run
```

Expected: All existing tests pass (101 tests, 0 failures). Note: the existing compact mode test at line 103 mocks `matchMedia` to return `matches: true`, so it exercises the compact path — the non-compact resizable path runs for other tests.

- [ ] **Step 6: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.tsx
git commit -m "feat: replace CSS grid with resizable Group/Panel/Separator"
```

---

### Task 3: Update ConversationScreen.css

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.css`

- [ ] **Step 1: Replace grid rules with workspace-group styles**

Remove lines 16-24:

```css
/* Remove these */
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: grid;
  gap: 0;
  grid-template-columns: 386px minmax(0, 1fr);
}

.conversation-view__workspace[data-workflow-surface="hidden"] {
  display: block;
}
```

Replace with:

```css
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: flex;
  flex: 1;
  min-height: 0;
}

.conversation-view__workspace[data-workflow-surface="hidden"] {
  display: block;
}
```

- [ ] **Step 2: Add workspace-group, workspace-panel, and workspace-separator rules**

At the end of `ConversationScreen.css`, add:

```css
/* --- Resizable panel layout (react-resizable-panels) --- */

.workspace-group {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.workspace-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.workspace-panel--chat {
  background: rgb(246 248 252);
}

.workspace-panel--chat .transcript {
  background: rgb(246 248 252);
}

.workspace-separator {
  background: transparent;
  flex-shrink: 0;
  outline: none;
  transition: background 150ms;
  width: 4px;
}

.workspace-separator:hover {
  background: var(--st-action);
  cursor: col-resize;
}

.workspace-separator[data-separator] {
  background: var(--st-action);
  cursor: col-resize;
}

.workspace-separator:focus-visible {
  background: var(--st-action);
  outline: 2px solid var(--st-action);
  outline-offset: 2px;
}
```

- [ ] **Step 3: Run style check**

```bash
cd apps/web-react && npx vitest run
```

Expected: All tests still pass (style-only change to existing CSS selectors should not affect tests).

- [ ] **Step 4: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.css
git commit -m "style: add resizable panel layout CSS rules"
```

---

### Task 4: Update Tests

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.test.tsx`

- [ ] **Step 1: Add "renders resize separator" test**

Add at the end of the `ConversationScreen` describe block (before the `ConversationScreenSide` describe block):

```tsx
  it("renders a resize separator between chat and graph when not compact", () => {
    expect.hasAssertions();

    vi.stubGlobal("matchMedia", (query: string) => ({
      addEventListener: vi.fn(),
      addListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
      removeListener: vi.fn(),
    }));

    render(
      <ConversationScreen
        onSubmitMessage={vi.fn()}
        view={agentWorkbenchRunningViewFixture}
      />,
    );

    // The Separator renders as a div with class workspace-separator
    const separator = document.querySelector(".workspace-separator");
    expect(separator).not.toBeNull();
    expect(screen.getByLabelText("检索策略图")).toBeVisible();
  });
```

- [ ] **Step 2: Run tests**

```bash
cd apps/web-react && npx vitest run
```

Expected: All tests pass (101+1 = 102 tests).

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.test.tsx
git commit -m "test: add resize separator rendering test"
```

---

### Task 5: Add Storybook Story

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.stories.tsx`

- [ ] **Step 1: Find the ConversationScreen stories file**

Read it to understand the existing pattern:

```bash
cat apps/web-react/src/components/workbench/ConversationScreen.stories.tsx
```

- [ ] **Step 2: Add a `ResizableLayout` story**

Following the existing pattern (simple `view` prop, no render override), add:

```tsx
export const ResizableLayout: Story = {
  args: {
    view: agentWorkbenchRunningViewFixture,
  },
};
```

This story renders the two-panel resizable layout with the running workbench view fixture. The `RunningWithStream` story already renders the same fixture, but in Chromatic/Playwright the resize separator will be visible between the chat and graph panels when the viewport is ≥1081px.

- [ ] **Step 3: Run Storybook build to verify**

```bash
cd apps/web-react && npx storybook build
```

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.stories.tsx
git commit -m "story: add ResizableLayout story for ConversationScreen"
```

---

### Task 6: Add Playwright Screenshot Entry

**Files:**
- Modify: `apps/web-react/tests/storybook-visual.spec.ts`

- [ ] **Step 1: Add the resizable layout entry**

After the existing `workbench-strategy-graph-large` entry (around line 37), add:

```tsx
  {
    name: "workbench-resizable-layout",
    url: "/iframe.html?id=workbench-conversationscreen--resizable-layout",
  },
```

- [ ] **Step 2: Update the test count assertion**

Find the existing `test.describe` assertion for the count and update it. Each entry generates 3 viewport screenshots (desktop, tablet, mobile). If the previous count was 17 entries × 3 = 51, the new count is 18 entries × 3 = 54.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/tests/storybook-visual.spec.ts
git commit -m "test: add resizable layout Playwright screenshot entry"
```

---

### Task 7: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd apps/web-react && npx vitest run
```

Expected: All tests pass, 0 failures.

- [ ] **Step 2: TypeScript check**

```bash
cd apps/web-react && npx tsc -b --pretty false
```

Expected: No type errors.

- [ ] **Step 3: Build check**

```bash
cd apps/web-react && npx vite build
```

Expected: Build succeeds.

- [ ] **Step 4: Verify git status**

```bash
git status
```

Expected: Working tree clean.

- [ ] **Step 5: Final commit**

```bash
git add -A && git commit -m "chore: final Phase 1 verification pass" || echo "Nothing to commit"
```

---

## Verification Checklist

1. `pnpm test` — all tests pass
2. `tsc -b` — no type errors
3. `vite build` — builds successfully
4. Navigate to `/conversations/agent_conv_1` — chat and graph panels have a 4px draggable separator
5. Hover over separator — blue highlight + `col-resize` cursor
6. Drag separator — panels resize proportionally
7. Chat panel cannot go below 280px or above 50%
8. Graph panel cannot go below 400px
9. Refresh page — panel positions restored from localStorage
10. ≤1080px viewport — no separator, tab layout works unchanged

## Phase 2 (future)

- Route merging (`/` + `/conversations/$id` → single route with stage state)
- HomeStartPanel collapse animation
- Pretext text measurement integration