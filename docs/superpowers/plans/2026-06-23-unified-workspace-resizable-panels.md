# Phase 2: Route Merging + Collapse Animation + Pretext — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Merge `/` and `/conversations/$id` into a single route, add HomeStartPanel collapse animation, fully integrate Pretext text measurement.

**Architecture:** Single route `/conversations/$conversationId` with `NewConversationFlow` (id="new") and `ExistingConversationFlow` (real id). Collapse animation plays in NewConversationFlow before navigate. Pretext prepare/layout in TranscriptRunGroup message cells.

**Tech Stack:** React 19, `@chenglou/pretext`, `@tanstack/react-router`

**Base:** `codex/unified-workspace-resizable-panels` (Phase 1 HEAD)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/web-react/package.json` | Modify | Add `@chenglou/pretext` |
| `apps/web-react/src/routes/root.tsx` | Modify | `/` → redirect to `/conversations/new` |
| `apps/web-react/src/routes/conversation.tsx` | Modify | Split into `NewConversationFlow` + `ExistingConversationFlow` |
| `apps/web-react/src/components/workbench/HomeStartPanel.tsx` | Modify | Add `collapsing` prop |
| `apps/web-react/src/components/workbench/HomeStartPanel.css` | Modify | Add collapse keyframes |
| `apps/web-react/src/components/workbench/TranscriptRunGroup.tsx` | Modify | Integrate Pretext `prepare`/`layout` |
| `apps/web-react/src/components/workbench/HomeStartPanel.test.tsx` | Modify | Add collapsing class test |
| `apps/web-react/src/components/workbench/TranscriptRunGroup.test.tsx` | Create | Pretext prepare/layout integration test |

---

### Task 1: Install @chenglou/pretext

**Files:**
- Modify: `apps/web-react/package.json`

- [ ] **Step 1: Install**

```bash
cd apps/web-react && pnpm add @chenglou/pretext
```

- [ ] **Step 2: Commit**

```bash
git add apps/web-react/package.json apps/web-react/pnpm-lock.yaml
git commit -m "chore: add @chenglou/pretext"
```

---

### Task 2: Update root.tsx — Redirect / to /conversations/new

**Files:**
- Modify: `apps/web-react/src/routes/root.tsx`

- [ ] **Step 1: Replace WorkbenchIndexRoute with a redirect**

Replace the entire content of `apps/web-react/src/routes/root.tsx`:

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

This removes the `WorkbenchIndexRoute` function entirely, along with the `ConversationList`, `HomeStartPanel`, `useAgentWorkbenchConversations`, `useCreateAgentWorkbenchConversationFromJd`, and `safeErrorMessage` imports that were only used here.

- [ ] **Step 2: Run type check and tests**

```bash
cd apps/web-react && npx tsc -b --pretty false && npx vitest run
```

Expected: No type errors. All 102 tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/routes/root.tsx
git commit -m "refactor: redirect / to /conversations/new"
```

---

### Task 3: Update conversation.tsx — NewConversationFlow + ExistingConversationFlow

**Files:**
- Modify: `apps/web-react/src/routes/conversation.tsx`

- [ ] **Step 1: Replace the file with split flows**

Replace the entire content of `apps/web-react/src/routes/conversation.tsx`:

```tsx
import { createRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConversationList } from "../components/workbench/ConversationList";
import {
  ConversationScreen,
  ConversationScreenSide,
  hasConversationWorkflowSurface,
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

  if (conversationId === "new") {
    return <NewConversationFlow />;
  }

  return <ExistingConversationFlow key={conversationId} conversationId={conversationId} />;
}

/** Lightweight flow for /conversations/new — only loads conversation list. */
function NewConversationFlow() {
  const [collapsing, setCollapsing] = useState(false);
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
      setCollapsing(true);
      setTimeout(() => {
        navigate({
          params: { conversationId: result.conversationId },
          to: "/conversations/$conversationId",
          replace: true,
        });
      }, 500);
    } catch (error) {
      setHomeErrorMessage(safeErrorMessage(error));
      throw error;
    }
  };

  return (
    <ConversationShell
      main={
        <HomeStartPanel
          collapsing={collapsing}
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
function ExistingConversationFlow({
  conversationId,
}: {
  conversationId: string;
}) {
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

- [ ] **Step 2: Run type check and tests**

```bash
cd apps/web-react && npx tsc -b --pretty false && npx vitest run
```

Expected: No type errors. All 102 tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/routes/conversation.tsx
git commit -m "feat: split conversation route into new/existing flows with collapse support"
```

---

### Task 4: Add Collapsing Prop to HomeStartPanel

**Files:**
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.tsx`
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.css`

- [ ] **Step 1: Add `collapsing` prop to TSX**

In `HomeStartPanel.tsx`, add `collapsing` to the props type and the section className:

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

And update the section:

```tsx
<section
  aria-label="新建招聘任务"
  className={
    "home-start-panel" + (collapsing ? " home-start-panel--collapsing" : "")
  }
>
```

- [ ] **Step 2: Add collapse keyframes to CSS**

At the end of `HomeStartPanel.css`:

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

- [ ] **Step 3: Run tests**

```bash
cd apps/web-react && npx vitest run
```

Expected: All 102 tests pass.

- [ ] **Step 4: Commit**

```bash
git add apps/web-react/src/components/workbench/HomeStartPanel.tsx \
        apps/web-react/src/components/workbench/HomeStartPanel.css
git commit -m "feat: add collapse animation to HomeStartPanel"
```

---

### Task 5: Add Collapsing Test for HomeStartPanel

**Files:**
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.test.tsx`

- [ ] **Step 1: Add test**

```tsx
it("renders with collapsing class when collapsing prop is true", () => {
    expect.hasAssertions();

    render(<HomeStartPanel collapsing onSubmit={vi.fn()} />);

    const section = screen.getByRole("region", { name: "新建招聘任务" });
    expect(section.className).toContain("home-start-panel--collapsing");
  });
```

- [ ] **Step 2: Run tests**

```bash
cd apps/web-react && npx vitest run
```

Expected: 103 tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/web-react/src/components/workbench/HomeStartPanel.test.tsx
git commit -m "test: add collapsing class test for HomeStartPanel"
```

---

### Task 6: Integrate Pretext into Transcript Event Rendering

**Files:**
- Modify: `apps/web-react/src/components/workbench/TranscriptRunGroup.tsx`

- [ ] **Step 1: Add Pretext imports and pre-measurement hook**

In `TranscriptRunGroup.tsx`, add:

```tsx
import { prepare, layout } from "@chenglou/pretext";

const FONT_STRING = "14px system-ui, -apple-system, sans-serif";
const MIN_PRETEXT_CHARS = 200;
```

- [ ] **Step 2: Update TranscriptEvent to use Pretext for long text**

In the `TranscriptEvent` function, before the `<p>` tag that renders `event.summary`, add pre-measurement:

```tsx
function TranscriptEvent({ event }: { event: AgentWorkbenchTranscriptEvent }) {
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const summaryText = event.summary ?? event.payload.summary ?? null;
  const shouldMeasure = summaryText !== null && summaryText.length > MIN_PRETEXT_CHARS;

  const prepared = useMemo(
    () => (shouldMeasure ? prepare(summaryText, FONT_STRING) : null),
    [summaryText, shouldMeasure],
  );

  const measuredHeight = useMemo(() => {
    if (prepared === null || containerWidth === null) return undefined;
    const { height } = layout(prepared, containerWidth, 20);
    return height;
  }, [prepared, containerWidth]);

  // ... rest of existing rendering
```

And add a `ResizeObserver` effect to track container width:

```tsx
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!shouldMeasure) return;
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [shouldMeasure]);
```

Then update the `<p>` tag rendering:

```tsx
        {(event.summary ?? event.payload.summary) ? (
          <p style={measuredHeight !== undefined ? { minHeight: measuredHeight } : undefined}>
            {event.summary ?? event.payload.summary}
          </p>
        ) : null}
```

And add `ref={ref}` to the transcript-event body div:

```tsx
      <div className="transcript-event__body" ref={ref}>
```

- [ ] **Step 3: Add the necessary imports**

```tsx
import { useMemo, useRef, useEffect, useState } from "react";
// ... existing imports
```

- [ ] **Step 4: Run type check and tests**

```bash
cd apps/web-react && npx tsc -b --pretty false && npx vitest run
```

Expected: No type errors. All 103 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web-react/src/components/workbench/TranscriptRunGroup.tsx
git commit -m "feat: integrate Pretext text measurement in Transcript events"
```

---

### Task 7: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd apps/web-react && npx vitest run
```

Expected: 103+ tests, all passing.

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

- [ ] **Step 4: Verify git status clean**

```bash
git status
```

Expected: Working tree clean.

- [ ] **Step 5: Final commit**

```bash
git add -A && git commit -m "chore: final Phase 2 verification pass" || echo "Nothing to commit"
```

---

## Verification Checklist

1. `pnpm test` — all tests pass
2. `tsc -b` — no type errors
3. `vite build` — builds successfully
4. `/` redirects to `/conversations/new`
5. Home form visible at `/conversations/new`
6. Submit form → 500ms collapse animation → workspace loads
7. `prefers-reduced-motion` skips animation
8. Panel resize → transcript text height follows (no DOM reflow for long messages)
9. Short messages (<200 chars) use normal CSS text flow
10. Existing conversation list, side panel, candidate drawer unaffected