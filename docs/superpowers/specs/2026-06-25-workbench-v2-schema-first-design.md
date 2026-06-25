# Workbench v2 Schema-First Design Spec

> **Date:** 2026-06-25
> **Branch:** `codex/workbench-v2-schema-first`
> **Status:** Spec review gate
> **Owner:** SeekTalent Workbench

## 1. Problem

The current Workbench conversation path mixes first-turn stores, outbox recovery, runtime projections, transcript groups, requirement drafts, and UI-specific grouping. This makes a basic agent transcript hard to reason about and hard to test. The visible failures are direct symptoms of that shape: the UI can render an old "已处理" screen instead of a chat transcript, long JD text can be clipped, requirement confirmation can live outside the transcript, duplicate assistant statuses can appear, and manual testing can hit stale routes or partially migrated view models.

The replacement must be simpler: one conversation agent sits between users and the stable SeekTalent runtime. The UI renders a transcript from persisted events. Runtime progress and results are converted into user-readable events, not leaked as internal audit or tool records.

## 2. Goals

1. Build Workbench v2 beside the old Workbench, without editing old first-turn/outbox semantics as the primary path.
2. Use one SQLite database as the v2 source of truth for conversations and transcript events.
3. Render the UI directly from ordered transcript events.
4. Support pure chat, pasted JD, supplementary requirements, requirement confirmation, runtime progress questions, and result explanation in one transcript.
5. Classify every user turn before taking action: pure chat, new JD/recruitment input, supplementary requirement, requirement confirmation, runtime progress question, result question, or memory-related request.
6. For turns classified as new JD/recruitment input or supplementary requirement, normalize arbitrary user text into `jobTitle`, `jd`, and optional `notes` when needed.
7. Ensure runtime starts only after `jobTitle` and `jd` are present; `notes` remains optional.
8. Use Bailian native strict structured output for the conversation agent response schema.
9. Keep the stable runtime behind an in-process service boundary for this phase.
10. Track formal Superpowers specs and plans in git while keeping large artifacts ignored.

## 3. Non-Goals

1. Do not migrate old historical Workbench conversations into v2.
2. Do not introduce gRPC, MCP skills, sandboxed skills, or a new workflow engine in this phase.
3. Do not make OpenAI Agents SDK own persistence, runtime state, background recovery, or transcript rendering.
4. Do not keep compatibility with old UI transcript groups inside the v2 UI path.
5. Do not delete the old Workbench until v2 passes manual and automated acceptance.

## 4. Documentation Tracking Policy

`docs/superpowers/` remains ignored by default because it can contain large artifacts and local scratch outputs. The repo should track only lightweight formal Markdown specs and plans:

```gitignore
docs/superpowers/**
!docs/superpowers/
!docs/superpowers/specs/
!docs/superpowers/plans/
!docs/superpowers/specs/*.md
!docs/superpowers/plans/*.md
docs/superpowers/artifacts/**
docs/superpowers/audits/**
docs/superpowers/tmp/**
```

Already tracked artifacts remain tracked until a separate cleanup explicitly removes them. New screenshots, exports, audits, and generated files stay local unless they are intentionally force-added.

## 5. Architecture

Workbench v2 has four narrow units:

1. **Thin route shim:** HTTP endpoints under `/api/agent/workbench/v2/*`. This is the only v2 code that must live near `src/seektalent_ui`; it validates HTTP request shape and delegates immediately.
2. **Clean v2 backend package:** New backend logic lives outside the old polluted Workbench BFF modules. It owns the store, service functions, agent loop, and view assembly.
3. **Conversation agent loop:** Converts user turns into either assistant messages or tool actions. It reads recent transcript events and summaries, produces strict structured output through Bailian, and appends only UI-safe events.
4. **Runtime service:** In-process boundary around stable SeekTalent runtime capabilities. It exposes extraction, run start, status, results, and runtime-event translation without letting runtime depend on Workbench v2.

Dependency direction:

```text
UI -> thin v2 route shim -> clean v2 backend package -> v2 store
                                                  -> conversation agent loop -> runtime service -> stable runtime
                                                                             -> memory tools
```

`src/seektalent` must not import Workbench v2, conversation-agent UI code, or old UI projection code. The new implementation must not add business logic to old BFF files such as `agent_workbench_transcript.py`, old first-turn/outbox projection modules, or the abandoned v2 attempt from the dirty main checkout.

## 6. Backend Layout and Cleanup Strategy

Create a new clean backend package for v2, for example:

```text
src/seektalent_workbench_v2/
  __init__.py
  agent_loop.py
  models.py
  runtime_service.py
  service.py
  store.py
  views.py
```

The existing `src/seektalent_ui` package should contain only a small v2 route shim and app mounting code. That shim may import `seektalent_workbench_v2`, but `seektalent_workbench_v2` must not import old Workbench BFF modules.

Replacement phases:

1. Build v2 in the new package while leaving old Workbench routes available.
2. Point the React v2 UI to the new route shim.
3. Once manual and automated acceptance pass, delete the old Workbench conversation/BFF projection path and the abandoned dirty-main v2 package.
4. Keep stable runtime modules intact except for narrowly required service-boundary imports.

## 7. Database Model

Use one SQLite database for v2 Workbench state.

### conversations

| Column | Meaning |
|---|---|
| `id` | Conversation id, prefixed with `agentv2_`. |
| `title` | Sidebar title, derived from first useful user/JD text and later updateable. |
| `created_at` | Creation timestamp. |
| `updated_at` | Last transcript event timestamp. |
| `runtime_run_id` | Current runtime run id, nullable until requirements are confirmed. |
| `runtime_state` | `idle`, `queued`, `running`, `completed`, `failed`, or `cancelled`. |
| `context_summary` | Latest compact summary used as short-term memory. |

### transcript_events

| Column | Meaning |
|---|---|
| `id` | Event id. |
| `conversation_id` | Parent conversation id. |
| `step` | Monotonic integer per conversation. UI renders by this. |
| `type` | Event type enum. |
| `role` | `user`, `assistant`, `system`, or `runtime` when useful for rendering. |
| `payload_json` | Event-specific JSON. |
| `created_at` | Event timestamp. |
| `status` | `pending`, `running`, `completed`, or `failed` for non-message events. |
| `parent_event_id` | Optional event relationship for updates. |
| `dedupe_key` | Optional idempotency key for retries. |

Allowed event types:

```text
user_message
assistant_message
assistant_status
requirement_form
requirement_form_confirmed
runtime_progress
runtime_result
error
context_summary
```

The UI never renders runtime audit records, raw tool calls, internal operation logs, or old transcript groups.

## 8. Agent Contract

Every agent turn returns a strict Bailian JSON object. Free-form model text is not accepted at the BFF boundary.

Top-level schema. Optional objects are explicit `null` values, not omitted keys, so Bailian strict structured output can validate every turn with the same schema. The example below shows all object shapes; a pure chat turn sets those object fields to `null`.

```json
{
  "intent": "chat|extract_requirements|update_requirements|confirm_requirements|start_runtime|get_runtime_status|get_runtime_results|read_memory|write_memory",
  "message": "User-facing assistant text.",
  "needsClarification": false,
  "clarifyingQuestion": null,
  "runtimeInput": {
    "jobTitle": "string",
    "jd": "string",
    "notes": "string"
  },
  "requirementPatch": null,
  "memoryRead": null,
  "memoryWrite": {
    "source": "string",
    "content": "string"
  }
}
```

For pure chat, `runtimeInput`, `requirementPatch`, `memoryRead`, and `memoryWrite` are `null`. `runtimeInput.jobTitle` and `runtimeInput.jd` are required before `start_runtime`. `runtimeInput.notes` is optional.

Turn handling is two-stage:

1. **Intent classification:** Decide whether the user is chatting, providing a new JD/recruitment need, supplementing requirements, confirming requirements, asking progress, asking results, or making a memory-related request.
2. **Intent-specific action:** Only JD/recruitment and supplementary-requirement turns enter requirement normalization. Pure chat answers directly. Progress questions call status tools. Result questions call result tools. Confirmation actions validate the current requirement form and then start runtime only when required fields are present.

Arbitrary user text is the normal input path for JD/recruitment and supplementary-requirement intents. The agent must normalize the user's raw text into:

1. `jobTitle`: the most likely position name.
2. `jd`: the full useful job description and hiring requirements.
3. `notes`: optional extra constraints, preferences, sourcing notes, or recruiter comments.

If the model can infer `jobTitle` and `jd` from one pasted block after classifying it as a recruitment input, it should do so without asking the user to reformat the input. If either required field is missing or ambiguous after normalization, the agent sets `needsClarification: true`, returns a focused `clarifyingQuestion`, appends an assistant message, and does not start runtime. The backend must enforce the same rule before executing `start_runtime`; this is not only a prompt convention.

The system prompt must state:

1. The agent is a general SeekTalent workbench assistant, not only a JD parser.
2. Every turn must first be classified; do not assume arbitrary text is a JD.
3. Pure chat should answer directly and should not call runtime tools.
4. Progress questions should read runtime status instead of modifying requirements.
5. Result/detail questions should read runtime results instead of modifying requirements.
6. Supplementary requirement text should update the current requirement form rather than create a new unrelated run.
7. Text classified as JD/recruitment input should be normalized into `jobTitle`, `jd`, and optional `notes` before asking the user to reformat anything.
8. Runtime cannot start without `jobTitle` and `jd`.
9. When required fields are missing or ambiguous, ask one focused clarification question instead of guessing.
10. Requirement changes must be reflected by appending new transcript events; only status fields on pending events may be updated in place.
11. Long-term memory writes require explicit source text and must not silently rewrite recruitment requirements.
12. User-facing messages should be concise and never expose raw provider errors or internal stack traces.

## 9. Runtime Service

The v2 runtime service is an in-process Python boundary with these functions:

```text
extract_requirements(text) -> requirement form payload
start_run(jobTitle, jd, notes) -> runtime_run_id
get_status(runtime_run_id) -> user-readable status payload
get_results(runtime_run_id) -> user-readable result payload
stream_events(runtime_run_id, after_cursor) -> runtime progress payloads
```

This boundary adapts existing stable runtime behavior. It does not introduce a second runtime state machine. Runtime events are translated into `runtime_progress` and `runtime_result` transcript events.

## 10. UI Contract

The React Workbench v2 screen renders only `transcript_events` ordered by `step`.

Rendering rules:

1. `user_message`: a normal user transcript block with full scrollable text for long JD content.
2. `assistant_message`: normal assistant text.
3. `assistant_status`: compact assistant activity row; no duplicate "正在处理需求 / 正在思考" rows for one turn.
4. `requirement_form`: embedded transcript form with editable checkboxes and notes.
5. `requirement_form_confirmed`: readonly transcript confirmation block.
6. `runtime_progress`: compact progress update, expandable for details.
7. `runtime_result`: candidate/result summary and links into the right-side surface.
8. `error`: user-safe error message.
9. `context_summary`: hidden by default or collapsed developer-visible event.

The left sidebar lists conversations from the v2 conversation table. The composer remains fixed at the bottom. The transcript scroll container owns overflow, so long JD content and long forms do not create bottom white gaps or unscrollable clipped regions. The right workflow/candidate surface appears with CSS transition when runtime state moves out of `idle`.

## 11. Memory

Short-term memory is built from the latest `context_summary` plus recent transcript events. When the transcript exceeds the configured window, the agent appends a new `context_summary` event and updates the conversation row. Historical transcript events are not deleted.

Long-term memory reuses the existing `seektalent_agent_memory` store through explicit `read_memory` and `write_memory` tools. The agent may write durable memory only when the source is explicit in the current conversation.

## 12. Error Handling

1. Schema validation failure: allow one bounded strict-output retry, then append an `error` event.
2. Bailian provider failure: append a user-safe `error` event and keep the user message in transcript.
3. Missing `jobTitle` or `jd` at runtime start: append a focused assistant clarification event and do not call runtime.
4. Runtime start failure: append `error`, keep requirements editable, and do not mark runtime as running.
5. Runtime status failure: append or return a user-safe progress error without exposing stack traces.
6. Duplicate client retries: use `dedupe_key` where available so the same user turn does not create duplicate transcript steps.

## 13. Testing

Backend tests:

1. Creating a conversation with "你好" appends `user_message` and `assistant_message`, with no requirement form and no runtime run.
2. Creating a conversation with a JD appends full `user_message`, one `assistant_status`, and one `requirement_form`.
3. A pure chat message that contains non-JD text does not enter requirement normalization.
4. A progress question reads runtime status and does not create or edit a requirement form.
5. A supplementary requirement updates the current requirement form rather than starting a new run.
6. A single pasted JD/recruitment text block can produce runtime input with `jobTitle`, `jd`, and optional `notes` without asking the user to split fields manually.
7. A vague recruitment text block that lacks a reliable `jobTitle` or `jd` produces one focused clarification question and no runtime run.
8. Backend `start_runtime` refuses to execute if `jobTitle` or `jd` is missing, even if the model selected `start_runtime`.
9. Checkbox edits append or update a `requirement_form` event and remain reversible before confirmation.
10. Confirming requirements appends `requirement_form_confirmed`, starts runtime, and records `runtime_run_id`.
11. Asking "现在进度如何" reads runtime status and appends a user-readable assistant answer or progress event.
12. Context compaction appends `context_summary` without deleting prior transcript events.
13. Architecture guard prevents `src/seektalent` from importing Workbench v2 or conversation UI code.
14. Architecture guard prevents `src/seektalent_workbench_v2` from importing old Workbench BFF projection modules.

Frontend tests:

1. Pure chat renders as normal chat transcript.
2. Long JD renders fully inside a scrollable transcript block.
3. Requirement form is embedded in transcript, editable before confirmation, readonly after confirmation.
4. No legacy "已处理" group header appears in v2 transcript.
5. Composer stays fixed while transcript scrolls.
6. Right-side surface transition is animated when runtime starts.

Integration tests:

1. New conversation: "你好".
2. New conversation: paste JD -> form appears -> uncheck one item -> add notes -> confirm -> runtime queued/running.
3. Refresh after confirmation restores transcript, form state, and runtime state.
4. Ask progress after runtime starts and receive a user-readable status.

## 14. Acceptance Criteria

1. Manual testing uses a clean v2 route and shows a normal agent transcript.
2. The UI no longer depends on old `transcriptGroups` for v2 conversations.
3. The requirement form is a transcript event, not a separate full-screen panel.
4. Every user turn is classified before action; v2 does not assume arbitrary text is a JD.
5. Arbitrary pasted user text classified as JD/recruitment input is normalized into `jobTitle`, `jd`, and optional `notes` when possible.
6. Runtime starts only from validated `jobTitle`, `jd`, and optional `notes`; missing required fields trigger clarification instead of a run.
7. Bailian strict structured output is the only production agent output parser.
8. Old Workbench remains available until v2 passes acceptance, then old polluted BFF/projection paths can be deleted in a separate cleanup PR.
9. New formal `docs/superpowers/specs/*.md` and `docs/superpowers/plans/*.md` files are tracked by default; large artifacts remain ignored.
