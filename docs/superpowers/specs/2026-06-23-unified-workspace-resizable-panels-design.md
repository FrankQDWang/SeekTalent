# Unified Workspace — Design Spec

> **Date:** 2026-06-23  
> **Branch:** `codex/unified-workspace-resizable-panels`  
> **Status:** Phase 2 — Route Merging + Collapse Animation + Pretext

---

## Phase 1 (Done)

Resizable chat↔graph panels with `react-resizable-panels` v4. See git log.

## Phase 2

### 1. Problem

After Phase 1, the workspace has resizable panels but the user still experiences a hard route jump from `/` to `/conversations/$id` with no transition. The form disappears and the workspace appears with no animation. Additionally, transcript text does not use pre-measurement, causing layout reflow during panel resize.

### 2. Goals

1. **Route merging:** `/` redirects to `/conversations/new`. Single route with `NewConversationFlow` / `ExistingConversationFlow` split.
2. **Form collapse animation:** Submitting the form triggers a 500ms CSS collapse animation on HomeStartPanel, then navigates to the real conversation.
3. **Pretext text measurement:** Full integration of `@chenglou/pretext` in Transcript message rendering for DOM-reflow-free text height during panel resize.

### 3. Route Architecture

```
/                        → redirect to /conversations/new
/conversations/new       → NewConversationFlow (HomeStartPanel + collapse animation)
/conversations/$id       → ExistingConversationFlow (ConversationScreen + BFF hooks)
```

The split at the route level avoids hook-ordering issues (BFF hooks never render for `"new"`). The collapse animation plays in `NewConversationFlow`, then after 500ms `navigate` to the real ID unmounts it and mounts `ExistingConversationFlow`.

### 4. Collapse Animation

HomeStartPanel accepts a `collapsing` prop. When true, CSS applies:

```css
.home-start-panel--collapsing {
  animation: home-panel-collapse 500ms ease forwards;
}

@keyframes home-panel-collapse {
  0% { opacity: 1; transform: scale(1); }
  100% { opacity: 0; transform: scale(0.95); }
}

@media (prefers-reduced-motion: reduce) {
  .home-start-panel--collapsing { animation: none; opacity: 0; }
}
```

### 5. Pretext Integration

`@chenglou/pretext` is used for pre-measuring message text heights in TranscriptRunGroup:

```tsx
import { prepare, layout } from "@chenglou/pretext";

// On message mount:
const prepared = useMemo(() => prepare(text, fontString), [text]);
// On panel resize:
const { height } = layout(prepared, containerWidth, lineHeight);
```

The measured height is applied as `min-height` to the message container, preventing DOM layout reflow during resize.

Only messages > 200 characters get Pretext pre-measurement. Shorter messages use CSS naturally.

### 6. Files Changed

| File | Change |
|------|--------|
| `routes/root.tsx` | `/` → redirect to `/conversations/new` |
| `routes/conversation.tsx` | Split into `NewConversationFlow` + `ExistingConversationFlow`. New flow has collapse animation + create mutation. |
| `components/workbench/HomeStartPanel.tsx` | Add `collapsing` prop |
| `components/workbench/HomeStartPanel.css` | Add collapse keyframes + `--collapsing` class |
| `components/workbench/TranscriptRunGroup.tsx` | Integrate Pretext `prepare`/`layout` |
| `components/workbench/HomeStartPanel.test.tsx` | Add collapsing class test |
| `package.json` | Add `@chenglou/pretext` |

### 7. Acceptance Criteria

1. `/` redirects to `/conversations/new`
2. At `/conversations/new`, home form is shown full-screen
3. Submitting the form: 500ms collapse animation plays, then workspace loads
4. Panel resize during workspace: transcript text height follows without visible DOM reflow
5. Pretext only activates for messages > 200 chars
6. `prefers-reduced-motion` skips collapse animation
7. Existing 102 tests pass, new Pretext tests pass