# Frontend UX

## Screen Placement

The existing new-session page currently shows a manual `CreateSessionForm`. The target experience replaces that primary form with an intake conversation panel.

Target file:

```text
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
```

The existing manual fields may remain available as an edit surface inside the confirmation card, but they should no longer be the first thing the user must fill out.

## Design-Timing Constraint

A larger UI redesign is expected after designer input. The implementation must not block on that redesign.

Build a functional transcript shell now:

- stable conversation API integration;
- upward-scrolling message transcript;
- input box at the bottom;
- confirmation card in the transcript;
- progress/result Q&A in the transcript;
- minimal styling consistent with the current Workbench.

The later redesign should be able to replace layout, visual styling, and micro-interactions without changing the backend state machine.

## Primary UI

Create:

```text
apps/web-svelte/src/lib/components/IntakeConversation.svelte
```

Expected regions:

- upward-scrolling transcript;
- message input;
- source status indicator;
- confirmation card;
- edit controls for `jobTitle`, `jdText`, `notes`, and catalog-backed source selection;
- confirm button;
- progress/result answer messages;
- reset conversation button;
- provider/config error state.

## Interaction Flow

```text
Open /sessions
  -> create or resume intake conversation
  -> user sends message
  -> assistant asks clarification or shows confirmation
  -> user edits if needed
  -> user confirms
  -> Workbench session is created
  -> route navigates to /sessions/{sessionId}
  -> user can ask progress/result questions in transcript
```

## Visual Tone

This is an operational local Workbench, not a marketing page.

Use:

- dense but readable layout;
- clear transcript hierarchy;
- restrained color;
- stable controls;
- no hero section;
- no decorative background;
- no nested cards.

## Required States

The UI must handle:

- empty conversation;
- sending;
- assistant response pending;
- clarifying;
- draft ready;
- edit mode;
- confirming;
- session created;
- workflow progress answer;
- workflow result answer;
- provider not configured;
- Codex harness unavailable;
- stale confirmation;
- generic failed state with stable reason code.

## Copy Requirements

Use Chinese user-facing copy by default.

Examples:

```text
请描述这次招聘需求
正在整理需求
我理解的招聘目标
确认并创建会话
返回修改
模型服务未配置
当前确认内容已过期，请重新确认
正在读取当前工作流进度
目前流程进展如下
```

Do not include visible explanatory text about internal architecture, Codex memory internals, or keyboard shortcuts.

## Frontend API Files

Expected additions:

```text
apps/web-svelte/src/lib/api/intake.ts
apps/web-svelte/src/lib/intake/types.ts
apps/web-svelte/src/lib/intake/state.ts
apps/web-svelte/src/lib/intake/state.test.ts
apps/web-svelte/src/lib/components/IntakeConversation.test.ts
```

Expected modification:

```text
apps/web-svelte/src/lib/query/keys.ts
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
```

## Accessibility

Required:

- message input has an accessible label;
- assistant status is announced via `aria-live`;
- confirmation controls are keyboard reachable;
- errors use `role="alert"`;
- disabled buttons expose clear visible labels;
- long JD text remains scrollable without layout overlap.

## Navigation

After successful confirmation, navigate to:

```text
/sessions/{sessionId}
```

The frontend must also update Svelte Query cache for:

- intake conversation;
- Workbench session;
- Workbench session list.

## Redesign Compatibility

The initial component should avoid baking business logic into visual structure.

Keep these separate:

- API calls in `apps/web-svelte/src/lib/api/intake.ts`;
- transcript state helpers in `apps/web-svelte/src/lib/intake/state.ts`;
- rendering in `IntakeConversation.svelte`.

Do not couple the backend contract to the first visual design. The designer-led redesign should be a component replacement, not a backend rewrite.
