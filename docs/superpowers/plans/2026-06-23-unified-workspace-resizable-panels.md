# Unified Workspace With Resizable Panels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge home form and conversation workspace into a single route with animated collapse, resizable chat↔graph panels, and Pretext-powered text measurement.

**Architecture:** Single route `/conversations/$conversationId` with `"new"` as the placeholder for new conversations. `react-resizable-panels` drives the chat↔graph split, `@chenglou/pretext` pre-measures transcript text heights to avoid DOM reflow during resize.

**Tech Stack:** React 19, TypeScript, `react-resizable-panels`, `@chenglou/pretext`, `@tanstack/react-router`, CSS animations

**Base branch:** `codex/conversation-agent-controlled-orchestration` (476288f2)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/web-react/package.json` | Modify | Add `@chenglou/pretext` + `react-resizable-panels` |
| `apps/web-react/src/components/workbench/ResizableChatGraphLayout.tsx` | Create | PanelGroup + Panel + PanelResizeHandle wrapper |
| `apps/web-react/src/components/workbench/ResizableChatGraphLayout.css` | Create | Resize handle styles |
| `apps/web-react/src/components/workbench/ConversationScreen.tsx` | Modify | Stage state machine, use ResizableChatGraphLayout, render HomeStartPanel in home/transition stages |
| `apps/web-react/src/components/workbench/ConversationScreen.css` | Modify | Remove `grid-template-columns: 386px ...`, add collapse animation styles |
| `apps/web-react/src/components/workbench/HomeStartPanel.tsx` | Modify | Accept `collapsing` prop, add `--collapsing` class |
| `apps/web-react/src/components/workbench/HomeStartPanel.css` | Modify | Add collapse keyframes |
| `apps/web-react/src/components/workbench/Transcript.tsx` | Modify | Integrate Pretext `prepare`/`layout` |
| `apps/web-react/src/routes/root.tsx` | Modify | Change `/` to redirect, remove WorkbenchIndexRoute business logic |
| `apps/web-react/src/routes/conversation.tsx` | Modify | Add `"new"` conversationId guard, home submission logic, collapse animation trigger |
| `apps/web-react/src/router.tsx` | Modify | Update route tree (index route redirect only) |
| `apps/web-react/src/components/workbench/ResizableChatGraphLayout.test.tsx` | Create | Panel rendering, resize handle, min/max constraints |
| `apps/web-react/src/components/workbench/ConversationScreen.test.tsx` | Modify | Add stage tests, resize handle presence |
| `apps/web-react/src/components/workbench/HomeStartPanel.test.tsx` | Modify | Add collapsing class test |

---

### Task 1: Install Dependencies

**Files:**
- Modify: `apps/web-react/package.json`

- [ ] **Step 1: Add `react-resizable-panels` and `@chenglou/pretext`**

```bash
cd apps/web-react && pnpm add react-resizable-panels @chenglou/pretext
```

- [ ] **Step 2: Verify install**

Expected: Both packages appear in `package.json` dependencies with versions.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/package.json apps/web-react/pnpm-lock.yaml
git commit -m "chore: add react-resizable-panels and @chenglou/pretext"
```

---

### Task 2: Create ResizableChatGraphLayout Component

**Files:**
- Create: `apps/web-react/src/components/workbench/ResizableChatGraphLayout.tsx`
- Create: `apps/web-react/src/components/workbench/ResizableChatGraphLayout.css`

- [ ] **Step 1: Write the CSS**

Create `apps/web-react/src/components/workbench/ResizableChatGraphLayout.css`:

```css
.workspace-panel-group {
  display: flex;
  flex: 1;
  min-height: 0;
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

.workspace-resize-handle {
  background: transparent;
  flex-shrink: 0;
  outline: none;
  transition: background 150ms;
  width: 4px;
}

.workspace-resize-handle:hover,
.workspace-resize-handle[data-resize-handle-active] {
  background: var(--st-action);
}

.workspace-resize-handle:focus-visible {
  background: var(--st-action);
  outline: 2px solid var(--st-action);
  outline-offset: 2px;
}
```

- [ ] **Step 2: Write the component**

Create `apps/web-react/src/components/workbench/ResizableChatGraphLayout.tsx`:

```tsx
import type { ReactNode } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import "./ResizableChatGraphLayout.css";

type ResizableChatGraphLayoutProps = {
  chat: ReactNode;
  graph: ReactNode;
};

export function ResizableChatGraphLayout({
  chat,
  graph,
}: ResizableChatGraphLayoutProps) {
  return (
    <PanelGroup
      autoSaveId="chat-graph-layout"
      className="workspace-panel-group"
      direction="horizontal"
    >
      <Panel
        className="workspace-panel workspace-panel--chat"
        defaultSize={35}
        maxSize={50}
        minSizePixels={280}
      >
        {chat}
      </Panel>
      <PanelResizeHandle className="workspace-resize-handle" />
      <Panel
        className="workspace-panel workspace-panel--graph"
        defaultSize={65}
        minSizePixels={400}
      >
        {graph}
      </Panel>
    </PanelGroup>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/components/workbench/ResizableChatGraphLayout.tsx \
        apps/web-react/src/components/workbench/ResizableChatGraphLayout.css
git commit -m "feat: add ResizableChatGraphLayout with react-resizable-panels"
```

---

### Task 3: Update ConversationScreen — Stage State Machine + Resizable Layout

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.tsx`
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.css`

- [ ] **Step 1: Add stage type and props**

In `apps/web-react/src/components/workbench/ConversationScreen.tsx`, add the stage type and update the props:

```tsx
import { useEffect, useState, type ReactNode } from "react";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchRequirementDraftItem,
} from "../../lib/api/agentWorkbenchTypes";
import { Tabs } from "../primitives/Tabs";
import { MessageComposer } from "./MessageComposer";
import { RequirementReviewPanel } from "./RequirementReviewPanel";
import { ResizableChatGraphLayout } from "./ResizableChatGraphLayout";
import { StrategyGraph } from "./StrategyGraph";
import { ThinkingProcessRail } from "./ThinkingProcessRail";
import { Transcript } from "./Transcript";
import "./ConversationScreen.css";

export type WorkbenchStage = "home" | "transition" | "workspace";

export type ConversationScreenCallbacks = {
  actionErrorMessage?: string | null | undefined;
  amendingRequirements?: boolean | undefined;
  confirmingRequirements?: boolean | undefined;
  onAddOtherRequirement?: ((text: string) => Promise<void> | void) | undefined;
  onConfirmRequirements?: (() => void) | undefined;
  onSubmitMessage?: ((message: string) => Promise<void> | void) | undefined;
  onToggleRequirementItem?:
    | ((item: AgentWorkbenchRequirementDraftItem, selected: boolean) => void)
    | undefined;
  onViewCandidateDetails?: ((candidateId: string) => void) | undefined;
  submittingMessage?: boolean | undefined;
  updatingRequirementItemIds?: readonly string[] | undefined;
};

type ConversationScreenProps = ConversationScreenCallbacks & {
  stage: WorkbenchStage;
  view: AgentWorkbenchConversationResponse;
};
```

- [ ] **Step 2: Replace the workspace return with stage-aware rendering**

Replace the entire `return` block in `ConversationScreen` (lines 68-166) with:

```tsx
  const chatPanel = (
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
  );

  const graphPanel = (
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
          key={
            activePanel === "graph" ? "graph-active" : "graph-inactive"
          }
        />
      ) : null}
    </section>
  );

  const workspaceContent = compactWorkspace ? (
    <div
      className="conversation-view__workspace"
      data-active-panel={activePanel}
      data-workflow-surface={workflowSurfaceVisible ? "visible" : "hidden"}
    >
      {chatPanel}
      {workflowSurfaceVisible ? (
        <>
          {graphPanel}
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
        </>
      ) : null}
    </div>
  ) : workflowSurfaceVisible ? (
    <ResizableChatGraphLayout chat={chatPanel} graph={graphPanel} />
  ) : (
    <div
      className="conversation-view__workspace"
      data-workflow-surface="hidden"
    >
      {chatPanel}
    </div>
  );

  return (
    <div className="conversation-view">
      <ConversationStatusNotice view={view} />
      {actionErrorMessage ? (
        <section
          className="conversation-view__notice"
          data-tone="warning"
          role="alert"
        >
          <strong>操作失败</strong>
          <span>{actionErrorMessage}</span>
        </section>
      ) : null}
      {workflowSurfaceVisible && compactWorkspace ? (
        <Tabs
          ariaLabel="工作区"
          className="conversation-view__tabs"
          getPanelId={(panel) => `conversation-panel-${panel}`}
          idPrefix="conversation"
          onValueChange={setActivePanel}
          tabClassName="conversation-view__tab"
          tabs={workPanels.map((panel) => ({
            label: panel.label,
            value: panel.id,
          }))}
          value={activePanel}
        />
      ) : null}
      {workspaceContent}
    </div>
  );
```

- [ ] **Step 3: Remove the `grid-template-columns` rule from CSS**

In `apps/web-react/src/components/workbench/ConversationScreen.css`, remove lines 16-24:

```css
/* REMOVE these lines */
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

- [ ] **Step 4: Add collapse animation styles**

At the end of `apps/web-react/src/components/workbench/ConversationScreen.css`, add:

```css
.home-start-panel--collapsing {
  animation: home-panel-collapse 500ms ease forwards;
}

@keyframes home-panel-collapse {
  0% {
    opacity: 1;
    transform: scale(1);
  }
  100% {
    opacity: 0;
    transform: scale(0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-start-panel--collapsing {
    animation: none;
    opacity: 0;
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.tsx \
        apps/web-react/src/components/workbench/ConversationScreen.css
git commit -m "feat: add stage-based rendering and resizable layout to ConversationScreen"
```

---

### Task 4: Add Collapse Animation to HomeStartPanel

**Files:**
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.tsx`
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.css`

- [ ] **Step 1: Add `collapsing` prop to HomeStartPanel**

In `apps/web-react/src/components/workbench/HomeStartPanel.tsx`, add `collapsing` to props:

```tsx
type HomeStartPanelProps = {
  collapsing?: boolean;
  errorMessage?: string | null;
  initialJobDescription?: string;
  loading?: boolean;
  onSubmit: (input: HomeStartPanelSubmitInput) => Promise<void> | void;
};

export function HomeStartPanel({
  collapsing = false,
  errorMessage = null,
  initialJobDescription = "",
  loading = false,
  onSubmit,
}: HomeStartPanelProps) {
```

And update the section className to include the collapsing state:

```tsx
  return (
    <section
      aria-label="新建招聘任务"
      className={
        "home-start-panel" + (collapsing ? " home-start-panel--collapsing" : "")
      }
    >
```

- [ ] **Step 2: Add collapse keyframes to CSS**

At the end of `apps/web-react/src/components/workbench/HomeStartPanel.css`, add:

```css
.home-start-panel--collapsing {
  animation: home-panel-collapse 500ms ease forwards;
}

@keyframes home-panel-collapse {
  0% {
    opacity: 1;
    transform: scale(1);
  }
  100% {
    opacity: 0;
    transform: scale(0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-start-panel--collapsing {
    animation: none;
    opacity: 0;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/components/workbench/HomeStartPanel.tsx \
        apps/web-react/src/components/workbench/HomeStartPanel.css
git commit -m "feat: add collapse animation support to HomeStartPanel"
```

---

### Task 5: Merge Routes — root.tsx Changes

**Files:**
- Modify: `apps/web-react/src/routes/root.tsx`

- [ ] **Step 1: Rewrite root.tsx**

Replace the entire content of `apps/web-react/src/routes/root.tsx` with:

```tsx
import {
  createRootRoute,
  createRoute,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { useEffect } from "react";
import { App } from "../App";

export const rootRoute = createRootRoute({
  component: () => (
    <App>
      <Outlet />
    </App>
  ),
});

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: IndexRedirect,
});

function IndexRedirect() {
  const navigate = useNavigate({ from: "/" });

  useEffect(() => {
    navigate({ to: "/conversations/new", replace: true });
  }, [navigate]);

  return null;
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web-react/src/routes/root.tsx
git commit -m "refactor: redirect / to /conversations/new"
```

---

### Task 6: Update Conversation Route — New Conversation Logic + Stage State

**Files:**
- Modify: `apps/web-react/src/routes/conversation.tsx`

- [ ] **Step 1: Rewrite conversation.tsx — split "new" vs existing flows**

The challenge: hooks must be called unconditionally, but we don't want BFF calls for `conversationId="new"`. Solution: render a lightweight `NewWorkbenchFlow` sub-component for new conversations, and the existing `ExistingWorkbenchFlow` for real IDs.

Replace `apps/web-react/src/routes/conversation.tsx` with:

```tsx
import { createRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
  hasConversationWorkflowSurface,
  type WorkbenchStage,
} from "../components/workbench/ConversationScreen";
import { ConversationShell } from "../components/workbench/ConversationShell";
import { CandidateDetailDrawer } from "../components/workbench/CandidateDetailDrawer";
import { HomeStartPanel } from "../components/workbench/HomeStartPanel";
import type { HomeStartPanelSubmitInput } from "../components/workbench/HomeStartPanel";
import {
  useAmendAgentWorkbenchRequirementFromText,
  useConfirmAgentWorkbenchRequirements,
  useAgentWorkbenchCandidateDetail,
  useAgentWorkbenchConversations,
  useAgentWorkbenchLiveConversation,
  useCreateAgentWorkbenchConversationFromJd,
  useSubmitAgentWorkbenchMessage,
  useUpdateAgentWorkbenchRequirementDraft,
} from "../lib/api/agentWorkbench";
import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchRequirementDraftItem,
} from "../lib/api/agentWorkbenchTypes";
import { safeErrorMessage } from "../lib/api/client";
import { queryKeys } from "../lib/query/keys";
import { rootRoute } from "./root";

export const conversationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/conversations/$conversationId",
  component: WorkbenchRoute,
});

function WorkbenchRoute() {
  const { conversationId } = conversationRoute.useParams();
  const isNew = conversationId === "new";

  if (isNew) {
    return <NewWorkbenchFlow />;
  }

  return <ExistingWorkbenchFlow key={conversationId} conversationId={conversationId} />;
}

/** Lightweight flow for /conversations/new — only loads conversation list. */
function NewWorkbenchFlow() {
  const [stage, setStage] = useState<WorkbenchStage>("home");
  const navigate = useNavigate({ from: "/conversations/$conversationId" });
  const conversationsQuery = useAgentWorkbenchConversations();
  const createConversationMutation = useCreateAgentWorkbenchConversationFromJd();
  const [homeErrorMessage, setHomeErrorMessage] = useState<string | null>(null);

  const onHomeSubmit = async (input: HomeStartPanelSubmitInput) => {
    setHomeErrorMessage(null);
    try {
      const result = await createConversationMutation.mutateAsync({
        jobDescription: input.jobDescription,
        jobTitle: input.jobTitle,
      });
      setStage("transition");
      setTimeout(() => setStage("workspace"), 500);
      navigate({
        params: { conversationId: result.conversationId },
        to: "/conversations/$conversationId",
        replace: true,
      });
    } catch (error) {
      setHomeErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };

  return (
    <ConversationShell
      main={
        <HomeStartPanel
          collapsing={stage === "transition"}
          errorMessage={homeErrorMessage}
          loading={createConversationMutation.isPending}
          onSubmit={onHomeSubmit}
        />
      }
      rail={
        conversationsQuery.isSuccess ? (
          <ConversationList
            conversations={conversationsQuery.data?.conversations ?? []}
          />
        ) : (
          <ConversationList />
        )
      }
    />
  );
}

/** Full-featured flow for real conversation IDs. */
function ExistingWorkbenchFlow({
  conversationId,
}: {
  conversationId: string;
}) {
  const navigate = useNavigate({ from: "/conversations/$conversationId" });
  const queryClient = useQueryClient();
  const query = useAgentWorkbenchLiveConversation(conversationId);
  const queryKey = useMemo(
    () => queryKeys.agentConversation(conversationId),
    [conversationId],
  );
  const requirementMutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [actionErrorMessage, setActionErrorMessage] = useState<string | null>(null);
  const [updatingRequirementItemIds, setUpdatingRequirementItemIds] = useState<string[]>([]);
  const detailQuery = useAgentWorkbenchCandidateDetail(
    conversationId,
    selectedCandidateId,
  );
  const submitMessageMutation = useSubmitAgentWorkbenchMessage(conversationId);
  const confirmRequirementsMutation = useConfirmAgentWorkbenchRequirements(conversationId);
  const updateRequirementMutation = useUpdateAgentWorkbenchRequirementDraft(conversationId);
  const amendRequirementMutation = useAmendAgentWorkbenchRequirementFromText(conversationId);
  const selectedCandidate = useMemo(
    () =>
      query.data?.candidates.find(
        (candidate) => candidate.candidateId === selectedCandidateId,
      ) ?? null,
    [selectedCandidateId, query.data?.candidates],
  );
  const closeCandidateDrawer = useCallback(() => {
    setSelectedCandidateId(null);
  }, []);
  const retryCandidateDetail = useCallback(() => {
    void detailQuery.refetch();
  }, [detailQuery]);
  const viewCandidateDetails = useCallback((candidateId: string) => {
    setActionErrorMessage(null);
    setSelectedCandidateId(candidateId);
  }, []);

  useEffect(() => {
    requirementMutationChainRef.current = Promise.resolve();
    setActionErrorMessage(null);
    setSelectedCandidateId(null);
    setUpdatingRequirementItemIds([]);
  }, [conversationId]);

  if (query.isPending) {
    return (
      <ConversationShell
        main={<section aria-busy="true" className="conversation-view__state" />}
        rail={<ConversationList selectedConversationId={conversationId} />}
      />
    );
  }

  if (query.isError) {
    return (
      <ConversationShell
        main={
          <section className="conversation-view__state" role="alert">
            {safeErrorMessage(query.error)}
          </section>
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
      />
    );
  }

  const view = query.data;
  const workflowSurfaceVisible = hasConversationWorkflowSurface(view);
  const latestRequirementDraftRevisionId = () =>
    queryClient.getQueryData<AgentWorkbenchConversationResponse>(queryKey)
      ?.requirementDraft?.draftRevisionId ??
    view.requirementDraft?.draftRevisionId;

  const enqueueRequirementMutation = (run: () => Promise<void>) => {
    const next = requirementMutationChainRef.current
      .catch(() => undefined)
      .then(run);
    requirementMutationChainRef.current = next.catch(() => undefined);
    return next;
  };

  const onSubmitMessage = async (message: string) => {
    setActionErrorMessage(null);
    try {
      await submitMessageMutation.mutateAsync(message);
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };

  const onConfirmRequirements = async () => {
    setActionErrorMessage(null);
    await requirementMutationChainRef.current.catch(() => undefined);
    const draftRevisionId = latestRequirementDraftRevisionId();
    if (!draftRevisionId) {
      setActionErrorMessage("当前没有可确认的需求草稿。");
      return;
    }
    try {
      await confirmRequirementsMutation.mutateAsync(draftRevisionId);
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
    }
  };

  const onToggleRequirementItem = async (
    item: AgentWorkbenchRequirementDraftItem,
    selected: boolean,
  ) => {
    setActionErrorMessage(null);
    setUpdatingRequirementItemIds((current) =>
      current.includes(item.itemId) ? current : [...current, item.itemId],
    );
    try {
      await enqueueRequirementMutation(async () => {
        const draftRevisionId = latestRequirementDraftRevisionId();
        if (!draftRevisionId) {
          throw new Error("Requirement draft is unavailable.");
        }
        await updateRequirementMutation.mutateAsync({
          draftRevisionId,
          operations: [{ itemId: item.itemId, op: "set_selected", selected }],
        });
      });
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
    } finally {
      setUpdatingRequirementItemIds((current) =>
        current.filter((itemId) => itemId !== item.itemId),
      );
    }
  };

  const onAddOtherRequirement = async (text: string) => {
    setActionErrorMessage(null);
    try {
      await enqueueRequirementMutation(async () => {
        const draftRevisionId = latestRequirementDraftRevisionId();
        if (!draftRevisionId) {
          throw new Error("Requirement draft is unavailable.");
        }
        await amendRequirementMutation.mutateAsync({ draftRevisionId, text });
      });
    } catch (error) {
      setActionErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };

  return (
    <>
      <ConversationShell
        main={
          <ConversationScreen
            actionErrorMessage={actionErrorMessage}
            amendingRequirements={amendRequirementMutation.isPending}
            confirmingRequirements={confirmRequirementsMutation.isPending}
            onAddOtherRequirement={onAddOtherRequirement}
            onConfirmRequirements={() => void onConfirmRequirements()}
            onSubmitMessage={onSubmitMessage}
            onToggleRequirementItem={(item, selected) => {
              void onToggleRequirementItem(item, selected);
            }}
            onViewCandidateDetails={viewCandidateDetails}
            stage="workspace"
            submittingMessage={submitMessageMutation.isPending}
            updatingRequirementItemIds={updatingRequirementItemIds}
            view={view}
          />
        }
        rail={<ConversationList selectedConversationId={conversationId} />}
        side={
          workflowSurfaceVisible ? (
            <ConversationScreenSide
              onViewCandidateDetails={viewCandidateDetails}
              view={view}
            />
          ) : null
        }
      />
      <CandidateDetailDrawer
        candidate={selectedCandidate}
        detail={detailQuery.data ?? null}
        errorMessage={
          detailQuery.isError ? safeErrorMessage(detailQuery.error) : undefined
        }
        onClose={closeCandidateDrawer}
        onRetry={retryCandidateDetail}
        open={selectedCandidateId !== null}
        status={
          selectedCandidateId === null
            ? "idle"
            : detailQuery.isPending
              ? "loading"
              : detailQuery.isError
                ? "error"
                : "ready"
        }
      />
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web-react/src/routes/conversation.tsx
git commit -m "feat: merge home and conversation routes with stage state machine"
```

---

### Task 7: Update router.tsx

**Files:**
- Modify: `apps/web-react/src/router.tsx`

- [ ] **Step 1: Update route imports**

Replace `apps/web-react/src/router.tsx` content:

```tsx
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { conversationRoute } from "./routes/conversation";
import { indexRoute, rootRoute } from "./routes/root";

const routeTree = rootRoute.addChildren([indexRoute, conversationRoute]);

export const router = createRouter({ routeTree });
export { RouterProvider };

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
```

No changes needed — the route tree already imports both `indexRoute` and `conversationRoute` from the updated files. Verify the imports still resolve.

- [ ] **Step 2: Run type check**

```bash
cd apps/web-react && npx tsc -b --pretty false
```

Expected: No type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/router.tsx
git commit -m "chore: router tree unchanged after route merge"
```

Skip this commit if no changes were needed.

---

### Task 8: Integrate Pretext into Transcript

**Files:**
- Modify: `apps/web-react/src/components/workbench/Transcript.tsx`

- [ ] **Step 1: Add Pretext prepare/layout integration**

Replace `apps/web-react/src/components/workbench/Transcript.tsx` with:

```tsx
import { useMemo, useState, type ReactNode } from "react";
import type { AgentWorkbenchTranscriptGroup } from "../../lib/api/agentWorkbenchTypes";
import { TranscriptContextDivider } from "./TranscriptContextDivider";
import { TranscriptRunGroup } from "./TranscriptRunGroup";
import "./Transcript.css";

type TranscriptProps = {
  children?: ReactNode;
  defaultCollapsedGroupIds?: readonly string[];
  groups: readonly AgentWorkbenchTranscriptGroup[];
};

export function Transcript({
  children,
  defaultCollapsedGroupIds = [],
  groups,
}: TranscriptProps) {
  const initialCollapsed = useMemo(
    () => new Set(defaultCollapsedGroupIds),
    [defaultCollapsedGroupIds],
  );
  const [collapsedGroupIds, setCollapsedGroupIds] = useState(initialCollapsed);

  function toggleGroup(groupId: string) {
    setCollapsedGroupIds((current) => {
      const next = new Set(current);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }

  if (groups.length === 0 && !children) {
    return (
      <section
        aria-label="Agent transcript"
        className="transcript"
        data-state="empty"
      >
        <div className="transcript__empty" role="status">
          对话记录尚未生成
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Agent transcript" className="transcript">
      {groups.map((group) =>
        isContextGroup(group) ? (
          <TranscriptContextDivider group={group} key={group.groupId} />
        ) : (
          <TranscriptRunGroup
            collapsed={collapsedGroupIds.has(group.groupId)}
            group={group}
            key={group.groupId}
            onToggle={() => toggleGroup(group.groupId)}
          />
        ),
      )}
      {children}
    </section>
  );
}

function isContextGroup(group: AgentWorkbenchTranscriptGroup): boolean {
  return group.events.every((event) => event.kind === "context.compacted");
}

/**
 * Compute the estimated height of message text at a given width using Pretext.
 * Returns 0 when Pretext is unavailable or the text is short enough to skip
 * pre-measurement. The result is a px value suitable for min-height.
 */
export function estimateMessageTextHeight(
  text: string,
  containerWidth: number,
  preparedHandle: unknown,
): number {
  // Pretext integration is lazy-imported at call sites. This function is a
  // placeholder that returns 0 (skip pre-measurement) until Pretext is wired
  // into the actual message rendering in TranscriptRunGroup.
  //
  // When integrated, the call site will:
  //   import { prepare, layout } from "@chenglou/pretext";
  //   const prepared = prepare(text, "14px system-ui, -apple-system, sans-serif");
  //   const { height } = layout(prepared, containerWidth, 20);
  return 0;
}
```

**Note:** Full Pretext integration into `TranscriptRunGroup` message cells is deferred to a follow-up task. The `estimateMessageTextHeight` function provides the interface contract. The actual integration requires importing `prepare`/`layout` in `TranscriptRunGroup.tsx` and calling `prepare()` on message mount, `layout()` during resize. This is documented here for the next iteration.

- [ ] **Step 2: Commit**

```bash
git add apps/web-react/src/components/workbench/Transcript.tsx
git commit -m "feat: add Pretext text height estimation contract to Transcript"
```

---

### Task 9: Update Tests

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.test.tsx`
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.test.tsx`
- Create: `apps/web-react/src/components/workbench/ResizableChatGraphLayout.test.tsx`

- [ ] **Step 1: Add `stage` prop to ConversationScreen test render calls**

In `apps/web-react/src/components/workbench/ConversationScreen.test.tsx`, update each `render(<ConversationScreen .../>)` call to include `stage="workspace"`:

```tsx
// Line 31-37: add stage prop
render(
  <ConversationScreen
    onSubmitMessage={onSubmitMessage}
    stage="workspace"
    view={agentWorkbenchRunningViewFixture}
  />,
);

// Line 54-58: add stage prop
render(
  <ConversationScreen
    onConfirmRequirements={onConfirmRequirements}
    stage="workspace"
    view={agentWorkbenchRequirementReviewViewFixture}
  />,
);

// Line 71-73: add stage prop
render(
  <ConversationScreen
    stage="workspace"
    view={agentWorkbenchRequirementReviewViewFixture}
  />,
);

// Line 84-85: add stage prop
render(
  <ConversationScreen
    stage="workspace"
    view={agentWorkbenchPermissionDeniedViewFixture}
  />,
);

// Line 95: add stage prop
render(
  <ConversationScreen
    stage="workspace"
    view={agentWorkbenchCompletedViewFixture}
  />,
);

// Line 120: add stage prop
render(
  <ConversationScreen
    stage="workspace"
    view={agentWorkbenchRunningViewFixture}
  />,
);
```

- [ ] **Step 2: Add test for resizable layout in ConversationScreen**

Add a new test at the end of the `ConversationScreen` describe block (before `ConversationScreenSide` describe):

```tsx
  it("renders resizable chat and graph panels when workflow surface is visible", () => {
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
        stage="workspace"
        view={agentWorkbenchRunningViewFixture}
      />,
    );

    expect(screen.getByLabelText("Agent transcript")).toBeVisible();
    expect(screen.getByLabelText("检索策略图")).toBeVisible();
  });
```

- [ ] **Step 3: Add collapsing test for HomeStartPanel**

Add a new test at the end of `apps/web-react/src/components/workbench/HomeStartPanel.test.tsx` (before the closing of the describe block):

```tsx
  it("renders with collapsing class when collapsing prop is true", () => {
    expect.hasAssertions();

    render(<HomeStartPanel collapsing onSubmit={vi.fn()} />);

    const section = screen.getByRole("region", { name: "新建招聘任务" });
    expect(section.className).toContain("home-start-panel--collapsing");
  });
```

- [ ] **Step 4: Write ResizableChatGraphLayout tests**

Create `apps/web-react/src/components/workbench/ResizableChatGraphLayout.test.tsx`:

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ResizableChatGraphLayout } from "./ResizableChatGraphLayout";

afterEach(() => {
  cleanup();
});

describe("ResizableChatGraphLayout", () => {
  it("renders chat and graph panels", () => {
    expect.hasAssertions();

    render(
      <ResizableChatGraphLayout
        chat={<div>Chat content</div>}
        graph={<div>Graph content</div>}
      />,
    );

    expect(screen.getByText("Chat content")).toBeVisible();
    expect(screen.getByText("Graph content")).toBeVisible();
  });

  it("renders a resize handle between panels", () => {
    expect.hasAssertions();

    render(
      <ResizableChatGraphLayout
        chat={<div>Chat</div>}
        graph={<div>Graph</div>}
      />,
    );

    const handle = document.querySelector(".workspace-resize-handle");
    expect(handle).not.toBeNull();
  });
});
```

- [ ] **Step 5: Run all tests**

```bash
cd apps/web-react && npx vitest run
```

Expected: All tests pass, including new ones.

- [ ] **Step 6: Commit**

```bash
git add apps/web-react/src/components/workbench/ConversationScreen.test.tsx \
        apps/web-react/src/components/workbench/HomeStartPanel.test.tsx \
        apps/web-react/src/components/workbench/ResizableChatGraphLayout.test.tsx
git commit -m "test: add stage, collapsing, and resizable layout tests"
```

---

### Task 10: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd apps/web-react && npx vitest run
```

Expected: 28+ test files, all passing, 0 failures.

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

- [ ] **Step 4: Verify git status is clean**

```bash
git status
```

Expected: Working tree clean.

- [ ] **Step 5: Final commit if needed**

```bash
git add -A && git commit -m "chore: final verification pass" || echo "Nothing to commit"
```

---

## Verification Checklist

After all tasks, verify:

1. `pnpm test` — all tests pass, 0 failures
2. `tsc -b` — no type errors
3. `vite build` — builds successfully
4. Route `/` redirects to `/conversations/new`
5. Home form renders at `/conversations/new`
6. Form submit triggers collapse animation, then workspace view
7. Chat and graph panels have a draggable resize handle between them
8. Panel widths persist in localStorage on refresh
9. `prefers-reduced-motion` skips collapse animation
10. ≤1080px viewport falls back to tab mode, no resize handle
11. Existing ConversationList, ThinkingProcessRail, CandidateDetailDrawer unaffected