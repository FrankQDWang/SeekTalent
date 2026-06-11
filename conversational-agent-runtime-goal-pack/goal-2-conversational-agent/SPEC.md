# Goal 2 Conversational Agent Spec

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `goal-2-conversational-agent/00-goal.md`
- `goal-2-conversational-agent/01-current-system-facts.md`
- `goal-2-conversational-agent/02-transcript-product-contract.md`
- `goal-2-conversational-agent/03-agent-tool-use-contract.md`
- `goal-2-conversational-agent/04-frontend-backend-boundaries.md`
- `goal-2-conversational-agent/08-conversation-storage-contract.md`

---

## Source: `goal-2-conversational-agent/00-goal.md`

## Goal 2: Conversational Agent Transcript

### Objective

Build the conversational-agent backend, API routes, transcript persistence, Codex-like transcript contract, context compaction, integrated advisory memory phase, and UI-ready view models over the completed runtime control plane.

The agent accepts a JD or hiring intent through backend conversation APIs, calls runtime-control tools for requirement extraction, returns editable requirement confirmation data, sends confirmed requirements back to runtime control, starts and observes the workflow, persists transcript-ready progress messages, accepts user commands, answers grounded detail questions, compacts long-running model context without losing canonical transcript state, injects SeekTalent-owned advisory memory, and produces a final summary.

The Svelte transcript UI is not part of this goal. It will be implemented later from designer-provided screens and must consume the DTOs and view models produced here.

### Product Result

After this goal, a user can:

1. create and reload a local agent conversation through real backend APIs;
2. rename a conversation through persisted backend thread metadata;
3. archive or unarchive a conversation without deleting transcript, runtime links, activity items, or memory records;
4. reopen a conversation and receive a `conversation_reopen_state` header that describes current status, allowed actions, cursors, pending states, and title;
5. submit a JD or rough hiring request;
6. receive structured requirement sections in Chinese;
7. receive selected-by-default item state for every extracted item;
8. edit, delete, move, select/unselect, and enable/disable supported requirement items through persisted operations;
9. confirm the requirement revision;
10. read transcript-ready workflow progress backed by runtime-control events;
11. observe Codex-like working-process activity items that update from queued/started through completed/failed without invented progress;
12. submit natural-language commands to pause, cancel, resume, add next-round requirements, or ask details;
13. receive command state reflected from persisted runtime-control records;
14. reload a long-running conversation after compaction without losing requirement review history, command history, runtime event cursors, activity item state, or final summary context;
15. receive safe advisory memory suggestions that still require user confirmation before changing requirements;
16. receive a final summary grounded in final runtime result and the user's instruction.

### Scope

In scope:

- conversational agent backend service;
- OpenAI Agents SDK runtime isolated behind `AgentRuntime`;
- transcript session/message persistence;
- persisted conversation title, archive state, and reopen metadata;
- dedicated conversation-agent SQLite store;
- context compaction for model input history while preserving canonical transcript and runtime cursors;
- agent API routes;
- runtime-control tool adapter;
- intent parsing for requirement edits, confirmation, workflow start, runtime commands, detail questions, and final summary requests;
- UI-ready transcript DTOs and view models;
- editable requirement confirmation API and view-model contract;
- event-driven transcript progress projection;
- Codex-like backend working-process transcript contract, including durable tool-call states, activity item lifecycle states, progress narration, and streamable activity deltas;
- integrated SeekTalent-owned advisory memory phase from `goal-2-agent-memory-extension/`;
- final summary flow;
- backend tests, agent evaluations, memory evaluations, API/view-model tests, and Workbench integration verification.

Out of scope:

- building the runtime-control APIs that Goal 1 owns;
- product runtime dependency on Codex CLI, Codex App Server, Codex MCP server, or Codex SDK;
- direct runtime imports in the agent package;
- cloud hosting;
- cross-user collaboration;
- voice interaction;
- arbitrary source provider configuration UI beyond the existing source catalog surface;
- advisory memory that changes requirements, runtime state, source selection, candidate facts, or candidate scores without user confirmation;
- using Codex memory, Codex CLI, Codex App Server, Codex MCP server, Codex SDK, or the local Codex source checkout as product runtime behavior;
- replacing the current Workbench product;
- implementing Svelte transcript screens before designer-provided UI is available;
- implementing temporary transcript UI controls for this goal.

### Codex Reference Capability Mapping

Goal 2 should reuse Codex as an implementation reference for general agent-product behavior, not as product runtime code.

Adopt in this goal:

| Codex capability pattern | SeekTalent adaptation |
| --- | --- |
| Thread/turn/item lifecycle | Conversation, agent turn, transcript message, tool call, and activity item lifecycle. |
| Item started/updated/completed stream | Persisted activity items plus stream/poll deltas over persisted state. |
| Tool-call lifecycle visibility | `agent_tool_calls` persisted states linked to transcript messages and activity items. |
| Plan/todo/progress visibility | Runtime-derived activity items and snapshot status, not model-invented checklists. |
| Context compaction as visible work | `context_compaction` activity item plus persisted `agent_context_summaries`. |
| Memory as advisory context | SeekTalent-owned memory phase with recall/review/delete APIs and requirement-confirmation boundary. |
| Memory/evidence citation | Store supplied memory fact ids and safe references for agent turns and suggestions. |
| Interrupt/steer/resume behavior | Runtime-safe pause/cancel/resume/next-round amendment/detail commands while a workflow is active. |
| Approval gates | Requirement confirmation, review-required amendment resolution, and memory-review confirmation. |
| Trace/token/status observability | Safe telemetry and progress metadata that exclude secrets, raw provider payloads, and Codex auth state. |
| Archive/rename/reopen metadata | Persisted conversation title, archive state, and `conversation_reopen_state` that restore the thread header and allowed actions after reload. |

Intentionally defer or reject in this goal:

| Codex capability pattern | Decision |
| --- | --- |
| Codex CLI/App Server/MCP/SDK runtime | Rejected as product dependency. Use OpenAI Agents SDK only behind `AgentRuntime`. |
| Shell/file-edit/code patch tools | Rejected for SeekTalent product workflow. Runtime-control tools and source providers are the tool surface. |
| Full Svelte transcript UI | Deferred until designer-provided UI screens exist. Backend DTOs must be complete now. |
| Browser/computer-control UI automation | Not part of the conversation transcript contract unless an approved source provider already owns it. |
| Multi-agent collaboration/subagent UI | Deferred unless a later product gate introduces parallel sourcing or review agents. |
| Codex memory files | Rejected as product state. SeekTalent memory uses its own store, privacy policy, and confirmation boundary. |

Every substantial implementation phase must record the inspected Codex source paths, adopted ideas, rejected ideas, local adaptations, and tests in `goal-2-conversational-agent/progress.md`.

### Dependency On Goal 1

Goal 2 cannot start product implementation until Goal 1 provides real runtime-control APIs, storage, events, snapshots, commands, checkpoint semantics, and verification evidence.

Goal 2 must also verify `../05-sqlite-event-log-and-projection-contract.md`: runtime-control event rows, event write transactions, event cursor reads, projection idempotency, cursor ownership, and gap recovery are the basis for transcript and activity projection.

If Goal 2 needs a runtime tool not provided by Goal 1, stop and escalate. Do not create a fake local copy of runtime-control behavior in the agent layer.

### Completion Statement

Goal 2 is complete only when the transcript agent drives the completed runtime-control service end to end and all UI-ready progress, command state, requirement state, detail answers, and final summaries are grounded in persisted runtime-control data.

---

## Source: `goal-2-conversational-agent/01-current-system-facts.md`

## Goal 2 Current System Facts

### UI Facts

- The repository contains a Svelte Workbench app under `apps/web-svelte`.
- Existing session pages live under `apps/web-svelte/src/routes/(app)/sessions/`.
- Built Workbench static assets live under `src/seektalent_ui/static/workbench/`.
- Generated Workbench static assets were dirty at planning time. Goal 2 workers must inspect dirty state and must not run frontend builds or overwrite generated assets unless API typing or an explicitly approved frontend build requires it.

### Backend UI Facts

- `src/seektalent_ui/workbench_routes.py` exposes Workbench requirement and runtime routes.
- `src/seektalent_ui/event_routes.py` exposes event streaming surfaces.
- `src/seektalent_ui/models.py` defines Workbench API DTOs.
- `src/seektalent_ui/server.py` wires routes into the local server.
- `src/seektalent_ui/runtime_bridge.py` currently bridges Workbench routes to runtime behavior.

### Requirement Facts

- The backend field mapping for the transcript confirmation is fixed by shared contract:
  - `must_have_capabilities`;
  - `preferred_capabilities`;
  - `hard_constraints`;
  - `exclusion_signals`;
  - `initial_query_term_pool[].term`.
- Every item must default to selected.
- Selection, edit, delete, move, and keyword enablement state must be persisted through runtime-control draft revisions, not only in Svelte component state.

### Runtime-Control Dependency Facts

Goal 2 reads Goal 1 contracts:

```text
src/seektalent_runtime_control/
conversational-agent-runtime-goal-pack/02-agent-tool-and-requirement-contracts.md
conversational-agent-runtime-goal-pack/02-agent-tool-and-requirement-contracts.md
conversational-agent-runtime-goal-pack/03-runtime-control-state-and-events.md
```

The agent package must call runtime-control public APIs or local HTTP routes built on those APIs. It must not import `WorkflowRuntime`, `RunState`, provider modules, or runtime internals.

### Goal 2 Start-Readiness Corrections

The runtime-control implementation is intentionally split across service classes. Do not treat absence of all tool methods on `RuntimeControlService` as evidence that Goal 1 is missing:

- `RuntimeControlService` owns requirement extraction, draft edits, draft-time free-form amendments, review resolution, and confirmation.
- `RuntimeCommandService` owns pause, cancel, resume, and running next-round requirement scheduling/application.
- `WorkflowRuntimeExecutor` owns workflow start.
- `RuntimeDetailService` owns detail reads and final summary preparation.
- `RuntimeControlStore` owns persisted snapshots and event pagination.

Goal 2 still needs an agent-facing facade or tool adapter that exposes the 15 names in `02-agent-tool-and-requirement-contracts.md`. If `get_workflow_snapshot` and `list_workflow_events` remain store-level methods, the facade must map them to `RuntimeControlStore.get_snapshot` and `RuntimeControlStore.list_events` without reading SQLite directly from the agent package.

Current bootstrap gaps that Goal 2 must close before product agent work:

- add `openai-agents` as an ordinary Python dependency and update the lockfile;
- register `seektalent_conversation_agent` in `pyproject.toml` build metadata;
- create `src/seektalent_conversation_agent/` with real tested store/service code, not scaffold-only files;
- declare `seektalent_conversation_agent` in `tach.toml` with dependency only on allowed public packages;
- add `AppSettings.conversation_agent_db_path` plus a workspace-root-resolved path property and `.env.example` entry;
- expand runtime-control public exports or add a narrow runtime-control facade so the agent adapter imports stable public APIs.

Existing boundary gates already scan `src/seektalent_conversation_agent/` for forbidden provider, runtime, Workbench-internal, and browser-automation imports. Do not add duplicate boundary rules just to satisfy this goal; update the gate only when new import patterns or dependencies need coverage.

Known runtime-control contract gap to verify before Goal 2 acceptance: running next-round requirement amendments that normalize to `needs_review` must support `resolve_requirement_review` with `runtimeRunId`, `baseApprovedRequirementRevisionId`, retargeting after round lock, and `runtime_no_future_round_available` rejection. If this path is still missing at implementation time, fix and test it in runtime-control before the conversation agent claims running amendment review states.

### OpenAI Agents SDK Direction

The product conversational agent uses OpenAI Agents SDK as a packaged dependency inside `src/seektalent_conversation_agent/`.

Goal 2 must not introduce Codex CLI, Codex App Server, Codex MCP server, Codex SDK, `openai-codex`, `@openai/codex-sdk`, or an operator-installed `codex` binary as a product runtime dependency.

The older `local-codex-intake-harness` can be referenced for lessons about local server structure and transcript UX, but it is not the product contract for this goal because it uses a Codex harness direction and does not support in-progress runtime commands with durable transcript interaction.

### Codex-Like Experience Direction

Goal 2 should produce a Codex-like backend experience, not a Codex runtime dependency.

Required Codex-like qualities:

- durable transcript stream with user, assistant, tool, progress, command, detail, error, and final-summary messages;
- explicit tool-call lifecycle state that can be rendered later as natural working-process transcript;
- persisted activity items with started/updated/completed-style lifecycle state and streamable deltas;
- event-grounded progress narration that never invents runtime facts;
- reloadable history after process restart;
- model-input context compaction when a long-running conversation would exceed the model budget;
- explicit token, cost, timeout, and recovery state so long conversations fail closed instead of silently degrading;
- final response grounded in runtime result, selected requirements, user instruction, and safe memory context;
- progress ledger evidence showing which local Codex source paths were inspected and how the idea was adapted or rejected.

Non-goals:

- copying Codex UI;
- using Codex CLI, App Server, MCP, SDK, memory files, or auth state;
- importing or packaging files from `.external/codex-reference`;
- treating Codex source as the product contract.

### Integrated Advisory Memory Direction

The memory phase uses `goal-2-agent-memory-extension/SPEC.md` and `goal-2-agent-memory-extension/PLAN.md` as the detailed contract. It is part of the combined Goal 2 branch, but it is sequenced after the core transcript-agent implementation.

The phase may start only after `ConversationAgentService`, `ConversationStore`, `AgentRuntime`, transcript routes, persisted transcript messages, persisted activity items, and focused core transcript tests exist. If those surfaces do not exist, fix Goal 2 first instead of creating memory code that reads frontend state, parses generated UI assets, or depends on a mock transcript reader.

Memory is advisory. If memory influences hiring requirements, the transcript must present it as a suggestion and route accepted changes through the normal runtime-control requirement amendment, review, and confirmation tools.

---

## Source: `goal-2-conversational-agent/02-transcript-product-contract.md`

## Goal 2 Transcript Product Contract

### Current UI Scope

The Svelte transcript UI is deferred. This file defines the transcript data and view-model behavior that Goal 2 must expose for the future UI.

Do not build a temporary transcript screen in this goal. Backend route responses, persisted transcript messages, and persisted activity items must contain enough structured data for the designer-backed UI to render the states below.

### Transcript Shape

Required transcript-ready regions:

- transcript message list;
- working-process activity item list;
- JD/input message state;
- structured requirement review message;
- runtime progress messages;
- command/result messages;
- tool-call lifecycle state;
- compaction summary metadata for long-running model context;
- final summary message.

The transcript view model may also include compact run status, current stage, source selection, and pending command state as supporting data, but runtime-control remains the source of truth for workflow state. Conversation and transcript records persist transcript projections, runtime links, and latest rendered cursors.

### Conversation Metadata, Archive, Rename, And Reopen State

Conversation metadata is canonical backend state owned by `ConversationStore`. It is not frontend-only display state and it is not stored in OpenAI Agents SDK sessions, Codex memory, or model traces.

Required conversation metadata:

```text
conversation_id
owner_user_id
workspace_id
title
status
is_archived
created_at
updated_at
archived_at
last_opened_at
latest_message_seq
latest_rendered_runtime_event_seq
runtime_run_id
workbench_session_id
latest_draft_revision_id
approved_requirement_revision_id
final_summary_id
pending_user_action
pending_command_count
pending_requirement_review_count
pending_memory_review_count
allowed_actions
```

Rename rules:

- `rename_conversation` updates the backend `title`, `updated_at`, and the reopen/list view-model title;
- rename is a metadata operation only and must not create requirement revisions, runtime events, transcript messages, memory facts, or SDK instructions;
- empty or whitespace-only titles are rejected with a stable reason code;
- title length is bounded by the backend DTO contract;
- duplicate titles are allowed unless a future product requirement says otherwise.

Archive rules:

- `archive_conversation` and `unarchive_conversation` update backend archive metadata and list visibility;
- archiving never deletes transcript messages, activity items, tool calls, runtime links, context summaries, final summaries, or memory records;
- archive state is independent from execution `status`; it must not be used to imply pause, cancel, failure, completion, or requirement approval;
- archiving a conversation with a running or starting workflow is rejected unless runtime control reports the run is paused or terminal, because hiding active work would make progress and command state hard to notice;
- archived conversations are excluded from the default conversation list but remain accessible by id to authorized callers;
- unarchive restores default-list visibility without changing runtime state.

`conversation_reopen_state` is the server-owned header returned when a caller opens or reloads a conversation. It is not a substitute for transcript messages or activity items. It tells the future UI how to resume rendering and which operations are currently allowed.

Required `conversation_reopen_state` fields:

```json
{
  "conversationId": "agent_conv_...",
  "title": "资深 Python 后端",
  "status": "running",
  "isArchived": false,
  "lastOpenedAt": "2026-06-08T00:00:00Z",
  "latestMessageSeq": 42,
  "latestRenderedRuntimeEventSeq": 128,
  "runtimeRunId": "runtime_run_...",
  "workbenchSessionId": "session_...",
  "latestDraftRevisionId": "reqdraft_...",
  "approvedRequirementRevisionId": "reqapproved_...",
  "finalSummaryId": null,
  "pendingUserAction": null,
  "pendingCommandCount": 1,
  "pendingRequirementReviewCount": 0,
  "pendingMemoryReviewCount": 0,
  "compactionSummaryCursor": {
    "latestSummaryId": "ctxsum_...",
    "coveredMessageSeqEnd": 30
  },
  "allowedActions": ["send_message", "request_pause", "ask_detail"],
  "reasonCode": null
}
```

Reload behavior:

- a full reload reads `conversation_reopen_state`, transcript messages, activity items, runtime links, and current snapshot from persisted stores;
- `last_opened_at` may update on a successful authorized reopen;
- reload must not advance `latest_rendered_runtime_event_seq` by itself;
- reload must not mark commands, reviews, memory candidates, or runtime work as resolved;
- stale or broken runtime links are surfaced through reason codes and allowed actions, not hidden behind generated assistant text.

### Codex-Like Activity Lifecycle

SeekTalent must not depend on Codex runtime components, but the backend transcript contract must adopt the useful Codex shape: a conversation contains turns, a turn produces durable items, and items may be started, updated, completed, or failed before the final assistant wording is ready.

Because SeekTalent workflow execution is more autonomous than Codex tool execution, activity items are projections of runtime-control events and snapshots. They are not agent-generated claims.

Required lifecycle statuses:

```text
queued
started
in_progress
completed
failed
cancelled
superseded
```

Required activity item types:

```text
requirement_extraction
workflow_start
round_controller
query_generation
source_dispatch
source_result
merge
scoring
feedback
command
next_round_requirement
detail_answer
finalization
context_compaction
memory_recall
memory_review
```

Projection rules:

- each activity item has a stable `activityId` and deterministic `activityKey` derived from conversation id, runtime run id, event type family, round number, source id, command id, requirement revision id, or compaction summary id;
- `list_workflow_events` is the primary source for activity start, update, completion, failure, cancellation, supersession, counts, and source ids;
- `get_workflow_snapshot` is used to refresh current aggregate status and recover from gaps, not to invent intermediate stages;
- multiple runtime events may update the same activity item in place by advancing `source_event_seq_latest`;
- transcript messages may still append concise narration, but the future UI must be able to render the current working state from activity items without parsing message text;
- an activity item cannot move to `completed`, `failed`, `cancelled`, or `superseded` unless the corresponding runtime-control event, tool-call result, compaction result, or memory review result exists;
- if a long-running runtime step has no fresh event, the activity may remain `in_progress` with its last factual summary; the agent must not fabricate percentages, candidate counts, source names, or stage changes;
- context compaction is represented as a visible `context_compaction` activity item whose lifecycle is backed by `agent_context_summaries` creation or a recorded safe fallback reason;
- advisory memory recall/review may appear as `memory_recall` or `memory_review` activity items, but accepted memory suggestions still route through normal requirement amendment, review, and confirmation APIs before changing runtime input.

Codex reference evidence for this design must be recorded in the progress ledger. At minimum, a Goal 2 worker must inspect the local reference checkout paths for event lifecycle, item types, app-server thread items, compaction, and memory boundaries, then record which patterns were adopted or rejected.

### Context Compaction

Context compaction is for building future OpenAI Agents SDK model input, not for deleting canonical product history.

Rules:

- `ConversationStore` keeps canonical transcript messages, activity items, tool calls, runtime links, requirement revision ids, command state, and final summary context until retention policy allows cleanup;
- `AgentRuntime` may receive a compacted model-input history built from stored transcript messages plus compaction summaries;
- compaction summaries must be persisted and reloadable after process restart;
- a summary must record the covered message sequence range, latest runtime event cursor, requirement draft revision id, approved requirement revision id, final summary id when applicable, and source tool call ids or safe references;
- compaction must not advance `latest_rendered_runtime_event_seq`;
- compaction must not remove unresolved requirement reviews, pending commands, pending user actions, active runtime state, or memory review state from canonical stores;
- compaction must not delete active activity items or collapse their current state into summary text while the runtime is active;
- if a summary cannot be built safely, the agent must use a bounded recent-history window and record a stable reason code instead of fabricating missing state.

Tests must prove a conversation can be reloaded after compaction and still render requirement review history, command history, activity item state, runtime cursor state, and final-summary context from server state.

### Interaction State Matrix

The transcript API and view models must cover these states explicitly:

| State | Required behavior |
| --- | --- |
| New conversation | Conversation API accepts JD or hiring intent; no landing-page state is required in this goal. |
| Conversation reopened | API returns `conversation_reopen_state`, transcript messages, activity items, runtime links, and allowed actions from persisted backend state. |
| Conversation renamed | Backend title is updated and returned from list/reopen responses without changing transcript or runtime facts. |
| Conversation archived | Default list excludes the conversation, but authorized read-by-id can still reload canonical transcript and activity state. |
| Requirement extraction pending | Progress message state is persisted and duplicate extraction uses idempotency. |
| Requirement draft ready | Review sections are returned from the latest draft revision with all items selected by default. |
| Draft edit pending | The response carries pending state and refreshes the revision when the backend operation completes. |
| Stale draft revision | API returns latest backend state and requires explicit caller confirmation before reapplying the edit. |
| Requirement review required | Review-required items return accept, edit, move, and reject allowed actions before confirmation can continue. |
| Requirement confirmed | Confirmation state is locked and the approved revision id is visible in message metadata. |
| Workflow starting | Transcript-ready data includes queued/start state from runtime control, not local optimistic text. |
| Workflow running | New runtime events append progress messages and update activity items without duplicating prior event cursors. |
| Command pending safe boundary | Pause/cancel command data carries accepted or pending state until runtime applies or rejects it. |
| Next-round requirement pending | Transcript-ready data includes the target round number and safe wording that the requirement will take effect before that round starts. |
| Next-round requirement review required | Transcript-ready data includes review allowed actions and does not claim the requirement is scheduled until resolution succeeds. |
| Paused | Conversation API accepts resume, cancel, detail questions, and next-round requirement edits allowed by runtime control. |
| Cancel requested | Data shows pending/applied/rejected command state and does not imply immediate stop during non-interruptible work. |
| Failed | Transcript-ready data includes reason-code-backed failure with retry/resume allowed actions only when runtime control supports them. |
| Completed | Conversation API accepts final-summary instruction and detail questions grounded in final result. |

### Future Responsive And Accessibility Data Requirements

- Requirement items must include concise Chinese display labels and machine-readable allowed actions.
- Long requirement text must be returned as text, not preformatted HTML, so the future UI can wrap it safely.
- Checkbox, edit, delete, move, enable/disable, confirm, pause, cancel, resume, archive, unarchive, rename, and summary actions must have stable action ids suitable for accessible labels.
- Progress updates must include message ids, activity ids, statuses, and event cursors so the future UI can avoid focus-stealing rerenders.
- Error messages use stable reason codes internally and short Chinese text in response payloads.

### Requirement Review Message

The requirement review appears after `extract_requirements` returns a draft.

It must include:

| Section | Backend field | User-facing meaning | Actions |
| --- | --- | --- | --- |
| 必须满足 | `must_have_capabilities` | 候选人必须具备的能力 | checkbox, edit, delete, move to 加分项 |
| 加分项 | `preferred_capabilities` | 有则更匹配的能力或背景 | checkbox, edit, delete, move to 必须满足 |
| 硬性筛选条件 | `hard_constraints` | 地点、学历、经验、年龄、性别、学校、学校类型、公司 | checkbox, edit, delete |
| 排除信号 | `exclusion_signals` | 出现后明显不匹配的信号 | checkbox, edit, delete |
| 检索关键词 | `initial_query_term_pool[].term` | 用于召回简历的关键词 | checkbox, enable/disable, edit, delete |

Every item is checked by default. Edits must call `update_requirement_draft` and refresh the returned revision. Confirmation calls `confirm_requirements` and then starts the workflow only after a valid approved revision exists.

### Runtime Progress Messages

Progress messages come from `list_workflow_events` and `get_workflow_snapshot`.

The projection must produce two outputs from the same factual inputs:

1. append-only `runtime_progress` or `command_state` transcript messages for the chronological transcript;
2. update-in-place activity items for the current working-process state.

Messages and activity items should describe:

- requirement extraction start and completion;
- workflow queued and started;
- round controller actions;
- query term generation;
- source dispatch and returned counts;
- merge and scoring counts;
- reflection/feedback;
- command acceptance and application;
- pause, resume, cancel, failure, and completion;
- finalization.

Each progress message should carry the source event ids or event cursor internally so the future UI can avoid duplicate rendering.

Each activity item must carry `activityId`, `activityType`, `activityKey`, `status`, Chinese `title`, Chinese `summary`, `payload`, `sourceRuntimeRunId`, `sourceEventSeqStart`, `sourceEventSeqLatest`, `startedAt`, `updatedAt`, and `completedAt` when applicable.

The future UI stream may send deltas such as `activity_started`, `activity_updated`, `activity_completed`, `transcript_message_added`, `tool_call_updated`, and `snapshot_updated`. Those deltas are transport shapes over persisted state; if a client reconnects, reload must rebuild the same state from persisted messages, activity items, runtime links, and snapshots.

### User Commands

The conversation message API accepts natural language while a run is active.

Required intents:

- pause the run;
- cancel/end the run;
- resume a paused run;
- add a next-round requirement;
- ask what the runtime is doing now;
- ask why a query was used;
- ask why a candidate was scored or filtered;
- request final summary.

The transcript data must show accepted/pending/applied/rejected command state from runtime-control records.

For next-round requirements, the transcript must not say the requirement is already active until runtime control emits `runtime_requirement_revision_activated`. Before that event, the wording should be equivalent to: `已记录，将在第 X 轮开始前生效。`

If the next-round requirement needs review, the transcript-ready text must say it is waiting for user confirmation and must not say it is scheduled. If review finishes after the original target round locks, the response must include the new target round returned by runtime control.

### Extra Requirement Input

The caller can add extra requirements in two ways:

- targeted: provide a section hint such as 必须满足 or 排除信号;
- conversational: type a free-form message such as `另外希望做过 toB SaaS，频繁跳槽的不要`.

Both paths call runtime-control free-form amendment normalization. The transcript data returns the normalized additions as draft items before confirmation. If runtime-control marks an amendment as review-required, the caller must resolve it before final confirmation.

The agent must not silently map free-form text to backend fields without runtime-control normalization.

Review-required items must include the runtime-normalized candidate, original user fragment, reason code, and allowed actions. Resolution calls `resolve_requirement_review`.

If the draft changed while the caller was reviewing, the API handles `requirement_draft_stale` by returning the latest draft and requiring explicit confirmation before reapplying resolution operations.

### Detail Answers

When the user asks for details, the agent calls `get_runtime_detail`.

Answers must be grounded in:

- runtime events;
- snapshots;
- checkpoints;
- Workbench-visible candidate/result data;
- safe artifact refs.

The agent must not reveal raw provider payloads, cookies, auth headers, browser state, or hidden debug artifacts.

### Final Summary

After workflow completion, the agent calls `prepare_final_summary`.

The final message must include:

- what requirement revision was used;
- what sources ran;
- high-level search/round outcome;
- candidate/result summary when available;
- important constraints or risks;
- direct response to any user-provided final-summary instruction.

---

## Source: `goal-2-conversational-agent/03-agent-tool-use-contract.md`

## Goal 2 Agent Tool Use Contract

### Agent Package

Create the agent package here:

```text
src/seektalent_conversation_agent/
```

This is the primary home for Goal 2 backend code. Do not put agent orchestration, transcript state, intent handling, or runtime-control tool selection inside `src/seektalent/` or `src/seektalent_ui/`.

The package should expose:

```text
ConversationAgentService
ConversationStore
AgentRuntime
AgentToolAdapter
TranscriptMessage
AgentIntent
AgentResponse
```

`AgentRuntime` owns OpenAI Agents SDK construction and execution. Route handlers and frontend-facing API modules must not import OpenAI Agents SDK directly.

### Tool Adapter

The adapter may call only Goal 1 runtime-control public APIs.

Allowed tool calls:

```text
extract_requirements
get_requirement_draft
update_requirement_draft
amend_requirement_draft_from_text
resolve_requirement_review
confirm_requirements
start_workflow
get_workflow_snapshot
list_workflow_events
request_pause
request_cancel
resume_workflow
submit_next_round_requirement
get_runtime_detail
prepare_final_summary
```

Forbidden:

```text
import seektalent.runtime
import seektalent.providers
from seektalent.models import RunState
direct SQLite reads from runtime_control.sqlite3 in frontend code
direct construction of WorkflowRuntime in agent code
calling codex, codex app-server, or codex mcp-server
import openai_codex
import @openai/codex-sdk
```

### Intent Handling

The agent service must classify user turns into these intent families:

```text
submit_jd
edit_requirement_draft
resolve_requirement_review
confirm_requirements
start_or_continue_workflow
pause_workflow
cancel_workflow
resume_workflow
add_next_round_requirement
ask_runtime_status
ask_runtime_detail
request_final_summary
general_clarification
```

Intent classification can be implemented with deterministic rules plus an LLM where needed. Any LLM structured output must validate against typed models. A bounded retry is allowed only when the model response fails schema validation.

### Agent Budget And Error Policy

Goal 2 must define local budget controls before the first OpenAI Agents SDK call ships.

Required settings or persisted defaults:

```text
agent_turn_input_token_budget
agent_turn_output_token_budget
agent_conversation_token_budget
agent_compaction_trigger_token_budget
agent_monthly_cost_budget_cents
agent_model_timeout_seconds
agent_tool_timeout_seconds
agent_stream_heartbeat_seconds
```

Budget rules:

- every model turn records estimated or provider-reported input tokens, output tokens, model name, and cost basis in `agent_tool_calls` or an equivalent usage table;
- if a turn would exceed `agent_turn_input_token_budget`, the service must compact model input first or reject with `agent_token_budget_exceeded`;
- if a conversation exceeds `agent_conversation_token_budget`, the service must require successful compaction before another model turn;
- `agent_monthly_cost_budget_cents` may be unset for local development, but when set it must fail closed with `agent_cost_budget_exceeded`;
- memory recall, transcript history, tool outputs, and detail answers must each have bounded contribution to the final prompt; no component may consume the whole prompt budget by itself.

Error recovery rules:

- automatic model fallback chains are not a default behavior; using a cheaper or alternate model requires an explicit setting and must be visible in usage metadata;
- a bounded retry is allowed only for structured-output validation failures after the model returned a response;
- runtime-control state-changing tools may be retried only with the same idempotency key and only when the prior outcome is unknown;
- read-only event polling, snapshot reads, and detail reads may retry once on transient transport errors, then persist a recoverable transcript error with a stable reason code;
- model timeout, model unavailable, tool timeout, and stream disconnect must produce typed errors such as `agent_model_timeout`, `agent_model_unavailable`, `agent_tool_timeout`, or `agent_stream_disconnected`;
- SSE or polling reconnect resumes from the last persisted message sequence and activity cursor, not from model memory.

### Transcript Persistence

Agent state must persist:

- conversation id;
- conversation title;
- archive state;
- last opened timestamp;
- user messages;
- assistant messages;
- tool call requests;
- tool call results;
- linked runtime run id;
- linked requirement draft revision id;
- linked approved requirement revision id;
- latest event cursor rendered to transcript;
- pending user action;
- pending command count;
- pending requirement review count;
- pending memory review count;
- final summary id when available.

This state can live in a dedicated conversation table/store or a route-level store backed by the existing local server storage conventions. It must not live only in frontend state, OpenAI Agents SDK session state, model traces, or Codex memory.

Use the dedicated persistence contract in `SPEC.md`. Route handlers may read and write transcript state only through `src/seektalent_conversation_agent/`.

### Tool Call Rules

1. A JD submission calls `extract_requirements`.
2. Requirement edits call `update_requirement_draft`.
3. Free-form additional requirements call `amend_requirement_draft_from_text`.
4. Review-required draft or running amendments call `resolve_requirement_review`.
5. Confirm calls `confirm_requirements`.
6. Workflow start calls `start_workflow` only after confirmation.
7. Progress polling calls `list_workflow_events` with the last rendered cursor.
8. Snapshot refresh calls `get_workflow_snapshot`.
9. Pause/cancel/resume commands call the matching command tools.
10. Next-round requirement changes call `submit_next_round_requirement` and project the returned target round/effective boundary into transcript-ready data.
11. Detail questions call `get_runtime_detail`.
12. Final summaries call `prepare_final_summary`.

If a tool returns `requirement_draft_stale`, the agent must refresh the latest draft and ask the user whether to reapply the edit. It must not ask OpenAI Agents SDK to invent a merge.

If a tool returns `runtime_event_gap_detected`, the agent must refresh the snapshot and persist a recoverable sync error instead of skipping missing events.

Free-form user text must pass a lightweight safety screen before it reaches runtime-control normalization:

- reject obvious candidate PII such as email addresses, phone numbers, personal ids, raw resume blocks, provider payload fragments, cookies, auth headers, and secrets with stable reason codes;
- allow normal hiring criteria text even when it contains company names, role names, technologies, seniority, salary, location, or language requirements;
- preserve the original rejected fragment only as a hash plus reason code unless an existing Workbench-visible policy already allows the raw text;
- tests must cover email, phone, secret-like token, raw resume marker, and safe ordinary requirement text.

### Grounding Rules

The agent can summarize and phrase. It cannot create factual runtime state.

Every message about run progress, command state, candidate counts, source returns, scoring, filtering, checkpoint, or final result must be grounded in a tool result. The transcript store must retain enough metadata to trace the message back to tool call id and event cursor.

Messages about running requirement changes must distinguish:

- accepted but not active;
- normalized but waiting for user review;
- scheduled for target round;
- active for target round;
- rejected because no future round is available.

### OpenAI Agents SDK Boundary

OpenAI Agents SDK is used for conversation orchestration and tool routing.

Rules:

1. `ConversationAgentService` calls `AgentRuntime`.
2. `AgentRuntime` constructs SDK agents, tools, runner config, and trace config.
3. SDK function tools call `AgentToolAdapter`.
4. `AgentToolAdapter` calls only runtime-control public APIs.
5. `ConversationStore` is reloaded before and after SDK runs so product state survives process restart.
6. SDK session state must be reconstructible from `ConversationStore`.
7. SDK traces must follow `../04-operating-policies-and-runtime-contracts.md` and `../04-operating-policies-and-runtime-contracts.md`.

---

## Source: `goal-2-conversational-agent/04-frontend-backend-boundaries.md`

## Goal 2 Frontend Backend Boundaries

### Primary Backend Package

Goal 2 main backend code belongs in:

```text
src/seektalent_conversation_agent/
```

This package owns conversation state, transcript message projection, intent handling, runtime-control tool calls, final summary orchestration, and service tests.

OpenAI Agents SDK construction belongs behind `AgentRuntime` in this package. Route files in `src/seektalent_ui/` and Svelte frontend files must not import OpenAI Agents SDK directly.

### Thin Backend Route Wiring

Expected existing UI-package touch points:

```text
src/seektalent_ui/agent_routes.py
src/seektalent_ui/conversation_routes.py
src/seektalent_ui/models.py
src/seektalent_ui/server.py
src/seektalent_ui/event_routes.py
tests/
```

Use either `agent_routes.py` or `conversation_routes.py` based on the naming that best matches the final route structure. Do not create both if one would be unused. Route files should be thin wrappers that validate HTTP inputs, call `src/seektalent_conversation_agent/`, and return typed responses.

### Frontend UI Scope

The Svelte transcript UI is deferred until designer-provided screens are available. Goal 2 should not add temporary transcript screens or memory screens.

Frontend files should change only when API type generation or server registration requires it. If frontend files are touched, the progress ledger must explain why the change is not a temporary UI implementation.

Future designer-backed UI files are expected under:

```text
apps/web-svelte/src/routes/(app)/agent/
apps/web-svelte/src/lib/agent/
apps/web-svelte/src/lib/components/
apps/web-svelte/src/lib/api/
apps/web-svelte/src/lib/types/
```

Generated static files under `src/seektalent_ui/static/workbench/` should change only when API type generation or an explicitly approved frontend build requires it.

### API Boundary

Backend routes should expose transcript operations such as:

```text
POST /api/agent/conversations
GET  /api/agent/conversations
GET  /api/agent/conversations/{conversation_id}
PATCH /api/agent/conversations/{conversation_id}/title
POST /api/agent/conversations/{conversation_id}/archive
POST /api/agent/conversations/{conversation_id}/unarchive
POST /api/agent/conversations/{conversation_id}/messages
GET  /api/agent/conversations/{conversation_id}/events
POST /api/agent/conversations/{conversation_id}/requirements/operations
POST /api/agent/conversations/{conversation_id}/requirements/amend-from-text
POST /api/agent/conversations/{conversation_id}/requirements/resolve-review
POST /api/agent/conversations/{conversation_id}/requirements/confirm
POST /api/agent/conversations/{conversation_id}/workflow/start
POST /api/agent/conversations/{conversation_id}/workflow/commands
GET  /api/agent/conversations/{conversation_id}/workflow/snapshot
GET  /api/agent/conversations/{conversation_id}/workflow/events
```

The exact route names may follow existing Workbench route style, but every required transcript operation must have a real backend endpoint and tests.

Conversation list/read routes must return backend-owned title and archive metadata. `GET /api/agent/conversations/{conversation_id}` must return `conversation_reopen_state` with transcript messages, activity items, current runtime link state, and allowed actions needed to reopen the thread after browser reload or process restart.

### DTO Shape And Versioning

HTTP JSON uses camelCase. Internal Python models may use snake_case. The conversion boundary belongs in `src/seektalent_ui/` route DTOs or an explicit API model module, not in Svelte components and not in OpenAI Agents SDK prompts.

Rules:

- every `/api/agent` response includes `schemaVersion` for the top-level response family;
- request DTOs reject unknown enum values and invalid casing with stable reason codes;
- response DTOs expose ids, cursors, statuses, allowed actions, and reason codes as machine-readable fields;
- generated frontend API types must be refreshed when route schemas change;
- compatibility-breaking DTO changes require a schema version bump and tests proving old persisted store rows still reload or fail with a stable migration error.

### Security Boundary

`/api/agent` must be added to the guarded API prefixes alongside Workbench APIs.

Required route posture:

- read routes use the same current-user dependency posture as Workbench read routes;
- write routes use the same CSRF/current-user dependency posture as Workbench write routes;
- state-changing `/api/agent` routes enforce local per-user and per-conversation rate limits with stable `agent_rate_limited` errors;
- event stream routes re-check session identity during streaming;
- host and origin guard tests include `/api/agent`;
- disabled Workbench or disabled agent feature gates cannot leave transcript write APIs reachable.

### Frontend State Boundary

Future Svelte components may hold temporary input text, open editor state, focused item id, and local optimistic UI state while a request is pending.

Canonical state must come from backend responses:

- conversation title;
- archive state;
- conversation reopen state;
- transcript messages;
- activity items;
- requirement draft revision;
- selected item ids;
- approved revision id;
- runtime run id;
- latest rendered event cursor;
- command state;
- final summary.

After a failed request, the future UI must re-render from backend state instead of keeping local edits as canonical.

### UI-Ready Data Requirements

- Route responses must contain all data needed for the future transcript screen described in `../04-operating-policies-and-runtime-contracts.md`.
- Requirement section labels and item labels must match the shared product spec.
- Every future control must correspond to a real backend operation and stable allowed-action id.
- Pending/disabled/loading state must be derivable from backend state and idempotency keys.
- Error payloads must include stable reason codes and short Chinese text.

### Tests

Backend:

- conversation store initialization and ownership;
- conversation create/read;
- JD submission;
- requirement edit operations;
- free-form requirement amendment;
- review-required requirement resolution;
- confirmation;
- workflow start;
- command routing;
- event polling;
- host/origin/auth/CSRF posture for `/api/agent`;
- final summary.

API/view-model:

- transcript create/reload view model;
- requirement review response shape;
- default selected checkbox state in response data;
- edit/delete/move/enable-disable allowed-action mapping;
- free-form extra requirement normalization response data;
- review-required accept/edit/move/reject response data;
- confirmation response data;
- runtime progress projection data from events;
- pause/cancel/resume command-state response data;
- final summary response data.

---

## Source: `goal-2-conversational-agent/08-conversation-storage-contract.md`

## Goal 2 Conversation Storage Contract

### Database

Use a dedicated conversation-agent SQLite database, exposed through `AppSettings.conversation_agent_db_path`:

```text
.seektalent/conversation_agent.sqlite3
```

Relative values are resolved with the same workspace-root rules as Workbench and runtime-control paths. Production defaults must not point into the repository.

This store is owned by `src/seektalent_conversation_agent/`. Route handlers in `src/seektalent_ui/` must not read or write this database directly.

### Tables

#### `agent_conversations`

```sql
CREATE TABLE agent_conversations (
  conversation_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  title_updated_at TEXT,
  is_archived INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT,
  archive_reason_code TEXT,
  last_opened_at TEXT,
  latest_message_seq INTEGER NOT NULL DEFAULT 0,
  latest_rendered_runtime_event_seq INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
```

Statuses:

```text
draft
awaiting_requirement_confirmation
running
paused
cancelled
completed
failed
```

`is_archived` is list visibility metadata, not an execution status. It must not replace or overwrite `status`.

#### `agent_transcript_messages`

```sql
CREATE TABLE agent_transcript_messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  message_seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  message_type TEXT NOT NULL,
  text TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_tool_call_id TEXT,
  source_runtime_run_id TEXT,
  source_runtime_event_seq INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(conversation_id, message_seq),
  UNIQUE(conversation_id, source_runtime_run_id, source_runtime_event_seq)
);
```

Roles:

```text
user
assistant
tool
system
```

Message types:

```text
user_text
requirement_review
runtime_progress
runtime_activity
command_state
detail_answer
final_summary
error
```

`runtime_activity` messages are optional append-only narration for an activity lifecycle transition. The current renderable activity state is stored in `agent_transcript_activity_items`; the future UI must not need to parse `runtime_activity` text to determine status.

#### `agent_transcript_activity_items`

```sql
CREATE TABLE agent_transcript_activity_items (
  activity_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  activity_key TEXT NOT NULL,
  activity_type TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_tool_call_id TEXT,
  source_runtime_run_id TEXT,
  source_event_seq_start INTEGER,
  source_event_seq_latest INTEGER,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  reason_code TEXT,
  UNIQUE(conversation_id, activity_key)
);
```

Activity item statuses:

```text
queued
started
in_progress
completed
failed
cancelled
superseded
```

Activity items are durable UI-ready projections. They are not canonical runtime, requirement, candidate, command, final-summary, or memory state. If an activity projection is stale or missing, the service must rebuild it from persisted runtime-control events, snapshots, tool-call records, compaction summaries, and memory review records.

#### `agent_tool_calls`

```sql
CREATE TABLE agent_tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_json TEXT,
  status TEXT NOT NULL,
  reason_code TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(conversation_id, idempotency_key)
);
```

#### `agent_runtime_links`

```sql
CREATE TABLE agent_runtime_links (
  conversation_id TEXT PRIMARY KEY,
  runtime_run_id TEXT,
  workbench_session_id TEXT,
  latest_draft_revision_id TEXT,
  approved_requirement_revision_id TEXT,
  latest_runtime_event_seq INTEGER,
  final_summary_id TEXT,
  link_status TEXT NOT NULL DEFAULT 'active',
  link_reason_code TEXT,
  updated_at TEXT NOT NULL
);
```

#### `agent_context_summaries`

```sql
CREATE TABLE agent_context_summaries (
  summary_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  covered_message_seq_start INTEGER NOT NULL,
  covered_message_seq_end INTEGER NOT NULL,
  latest_runtime_event_seq INTEGER,
  latest_draft_revision_id TEXT,
  approved_requirement_revision_id TEXT,
  final_summary_id TEXT,
  summary_text TEXT NOT NULL,
  safe_refs_json TEXT NOT NULL,
  reason_code TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(conversation_id, covered_message_seq_start, covered_message_seq_end)
);
```

`agent_context_summaries` rows are derived model-input context. They are not canonical transcript, requirement, command, runtime, final-summary, or memory state. If a summary is wrong or stale, the service must rebuild it from canonical stores.

#### `agent_context_compactions`

```sql
CREATE TABLE agent_context_compactions (
  compaction_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  status TEXT NOT NULL,
  trigger_reason_code TEXT NOT NULL,
  covered_message_seq_start INTEGER NOT NULL,
  covered_message_seq_end INTEGER NOT NULL,
  latest_runtime_event_seq INTEGER,
  latest_draft_revision_id TEXT,
  approved_requirement_revision_id TEXT,
  pending_state_json TEXT NOT NULL,
  quality_check_json TEXT NOT NULL,
  summary_id TEXT,
  failure_reason_code TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(conversation_id, covered_message_seq_start, covered_message_seq_end, trigger_reason_code)
);
```

`status` is one of `in_progress`, `completed`, or `failed`. A completed compaction must point at an `agent_context_summaries.summary_id`. A failed compaction must preserve `failure_reason_code` and must not change the model-input cursor.

Compaction quality rules:

- start only when there is no unresolved requirement review, pending command, pending user action, active runtime state that lacks an event cursor, or pending memory review that would be represented only in summary text;
- record the pending-state counts and source cursors in `pending_state_json` before summarization;
- validate that the summary preserves current requirement revision ids, runtime run id, latest rendered event cursor, final summary id when present, pending counts, activity item ids still active, and safe source refs;
- fail with `agent_compaction_quality_failed` instead of storing a summary that drops required state;
- failed or interrupted compaction must be visible as a `context_compaction` activity item and must fall back to a bounded recent-history prompt window.

### Cursor Rules

Progress projection must be cursor-based:

1. read `latest_rendered_runtime_event_seq`;
2. call runtime control `list_workflow_events(afterSeq=latest_rendered_runtime_event_seq)`;
3. create transcript messages for new events in event order;
4. upsert activity items affected by those events in deterministic activity-key order;
5. update `latest_rendered_runtime_event_seq` in the same transaction as inserted messages and activity-item updates.

Tests must prove replay after process restart does not duplicate transcript progress messages or activity items.

If runtime control returns `runtime_event_gap_detected`, the conversation agent must not advance `latest_rendered_runtime_event_seq`. It should refresh the runtime snapshot and persist an error transcript message that is traceable to the failed event poll.

Concurrent projection from multiple browser tabs must not create duplicate progress messages. The unique `(conversation_id, source_runtime_run_id, source_runtime_event_seq)` constraint is required for runtime progress messages.

Concurrent projection from multiple browser tabs must not create duplicate activity items. The unique `(conversation_id, activity_key)` constraint is required for activity items, and updates must be monotonic: `source_event_seq_latest` never moves backward.

Activity item projection from runtime events must be idempotent:

- a `*_started`, `runtime_run_queued`, command accepted, compaction start, or memory recall start event creates or updates the item to `queued`, `started`, or `in_progress`;
- a `*_completed`, command applied, compaction summary created, or memory review accepted/rejected event moves the item to a terminal state only when that terminal fact exists;
- a failure, cancellation, rejection, or supersession event moves the item to `failed`, `cancelled`, or `superseded` with a stable reason code;
- aggregate count updates may change `summary` and `payload_json`, but they must stay traceable to `source_event_seq_latest`.

### Cross-Store Link Rules

`agent_runtime_links` references runtime-control and Workbench rows that live outside this database. The agent store must therefore treat links as application-validated references.

Rules:

- `runtime_run_id`, `workbench_session_id`, `latest_draft_revision_id`, and `approved_requirement_revision_id` are updated only from runtime-control tool responses;
- missing or inconsistent runtime-control links set `link_status='broken'` and a stable `link_reason_code`;
- agent responses must surface broken links as `runtime_link_broken` or `workbench_session_missing`;
- link reconciliation must be tested after restart and after duplicate start calls.

### Security And Privacy

The conversation store may contain JD text, requirement review text, assistant messages, safe runtime summaries, command state, and safe detail answers.

It must not store:

- raw provider cookies;
- auth headers;
- browser storage;
- raw provider payloads;
- raw resume text outside existing Workbench-visible policy;
- Codex auth state.

### Tests

Add tests for:

- empty DB initialization;
- future-version rejection;
- path resolution through `workspace_root`;
- conversation create/read ownership;
- conversation list excludes archived conversations by default and can include them when requested;
- rename persists backend title and rejects empty or over-limit titles;
- archive/unarchive persists backend metadata without deleting transcript messages, activity items, runtime links, context summaries, or memory references;
- archiving an active running or starting workflow is rejected unless runtime control reports the run is paused or terminal;
- `conversation_reopen_state` after restart includes title, archive state, latest message seq, latest runtime cursor, runtime link ids, pending counts, compaction cursor, and allowed actions;
- message sequence monotonicity;
- tool call idempotency;
- runtime link update;
- cursor-based event projection without duplicate transcript messages;
- cursor gap handling without advancing the rendered cursor;
- cross-store link reconciliation and broken-link reason codes;
- context summary creation, reload, stale-summary rebuild, and no cursor advancement during compaction;
- compaction `in_progress`, `completed`, and `failed` state recovery after restart;
- compaction quality failure preserving the prior model-input cursor and activity state;
- privacy-sensitive payload rejection or redaction;
- token and cost budget rejection paths;
- model timeout, model unavailable, tool timeout, and stream reconnect behavior;
- `/api/agent` DTO schema version and camelCase response contract;
- `/api/agent` state-changing route rate limits.
