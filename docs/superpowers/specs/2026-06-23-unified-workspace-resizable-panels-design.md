# Phase 1: Resizable Chat↔Graph Panels — Design Spec

> **Date:** 2026-06-23  
> **Branch:** `codex/unified-workspace-resizable-panels`  
> **Base:** `codex/conversation-agent-controlled-orchestration` (476288f2)  
> **Status:** phased — Phase 1 only

---

## 1. Problem

The ConversationScreen workspace chat and graph panels are laid out with a fixed CSS Grid (`grid-template-columns: 386px minmax(0, 1fr)`). Users cannot resize the panels. When the chat panel has long messages or the graph needs more width, there is no way to adjust the split.

## 2. Goal

Replace the fixed CSS Grid with `react-resizable-panels` (v4) `Group`/`Panel`/`Separator` components, making the chat↔graph split draggable. Panel sizes persist via `autoSaveId` in localStorage.

## 3. Non-goals (Phase 1)

- No route merging (`/` and `/conversations/$id` stay separate)
- No HomeStartPanel collapse animation
- No Pretext text measurement
- No changes to the rail, side panel, or responsive tab layout
- No changes to BFF or backend

## 4. Design

### 4.1 Component Change

**Before** (`ConversationScreen.tsx`):
```tsx
<div className="conversation-view__workspace"
     data-workflow-surface="visible">
  <section className="conversation-view__panel--chat">...</section>
  {workflowSurfaceVisible ? (
    <section className="conversation-view__panel--graph">...</section>
  ) : null}
</div>
```

CSS:
```css
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: grid;
  grid-template-columns: 386px minmax(0, 1fr);
}
```

**After**:
```tsx
import { Group, Panel, Separator } from "react-resizable-panels";

// Inside ConversationScreen, when !compactWorkspace && workflowSurfaceVisible:
<Group autoSaveId="chat-graph-layout" className="workspace-group" direction="horizontal">
  <Panel className="workspace-panel workspace-panel--chat"
         defaultSize={386} minSize={280} maxSize="50%">
    {chatPanelContent}
  </Panel>
  <Separator className="workspace-separator" />
  <Panel className="workspace-panel workspace-panel--graph"
         defaultSize={null} minSize={400}>
    {graphPanelContent}
  </Panel>
</Group>
```

**Key API notes (react-resizable-panels v4.11.2):**
- Container: `Group` (not `PanelGroup`)
- Handle: `Separator` (not `PanelResizeHandle`)
- Size props: numeric values = pixels, strings ending with `%` = percentage
- Default `minSize`/`maxSize` = 0%/100%
- Hover/active styling uses `[data-separator]` attribute selector

### 4.2 CSS Changes

Remove `grid-template-columns` rules. Add resize handle styles:

```css
/* Remove */
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: grid;
  gap: 0;
  grid-template-columns: 386px minmax(0, 1fr);
}

/* Replace with */
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: flex;
  flex: 1;
  min-height: 0;
}
```

```css
/* Add */
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

.workspace-separator {
  background: transparent;
  flex-shrink: 0;
  outline: none;
  transition: background 150ms;
  width: 4px;
}

.workspace-separator:hover,
.workspace-separator[data-separator] {
  background: var(--st-action);
  cursor: col-resize;
}

.workspace-separator:focus-visible {
  background: var(--st-action);
  outline: 2px solid var(--st-action);
  outline-offset: 2px;
}

.workspace-panel--graph .strategy-graph {
  height: 100%;
  min-height: 100%;
}
```

### 4.3 Responsive (≤1080px)

No change. `useCompactWorkspace()` still returns true on narrow viewports, and the existing tab-driven layout works as before. `Group`/`Panel`/`Separator` are only rendered when `!compactWorkspace`.

### 4.4 Compact Workspace Path

When `compactWorkspace` is true, the existing block layout with tab switching is preserved unchanged. The `Group` is never mounted.

### 4.5 localStorage Persistence

`autoSaveId="chat-graph-layout"` on `Group` persists the layout automatically. Refreshing the page restores the user's panel widths.

## 5. Files Changed

| File | Change |
|------|--------|
| `package.json` | Add `react-resizable-panels` dependency |
| `ConversationScreen.tsx` | Import `Group`/`Panel`/`Separator`, replace CSS Grid in `!compactWorkspace && workflowSurfaceVisible` path |
| `ConversationScreen.css` | Remove `grid-template-columns: 386px ...`, add `.workspace-group`/`.workspace-panel`/`.workspace-separator` styles |
| `ConversationScreen.test.tsx` | Add test that resize handle renders (no structural change to existing tests) |
| `ConversationScreen.stories.tsx` | Add story `ResizableLayout` showing the draggable split |
| `tests/storybook-visual.spec.ts` | Add screenshot entry for `workbench-resizable-layout` |

## 6. Acceptance Criteria

1. Chat and graph panels have a 4px vertical separator between them
2. Hovering over the separator shows `cursor: col-resize` and a blue highlight
3. Dragging the separator resizes both panels proportionally
4. Chat panel cannot be smaller than 280px or larger than 50%
5. Graph panel cannot be smaller than 400px
6. Panel positions persist in localStorage across page reloads
7. ≤1080px viewport: no separator, existing tab layout works unchanged
8. Keyboard accessible: tab to separator, arrow keys to resize

## 7. Test Plan

### Unit Tests

- `ResizableChatGraphLayout.test.tsx`: verify Group, Panel, Separator render
- Extend `ConversationScreen.test.tsx`: verify resize handle rendered when `!compactWorkspace`

### Playwright Visual

- `workbench-live.spec.ts`: existing test already navigates to workspace and screenshots; verify result includes separator
- `storybook-visual.spec.ts`: add `workbench-resizable-layout` entry

### Storybook

- `ConversationScreen/ResizableLayout`: story showing the draggable chat↔graph split with fixture data

## 8. Dependencies

- `react-resizable-panels@^4.11.2`