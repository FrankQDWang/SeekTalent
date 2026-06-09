# Codex Goal: Build SeekTalent Conversational Agent Runtime

## Objective

Build the complete local conversational agent backend and UI-ready data contract described in this directory. The Svelte transcript UI and memory-management UI are deferred until designer-provided screens are available. The finished goals must let a backend caller or future UI create, rename, archive, unarchive, and reopen conversations; enter a JD; receive structured requirement-review data; edit and confirm selected requirement items through real APIs; start and control the workflow runtime through durable tools; read real progress through transcript-ready messages; ask detail questions during execution; pause/cancel/resume at safe boundaries; add next-round requirements; survive long-running transcript compaction; recall product-owned advisory memory; and receive a final summary grounded in runtime results.

Goal 2 now includes the Codex-like transcript experience, conversation compaction, and product-owned advisory memory phase. The files in `goal-2-agent-memory-extension/` are the source contract for the integrated advisory memory phase. They are no longer a separate default later goal, but that phase must still start only after the core conversation-agent service, store, `AgentRuntime`, transcript routes, persisted transcript messages, and persisted activity items are real and verified.

## How To Use This File

This file is the goal-pack entrypoint and shared completion contract. Do not paste it verbatim as one Codex `/goal` prompt.

Run one selected goal at a time using the Goal Invocation snippets in `04-operating-policies-and-runtime-contracts.md`:

1. run Goal 1 only;
2. after Goal 1 is complete and verified, run the combined Goal 2 transcript, compaction, and advisory-memory goal;
3. split the advisory memory phase into a separate later execution only if an explicit product gate says to defer it.

Each selected goal must read this file, the shared contracts, and its own `SPEC.md` and `PLAN.md`, then stop at that goal's completion boundary. A Codex worker must not combine Goal 1 with Goal 2 in one execution. For Goal 2, the memory documents are part of the selected goal's required reading and implementation sequence.

## Required Reading

Read these files before editing code:

```text
conversational-agent-runtime-goal-pack/00-codex-goal.md
conversational-agent-runtime-goal-pack/MANIFEST.md
conversational-agent-runtime-goal-pack/00-index.md
conversational-agent-runtime-goal-pack/01-shared-product-and-architecture.md
conversational-agent-runtime-goal-pack/02-agent-tool-and-requirement-contracts.md
conversational-agent-runtime-goal-pack/03-runtime-control-state-and-events.md
conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md
conversational-agent-runtime-goal-pack/05-sqlite-event-log-and-projection-contract.md
```

Then read the selected goal documents in full:

```text
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/SPEC.md
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/PLAN.md
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/SPEC.md
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/PLAN.md
```

For combined Goal 2, also read the integrated advisory memory phase documents in full:

```text
conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/SPEC.md
conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/PLAN.md
```

Goal 1 must complete before Goal 2 starts.

The Goal 2 memory phase must start only after the core Goal 2 transcript-agent APIs, store, `AgentRuntime`, routes, persisted transcript messages, and persisted activity items exist and have focused verification evidence.

## Goals

- Build a durable runtime control plane between the conversational agent and the current `WorkflowRuntime`.
- Expose stable, agent-callable tools for requirement extraction, free-form requirement amendment normalization, requirement confirmation, workflow start, progress reads, commands, details, and final summary preparation.
- Persist requirement drafts, user selection state, commands, events, snapshots, checkpoints, and final result metadata.
- Keep the local SQLite durable event log, cursor rules, projection idempotency, and gap recovery aligned with `05-sqlite-event-log-and-projection-contract.md`.
- Persist conversation title, archive state, reopen metadata, and list visibility through backend APIs.
- Keep runtime progress in the transcript grounded in real runtime-control events.
- Provide a Codex-like backend transcript contract: durable message stream, tool-call states, event-grounded progress narration, lifecycle activity items, streamable activity deltas, reloadable history, compaction summaries, and clear working-process updates without building UI.
- Enforce explicit agent token, cost, timeout, rate-limit, DTO versioning, free-text safety, and compaction quality contracts.
- Add SeekTalent-owned advisory memory that can influence suggestions and wording, but cannot become requirement, runtime, candidate, or source-selection truth.
- Prepare complete UI-ready DTOs and transcript view models for the future designer-backed UI.
- Preserve source registry/catalog direction and avoid hard-coding CTS/Liepin as the complete source universe.
- Make production artifact/trace output safe for local commercial use.

## Non-Goals

- Do not build a SaaS control plane.
- Do not build a general-purpose workflow engine.
- Do not implement arbitrary Python stack-frame restoration.
- Do not let the agent directly import runtime internals.
- Do not let frontend state become canonical product state.
- Do not use Codex memory as canonical product state.
- Do not use SeekTalent memory as canonical requirement, runtime, or candidate state.
- Do not treat the local Codex source checkout as product source, vendored code, or a runtime dependency.
- Do not introduce Codex CLI, Codex App Server, Codex MCP server, Codex SDK, or an operator-installed `codex` binary as a product runtime dependency.
- Do not ship fake tool responses, unused storage, empty adapters, or hidden fallback chains.
- Do not ship temporary transcript UI or memory UI before design-backed UI work starts.
- Do not complete Goal 2 before Goal 1's runtime-control APIs are real and verified.

## Expected Deliverables

Goal 1 delivers:

- runtime-control backend package;
- durable SQLite schema and migrations;
- requirement draft revision model;
- free-form requirement amendment model;
- runtime command model;
- runtime event and snapshot store;
- checkpoint persistence;
- artifact/trace sink policy;
- Workbench bridge;
- safe-boundary pause/cancel/resume semantics;
- tests and verification gates.

Goal 2 delivers:

- conversational agent backend service;
- OpenAI Agents SDK runtime integration packaged with SeekTalent;
- conversation transcript persistence;
- conversation metadata rename/archive/unarchive/reopen APIs;
- transcript API routes;
- agent tool orchestration over Goal 1 APIs;
- editable requirement confirmation API and view-model contract;
- free-form extra requirement input that normalizes through runtime-control tools;
- free-form text safety screening before runtime-control normalization;
- transcript progress narration from real events;
- command input handling for pause/cancel/resume/add requirement/detail questions;
- final summary flow;
- transcript history compaction that preserves reload, event cursors, requirement review history, command history, conversation metadata, final-summary context, and compaction quality evidence;
- dedicated memory SQLite store;
- memory extraction, privacy filtering, recall-time filtering, consolidation, recall, and deletion;
- OpenAI Agents SDK advisory memory injection;
- memory management APIs;
- tests proving memory cannot bypass requirement confirmation or store forbidden data;
- backend tests, agent evaluations, memory evaluations, API/view-model tests, and integration verification.

## Completion

The task is complete only after Goal 1 and the combined Goal 2 pass their own acceptance criteria, the integrated memory phase acceptance criteria, and the cross-goal acceptance criteria in `04-operating-policies-and-runtime-contracts.md`.
