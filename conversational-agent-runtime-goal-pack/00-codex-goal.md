# Codex Goal: Build SeekTalent Conversational Agent Runtime

## Objective

Build the complete local conversational agent backend and UI-ready data contract described in this directory. The Svelte transcript UI and memory-management UI are deferred until designer-provided screens are available. The finished goals must let a backend caller or future UI enter a JD, receive structured requirement-review data, edit and confirm selected requirement items through real APIs, start and control the workflow runtime through durable tools, read real progress through transcript-ready messages, ask detail questions during execution, pause/cancel/resume at safe boundaries, add next-round requirements, and receive a final summary grounded in runtime results.

The agent memory extension in `goal-2-agent-memory-extension/` is a post-Goal-2 package. It is not required for the primary two-goal completion unless explicitly invoked.

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
```

Then read the selected goal documents in full:

```text
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/SPEC.md
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/PLAN.md
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/SPEC.md
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/PLAN.md
```

Goal 1 must complete before Goal 2 starts.

The memory extension must start only after Goal 2 completes and is explicitly invoked.

## Goals

- Build a durable runtime control plane between the conversational agent and the current `WorkflowRuntime`.
- Expose stable, agent-callable tools for requirement extraction, free-form requirement amendment normalization, requirement confirmation, workflow start, progress reads, commands, details, and final summary preparation.
- Persist requirement drafts, user selection state, commands, events, snapshots, checkpoints, and final result metadata.
- Keep runtime progress in the transcript grounded in real runtime-control events.
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
- transcript API routes;
- agent tool orchestration over Goal 1 APIs;
- editable requirement confirmation API and view-model contract;
- free-form extra requirement input that normalizes through runtime-control tools;
- transcript progress narration from real events;
- command input handling for pause/cancel/resume/add requirement/detail questions;
- final summary flow;
- backend tests, agent evaluations, API/view-model tests, and integration verification.

The post-Goal-2 memory extension delivers, when explicitly invoked:

- product-owned advisory memory package;
- dedicated memory SQLite store;
- memory extraction, privacy filtering, consolidation, recall, and deletion;
- OpenAI Agents SDK advisory memory injection;
- memory management APIs;
- tests proving memory cannot bypass requirement confirmation or store forbidden data.

## Completion

The task is complete only after both goals pass their own acceptance criteria and the cross-goal acceptance criteria in `04-operating-policies-and-runtime-contracts.md`.
