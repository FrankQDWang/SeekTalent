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

Build the conversational-agent backend, API routes, transcript persistence, and UI-ready view models over the completed runtime control plane.

The agent accepts a JD or hiring intent through backend conversation APIs, calls runtime-control tools for requirement extraction, returns editable requirement confirmation data, sends confirmed requirements back to runtime control, starts and observes the workflow, persists transcript-ready progress messages, accepts user commands, answers grounded detail questions, and produces a final summary.

The Svelte transcript UI is not part of this goal. It will be implemented later from designer-provided screens and must consume the DTOs and view models produced here.

### Product Result

After this goal, a user can:

1. create and reload a local agent conversation through real backend APIs;
2. submit a JD or rough hiring request;
3. receive structured requirement sections in Chinese;
4. receive selected-by-default item state for every extracted item;
5. edit, delete, move, select/unselect, and enable/disable supported requirement items through persisted operations;
6. confirm the requirement revision;
7. read transcript-ready workflow progress backed by runtime-control events;
8. submit natural-language commands to pause, cancel, resume, add next-round requirements, or ask details;
9. receive command state reflected from persisted runtime-control records;
10. receive a final summary grounded in final runtime result and the user's instruction.

### Scope

In scope:

- conversational agent backend service;
- OpenAI Agents SDK runtime isolated behind `AgentRuntime`;
- transcript session/message persistence;
- dedicated conversation-agent SQLite store;
- agent API routes;
- runtime-control tool adapter;
- intent parsing for requirement edits, confirmation, workflow start, runtime commands, detail questions, and final summary requests;
- UI-ready transcript DTOs and view models;
- editable requirement confirmation API and view-model contract;
- event-driven transcript progress projection;
- final summary flow;
- backend tests, agent evaluations, API/view-model tests, and Workbench integration verification.

Out of scope:

- building the runtime-control APIs that Goal 1 owns;
- product runtime dependency on Codex CLI, Codex App Server, Codex MCP server, or Codex SDK;
- direct runtime imports in the agent package;
- cloud hosting;
- cross-user collaboration;
- voice interaction;
- arbitrary source provider configuration UI beyond the existing source catalog surface;
- long-term advisory memory beyond the post-Goal-2 memory extension;
- replacing the current Workbench product;
- implementing Svelte transcript screens before designer-provided UI is available;
- implementing temporary transcript UI controls for this goal.

### Dependency On Goal 1

Goal 2 cannot start product implementation until Goal 1 provides real runtime-control APIs, storage, events, snapshots, commands, checkpoint semantics, and verification evidence.

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

### OpenAI Agents SDK Direction

The product conversational agent uses OpenAI Agents SDK as a packaged dependency inside `src/seektalent_conversation_agent/`.

Goal 2 must not introduce Codex CLI, Codex App Server, Codex MCP server, Codex SDK, `openai-codex`, `@openai/codex-sdk`, or an operator-installed `codex` binary as a product runtime dependency.

The older `local-codex-intake-harness` can be referenced for lessons about local server structure and transcript UX, but it is not the product contract for this goal because it uses a Codex harness direction and does not support in-progress runtime commands with durable transcript interaction.

---

## Source: `goal-2-conversational-agent/02-transcript-product-contract.md`

## Goal 2 Transcript Product Contract

### Current UI Scope

The Svelte transcript UI is deferred. This file defines the transcript data and view-model behavior that Goal 2 must expose for the future UI.

Do not build a temporary transcript screen in this goal. Backend route responses and persisted transcript messages must contain enough structured data for the designer-backed UI to render the states below.

### Transcript Shape

Required transcript-ready regions:

- transcript message list;
- JD/input message state;
- structured requirement review message;
- runtime progress messages;
- command/result messages;
- final summary message.

The transcript view model may also include compact run status, current stage, source selection, and pending command state as supporting data, but runtime-control remains the source of truth for workflow state. Conversation and transcript records persist transcript projections, runtime links, and latest rendered cursors.

### Interaction State Matrix

The transcript API and view models must cover these states explicitly:

| State | Required behavior |
| --- | --- |
| New conversation | Conversation API accepts JD or hiring intent; no landing-page state is required in this goal. |
| Requirement extraction pending | Progress message state is persisted and duplicate extraction uses idempotency. |
| Requirement draft ready | Review sections are returned from the latest draft revision with all items selected by default. |
| Draft edit pending | The response carries pending state and refreshes the revision when the backend operation completes. |
| Stale draft revision | API returns latest backend state and requires explicit caller confirmation before reapplying the edit. |
| Requirement review required | Review-required items return accept, edit, move, and reject allowed actions before confirmation can continue. |
| Requirement confirmed | Confirmation state is locked and the approved revision id is visible in message metadata. |
| Workflow starting | Transcript-ready data includes queued/start state from runtime control, not local optimistic text. |
| Workflow running | New runtime events append without duplicating prior event cursors. |
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
- Checkbox, edit, delete, move, enable/disable, confirm, pause, cancel, resume, and summary actions must have stable action ids suitable for accessible labels.
- Progress updates must include message ids and event cursors so the future UI can avoid focus-stealing rerenders.
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

Messages should describe:

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

### Transcript Persistence

Agent state must persist:

- conversation id;
- user messages;
- assistant messages;
- tool call requests;
- tool call results;
- linked runtime run id;
- linked requirement draft revision id;
- linked approved requirement revision id;
- latest event cursor rendered to transcript;
- pending user action;
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
GET  /api/agent/conversations/{conversation_id}
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

### Security Boundary

`/api/agent` must be added to the guarded API prefixes alongside Workbench APIs.

Required route posture:

- read routes use the same current-user dependency posture as Workbench read routes;
- write routes use the same CSRF/current-user dependency posture as Workbench write routes;
- event stream routes re-check session identity during streaming;
- host and origin guard tests include `/api/agent`;
- disabled Workbench or disabled agent feature gates cannot leave transcript write APIs reachable.

### Frontend State Boundary

Future Svelte components may hold temporary input text, open editor state, focused item id, and local optimistic UI state while a request is pending.

Canonical state must come from backend responses:

- transcript messages;
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
  title TEXT,
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
command_state
detail_answer
final_summary
error
```

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

### Cursor Rules

Progress projection must be cursor-based:

1. read `latest_rendered_runtime_event_seq`;
2. call runtime control `list_workflow_events(afterSeq=latest_rendered_runtime_event_seq)`;
3. create transcript messages for new events in event order;
4. update `latest_rendered_runtime_event_seq` in the same transaction as inserted messages.

Tests must prove replay after process restart does not duplicate transcript progress messages.

If runtime control returns `runtime_event_gap_detected`, the conversation agent must not advance `latest_rendered_runtime_event_seq`. It should refresh the runtime snapshot and persist an error transcript message that is traceable to the failed event poll.

Concurrent projection from multiple browser tabs must not create duplicate progress messages. The unique `(conversation_id, source_runtime_run_id, source_runtime_event_seq)` constraint is required for runtime progress messages.

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
- message sequence monotonicity;
- tool call idempotency;
- runtime link update;
- cursor-based event projection without duplicate transcript messages;
- cursor gap handling without advancing the rendered cursor;
- cross-store link reconciliation and broken-link reason codes;
- privacy-sensitive payload rejection or redaction.
