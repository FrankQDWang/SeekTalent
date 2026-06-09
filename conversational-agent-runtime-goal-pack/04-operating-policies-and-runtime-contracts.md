# Operating Policies And Runtime Contracts

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `07-artifact-trace-policy.md`
- `08-risk-and-boundaries.md`
- `09-cross-goal-acceptance.md`
- `10-execution-control.md`
- `11-openai-agents-sdk-runtime.md`
- `12-ui-data-contract.md`
- `13-agent-evaluation-contract.md`
- `14-retention-and-compaction-policy.md`

---

## Source: `07-artifact-trace-policy.md`

## Artifact And Trace Policy

### Problem

The current runtime writes many artifacts through `RunTracer` and `ArtifactStore`. That is useful for development, but a conversational agent can launch and observe many runs. Production local use must not fill the user's disk with debug payloads or write sensitive material into ordinary transcript-visible state.

### Modes

#### `dev_full_local`

Use for local development and debugging.

Writes:

- run manifest;
- trace log;
- event log;
- prompt snapshots;
- LLM call artifacts;
- round retrieval plans;
- scoring artifacts;
- reflection artifacts;
- final markdown;
- debug JSON.

#### `prod_compact_local`

Use as local production default.

Writes:

- run manifest;
- runtime-control events;
- latest snapshot;
- checkpoints;
- final result summary;
- error summary;
- safe artifact refs.

Does not write:

- full prompt snapshots;
- raw provider payloads;
- raw browser state;
- raw resume text;
- per-call debug JSON unless explicitly enabled.

#### `off_except_db`

Use for privacy-sensitive operation.

Writes only:

- runtime-control DB rows;
- Workbench rows already required by product behavior;
- final safe summary.

No filesystem debug artifacts are written by default.

### Policy Inputs

Runtime control configuration should expose:

```text
SEEKTALENT_RUNTIME_OUTPUT_MODE=dev_full_local | prod_compact_local | off_except_db
SEEKTALENT_RUNTIME_TRACE_MODE=debug | normal | compact
```

Existing `runtime_mode` may choose defaults:

```text
dev  -> dev_full_local
prod -> prod_compact_local
```

### Sink Interface

Runtime control should introduce a sink abstraction with concrete implementations for the modes above.

Required operations:

```text
write_event
write_snapshot
write_checkpoint
write_artifact_ref
write_debug_artifact
open_stream
finalize
```

The runtime executor may still use `RunTracer` internally during migration, but production defaults must route through the policy so debug writes can be suppressed or compacted.

### Agents SDK Trace Policy

OpenAI Agents SDK traces are governed by the same output mode.

Rules:

- `dev_full_local` may keep full local traces for developer debugging when explicitly enabled;
- `prod_compact_local` must disable sensitive trace payloads or store only compact safe references;
- `off_except_db` must not write local trace files for ordinary agent turns;
- agent tool inputs and outputs that include JD text, requirement drafts, candidate ids, or summaries must be redacted or referenced according to the active mode;
- raw provider payloads, cookies, auth headers, browser storage, and raw resume text outside Workbench visibility rules must never be written into Agents SDK traces.

### Acceptance

Tests must prove:

- dev mode writes expected debug artifacts;
- prod compact mode does not write prompt snapshots or raw provider payloads;
- off mode writes no run artifact directory for ordinary progress;
- Agents SDK trace output follows the selected artifact/trace mode;
- final result and error summary remain available in every mode;
- existing artifact tests still pass for dev mode.

---

## Source: `08-risk-and-boundaries.md`

## Risk And Boundaries

### Red-Zone Files

Changes to these files require focused tests and the red-zone gate:

```text
src/seektalent/runtime/orchestrator.py
src/seektalent/runtime/source_lanes.py
src/seektalent/tracing.py
src/seektalent/artifacts/store.py
src/seektalent_ui/workbench_store.py
src/seektalent_ui/runtime_bridge.py
src/seektalent_ui/workbench_routes.py
src/seektalent_ui/models.py
src/seektalent_ui/server.py
apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
```

### Primary Risks

| Risk | Impact | Required mitigation |
| --- | --- | --- |
| Agent bypasses runtime control plane | Runtime refactors break transcript behavior | Add import boundary tests preventing agent imports from `seektalent.runtime`. |
| Pause is UI-only | User believes work stopped while runtime continues | Command state must be persisted and applied at safe boundaries with events. |
| Checkpoint is too shallow | Resume reruns or loses user changes | Persist `RunState`, source plan, pending commands, stage, round, schema version. |
| Requirement edits are frontend-only | Confirmed runtime input diverges from transcript | Persist draft revisions and selected item ids. |
| Production artifacts are too large | User disk fills and privacy boundary weakens | Add output modes and tests proving compact/off behavior. |
| Workbench and runtime-control events diverge | UI and transcript disagree | Reconcile event ids and store workbench session mapping. |
| Agent hallucinates progress | User loses trust | Transcript progress must cite event ids or snapshot cursors. |
| Product runtime accidentally depends on Codex CLI | Packaging and installation become fragile | Use OpenAI Agents SDK as package dependency and add tests forbidding `codex` process/runtime dependencies. |

### Stop Conditions

Stop and ask before product edits if:

- a required API can only be implemented by direct agent imports of `WorkflowRuntime`;
- runtime command application requires killing arbitrary provider/LLM/browser calls;
- Workbench session creation and runtime-control run creation cannot be made idempotent;
- source catalog is mid-refactor and has no stable read surface;
- artifact policy would require weakening existing artifact path safety;
- generated frontend static assets are dirty and must be overwritten.
- OpenAI Agents SDK cannot be packaged as a normal SeekTalent dependency without introducing Codex CLI or Codex App Server.

### Review Mode

Plan review should use selective expansion with hard completeness:

- full local runtime control and agent transcript are in scope;
- SaaS, cloud control plane, generic workflow engine, and arbitrary stack-frame restoration are out of scope;
- any proposed expansion must be written as an explicit opt-in decision.

---

## Source: `09-cross-goal-acceptance.md`

## Cross-Goal Acceptance

These criteria are checked after Goal 2 completes.

### Product Acceptance

1. A backend caller or future UI can submit a JD and receive structured requirement sections.
2. Requirement sections include checkbox selection state with every extracted item selected by default.
3. A backend caller or future UI can unselect, edit, delete, and move supported requirement items through real APIs.
4. A backend caller or future UI can add free-form extra requirements and receive runtime-normalized draft additions before confirmation.
5. A backend caller or future UI can resolve review-required requirement items before confirmation.
6. Confirming sends the approved requirement revision to runtime control.
7. Workflow starts only after confirmation.
8. Transcript progress is based on persisted runtime-control events.
9. A backend caller or future UI can request pause and receives command accepted/pending/applied state.
10. A backend caller or future UI can request cancel and receives command accepted/pending/applied state.
11. A backend caller or future UI can resume a paused run.
12. A backend caller or future UI can add a next-round requirement and receive the target round before it becomes active.
13. A backend caller or future UI can resolve review-required next-round requirements before they are scheduled.
14. Transcript-ready data records when a next-round requirement is activated for that target round.
15. A backend caller or future UI can ask for runtime details and receives answers grounded in detail read models.
16. Final summary is grounded in final runtime result and user instruction.

### Technical Acceptance

1. Agent code does not import `seektalent.runtime`.
2. Runtime-control code has one approved executor adapter boundary into `WorkflowRuntime`.
3. Runtime-control SQLite migrations initialize from empty DB and reject future versions.
4. Runtime commands are idempotent by `runtime_run_id + idempotency_key`.
5. Free-form requirement amendments are idempotent and produce versioned draft revisions.
6. Running next-round amendments never mutate a round after `runtime_round_input_locked`.
7. Runtime events are ordered by `(runtime_run_id, event_seq)`.
8. Runtime snapshots are available after requirement extraction, after each safe boundary, and after finalization.
9. Artifact modes are tested.
10. Source selection comes from catalog/registry, not fixed CTS/Liepin universe.
11. Workbench session id and runtime run id are linked.
12. Runtime-control and Workbench event links are persisted so transcript, graph, and Workbench event streams cannot diverge silently.
13. Agent APIs under `/api/agent` use the same host/origin/auth/CSRF posture as Workbench write APIs.
14. Conversation transcript state is stored in `src/seektalent_conversation_agent/` persistence, not only in frontend state.
15. UI-ready DTOs and API schemas are current after API changes.
16. Goal 2 does not ship temporary Svelte transcript UI or memory UI before designer-provided screens are available.
17. SeekTalent product runtime does not require Codex CLI, Codex App Server, Codex MCP server, Codex SDK, `openai-codex`, or `@openai/codex-sdk`.
18. OpenAI Agents SDK usage is isolated behind `src/seektalent_conversation_agent/` and route handlers do not import the SDK directly.
19. Runtime event gap detection prevents transcript cursor advancement across missing events.
20. Agent tool-routing and grounding evals pass.

### Required Final Verification

Run and record:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
uv run --group dev python -m pytest tests -q
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
scripts/verify-dev-workbench.sh
scripts/verify-red-zone.sh
git diff --check
```

If full `pytest tests -q` is not practical during a checkpoint, the goal ledger must record the focused subset used and the final run must include full backend verification.

---

## Source: `10-execution-control.md`

## Execution Control

This file controls how Codex Goal workers execute this pack. It is run protocol, not product design.

### Goal Invocation

Run Goal 1 with:

```text
Complete Goal 1 only: the Runtime Control Plane described in conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/SPEC.md and conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/PLAN.md.

Before editing product code, read the full conversational-agent-runtime-goal-pack shared documents, read Goal 1 SPEC and PLAN in full, inspect the current runtime/source/Workbench code facts, run and record Goal 1 preflight, create or update conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md, and write the implementation plan with explicit evidence for any contract/code mismatch.

Run the repository-required plan review gate before product implementation. If plan review cannot be run or raises a blocking issue, stop and report the blocker instead of editing product code.

Implement the full agreed local Goal 1 scope, not a scaffold. Do not start Goal 2 or the memory extension. Finish only when every Goal 1 acceptance criterion passes, exact verification output is recorded, and the Goal 1 completion phrase from MANIFEST.md is included in the final packet.
```

Run Goal 2 only after Goal 1 is complete:

```text
Complete Goal 2 only: the Conversational Agent Transcript backend and UI-ready data contract described in conversational-agent-runtime-goal-pack/goal-2-conversational-agent/SPEC.md and conversational-agent-runtime-goal-pack/goal-2-conversational-agent/PLAN.md.

Before editing product code, read the full conversational-agent-runtime-goal-pack shared documents, verify Goal 1 completion evidence and runtime-control APIs, read Goal 2 SPEC and PLAN in full, run and record Goal 2 preflight, create or update conversational-agent-runtime-goal-pack/goal-2-conversational-agent/progress.md, and write the implementation plan with explicit evidence for any contract/code mismatch.

Run the repository-required plan review gate before product implementation. If plan review cannot be run, Goal 1 evidence is incomplete, or Goal 2 needs a runtime-control tool that Goal 1 did not implement, stop and report the blocker instead of editing product code.

Implement the full agreed local transcript-agent backend/API/view-model scope, not a scaffold. Do not build temporary Svelte UI. Do not start the memory extension. Finish only when every Goal 2 acceptance criterion and the cross-goal acceptance criteria pass, exact verification output is recorded, and the Goal 2 and cross-goal completion phrases from MANIFEST.md are included in the final packet.
```

Run the Goal 2 memory extension only after Goal 2 is complete and explicitly invoked:

```text
Complete only the Goal 2 Agent Memory Extension described in conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/SPEC.md and conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/PLAN.md.

Before editing product code, read the full conversational-agent-runtime-goal-pack shared documents, verify Goal 2 completion evidence and conversation-agent APIs, read the memory extension SPEC and PLAN in full, run and record memory-extension preflight, create or update conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/progress.md, and write the implementation plan with explicit evidence for any contract/code mismatch.

Run the repository-required plan review gate before product implementation. If plan review cannot be run, Goal 2 evidence is incomplete, or memory would become canonical requirement/runtime/candidate state, stop and report the blocker instead of editing product code.

Implement the full agreed local advisory-memory backend/API/DTO scope, not a scaffold. Do not build memory-management UI. Finish only when every memory-extension acceptance criterion passes, exact verification output is recorded, and the memory-extension completion phrase from MANIFEST.md is included in the final packet.
```

### Shared Preflight

Every goal worker must run and record:

```bash
pwd
git branch --show-current
git rev-parse HEAD
git rev-parse --verify origin/main || echo "MISSING origin/main; fetch before final verification"
git merge-base HEAD origin/main || echo "MISSING merge-base with origin/main"
git status --short --untracked-files=all
git stash list
test -d conversational-agent-runtime-goal-pack && echo "pack present" || echo "MISSING pack"
test -f conversational-agent-runtime-goal-pack/MANIFEST.md && echo "manifest present" || echo "MISSING manifest"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "cross acceptance present" || echo "MISSING cross acceptance"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "agents sdk runtime present" || echo "MISSING agents sdk runtime"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "ui data contract present" || echo "MISSING ui data contract"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "agent eval contract present" || echo "MISSING agent eval contract"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "retention contract present" || echo "MISSING retention contract"
test -d conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension && echo "memory extension present" || echo "MISSING memory extension"
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
```

The current repository may contain generated Workbench static assets. If dirty state includes unrelated generated assets, leave them untouched unless the selected goal explicitly regenerates the frontend and the final diff is expected to include built assets.

### Progress Ledgers

Goal 1 ledger:

```text
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md
```

Goal 2 ledger:

```text
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/progress.md
```

Memory extension ledger:

```text
conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/progress.md
```

Each ledger must include:

```markdown
# [Goal Name] Progress

## Run Identity

- Goal pack:
- Goal:
- Started at:
- Branch:
- HEAD at start:
- Origin main at start:
- Merge-base with origin/main:
- Worktree path:
- Dirty state at start:
- Stashes observed:

## Current Phase

- Phase:
- Status: not-started | in-progress | blocked | complete
- Latest successful command:
- Latest failed command:
- Current blocker:

## Phase Evidence

| Phase | Status | Files changed | Tests/checks | Evidence |
| --- | --- | --- | --- | --- |

## Red-Green Evidence

| Check | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
```

### Resume Protocol

After pause, crash, context compaction, or thread switch:

1. read `00-codex-goal.md`;
2. read this file;
3. read the selected goal's `PLAN.md`;
4. read that goal's progress ledger;
5. inspect `git status --short --untracked-files=all`;
6. re-run only the latest failed command or the smallest relevant verification command;
7. continue from the current ledger phase.

Do not restart from phase 1 if the ledger shows completed phases with evidence.

### Architecture Escalation

Stop and ask before continuing when one of these conflicts appears:

- Goal 2 needs a tool that Goal 1 did not implement.
- Runtime safe-boundary command semantics require arbitrary stack-frame interruption.
- The implementation can pass only by weakening source-boundary, Tach, privacy, red-zone, or frontend checks.
- Agent transcript would need direct `WorkflowRuntime` imports.
- Requirement selection state would exist only in frontend state.
- Artifact/trace production policy would still write full debug payloads by default.
- Workbench and runtime-control session identities cannot be reconciled.
- Goal 2 cannot use OpenAI Agents SDK as a normal packaged dependency without introducing Codex CLI, Codex App Server, or Codex SDK.
- Goal 2 would require building temporary Svelte transcript UI before designer-provided screens are available.
- The memory extension would need to store candidate PII, raw resume text, provider payloads, or secrets.
- The memory extension would change requirements without user confirmation.

Escalation format:

```markdown
## Architecture Escalation

- Constraint conflict:
- Evidence:
- Option A:
- Option B:
- Recommendation:
- Risk if wrong:
```

### No Scaffold Rule

The product code and this pack's implementation updates must not contain:

- unresolved planning-marker comments;
- data values used only to make tests or screens appear complete;
- empty adapters;
- test-only fake product paths;
- storage columns written but never read;
- UI controls that do not call real APIs;
- APIs that return hard-coded runtime progress.

Every final goal packet must include a no-scaffold verification command over the touched product files. Commands that append `|| true` are allowed only for context gathering and must not be reported as acceptance gates or validation evidence.

If a no-scaffold term is intentionally present in a test name, fixture, or documentation string, the goal worker must record why it is safe and show that the product path still implements real behavior.

---

## Source: `11-openai-agents-sdk-runtime.md`

## OpenAI Agents SDK Runtime

### Runtime Decision

The SeekTalent conversational agent runtime uses OpenAI Agents SDK as an application dependency inside `src/seektalent_conversation_agent/`.

Product code must not depend on Codex CLI, Codex App Server, Codex MCP server, Codex SDK, or an operator-installed `codex` binary. Codex Goal mode may be used by developers to execute this goal pack, but it is not a product runtime dependency.

Forbidden product runtime dependencies:

```text
codex
codex app-server
codex mcp-server
openai-codex
@openai/codex-sdk
Codex App Server JSON-RPC client
Codex MCP server client
```

Allowed product runtime dependency:

```text
openai-agents
```

The exact dependency declaration belongs to implementation, but Goal 2 must make the agent framework an ordinary package dependency that is installed with SeekTalent.

### Ownership

OpenAI Agents SDK owns:

- agent turn orchestration;
- model calls used for conversational intent and wording;
- tool invocation routing;
- streaming assistant output when supported by the local API layer;
- trace spans for agent runs when tracing is enabled;
- optional SDK session mechanics only when they mirror the canonical conversation store.

SeekTalent owns:

- conversation ids and transcript persistence;
- user authentication and API authorization;
- requirement draft and approved requirement truth;
- runtime run truth;
- command truth;
- event and snapshot truth;
- Workbench session links;
- privacy filtering and redaction;
- retry, idempotency, and recovery semantics for product state.

### Agent Shape

Goal 2 should define a small agent runtime module under:

```text
src/seektalent_conversation_agent/
```

The package should contain:

```text
ConversationAgentService
ConversationStore
AgentRuntime
AgentToolAdapter
AgentRunContext
```

`AgentRuntime` is the only module that constructs OpenAI Agents SDK `Agent`, `Runner`, tool definitions, run config, and SDK-specific tracing/session configuration.

Route handlers call `ConversationAgentService`. They must not import OpenAI Agents SDK directly.

### Tool Mapping

Runtime-control operations are exposed to OpenAI Agents SDK as function tools. The SDK tool layer is a thin adapter over `src/seektalent_runtime_control/` public APIs.

Tool functions must validate typed inputs before calling runtime-control. They must return typed, compact, model-safe outputs that include stable ids and reason codes.

The SDK tool layer must not:

- parse JD requirements itself;
- map free-form requirements to backend fields itself;
- read runtime-control SQLite directly;
- call `WorkflowRuntime`;
- mutate Workbench state directly;
- return frontend-only state as if it were product truth.

### State And Sessions

The canonical conversation state is `ConversationStore` as defined in `goal-2-conversational-agent/SPEC.md`.

OpenAI Agents SDK session features may be used only as a convenience for model input history. If used, they must be reconstructible from `ConversationStore` and must not contain the only copy of:

- transcript messages;
- tool calls;
- runtime ids;
- requirement draft revisions;
- approved requirement revisions;
- latest rendered event cursor;
- pending user actions;
- final summaries.

Codex memory, OpenAI Agents SDK memory/session history, and model traces are not canonical product state.

The post-Goal-2 memory extension may provide SeekTalent-owned advisory memory context through `ConversationAgentService`. That memory is also not canonical product state and must not bypass runtime-control requirement confirmation.

### Human-In-The-Loop

Human confirmation is represented in SeekTalent state, not only in an SDK interruption object.

Requirement confirmation, `needs_review` resolution, pause/resume/cancel, and next-round requirement amendments must persist through the runtime-control and conversation stores before the agent reports success.

If an Agents SDK run is interrupted, restarted, or retried, the agent service must rebuild product-visible state from the stores and idempotency keys. It must not infer successful user approval from an SDK-local object that was not persisted.

### Streaming And Transcript

Streaming output may be used for assistant wording, but runtime progress messages must be grounded in runtime-control events and snapshots.

The transcript may show:

- assistant reasoning summary text produced by the agent;
- tool call started/completed states;
- runtime progress events;
- command pending/applied/rejected states;
- review-required requirement items.

The transcript must not show invented progress while waiting for runtime-control events.

### Tracing

Agents SDK tracing is optional for development and production observability. It must follow `04-operating-policies-and-runtime-contracts.md`.

Trace payloads must not include raw provider cookies, auth headers, browser storage, raw resume text outside existing Workbench visibility rules, or Codex auth state.

Production defaults must either disable sensitive trace payloads or store only compact safe references.

### Implementation Boundary

Goal 2 implementation must add tests proving:

- no product code invokes `codex`, `codex app-server`, or `codex mcp-server`;
- no product dependency on `openai-codex` or `@openai/codex-sdk` exists;
- route handlers do not import OpenAI Agents SDK directly;
- `AgentRuntime` tools call only runtime-control public APIs;
- canonical transcript state can be reloaded without SDK session state;
- tool calls preserve runtime-control reason codes.

---

## Source: `12-ui-data-contract.md`

## UI Data Contract

### Current UI Scope

The Svelte transcript UI and memory-management UI are deferred until designer-provided screens are available.

Goal 2 and the memory extension must not implement temporary UI screens, display-only components, or controls that exist only to satisfy tests. They must prepare complete backend data for the future UI through typed API responses and stable view models.

### Required UI-Ready Surfaces

Goal 2 must expose UI-ready data for:

- conversation creation and reload;
- user and assistant transcript messages;
- tool call started, completed, failed, and idempotent replay state;
- requirement review sections;
- item-level selected, unselected, edited, moved, deleted, enabled, disabled, and review-required states;
- stale revision conflicts with latest backend state;
- workflow queued, starting, running, paused, cancelled, failed, and completed states;
- command accepted, pending-safe-boundary, applied, rejected, and superseded states;
- next-round requirement pending, review-required, scheduled, activated, superseded, and rejected states;
- runtime detail answers grounded in event, checkpoint, Workbench-visible, or artifact-ref sources;
- final summary state;
- recoverable transcript sync errors such as `runtime_event_gap_detected`.

### View Model Rules

Every route response consumed by the future UI must include:

```text
stable id
revision id or cursor when applicable
user-facing Chinese display text
machine-readable status
machine-readable reason code when applicable
allowed actions for the current state
source tool call id or runtime event cursor when the data is grounded in tool/runtime state
```

The future UI must be able to render from server state after a full browser reload. No required UI state may exist only in frontend component state, OpenAI Agents SDK session state, or model traces.

### Requirement Review View Model

The requirement review response must include:

```json
{
  "conversationId": "agent_conv_...",
  "draftRevisionId": "reqdraft_...",
  "status": "draft_ready",
  "canConfirm": true,
  "unresolvedReviewItemCount": 0,
  "sections": [
    {
      "sectionId": "must_have_capabilities",
      "displayName": "必须满足",
      "backendField": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_...",
          "text": "Python 后端 API 开发",
          "selected": true,
          "enabled": true,
          "status": "resolved",
          "source": "extracted",
          "allowedActions": ["select", "edit", "delete", "move_to_preferred_capabilities"],
          "review": null
        }
      ]
    }
  ],
  "reasonCode": null
}
```

All extracted items are selected by default. The selected state, enabled state, deletion state, movement state, and review state are persisted by runtime control and reloaded through the API.

### Transcript Message View Model

Transcript messages must include enough metadata for the future UI to render without reinterpreting agent text:

```json
{
  "messageId": "msg_...",
  "messageSeq": 42,
  "role": "assistant",
  "messageType": "runtime_progress",
  "text": "第 2 轮正在评分新增候选人。",
  "payload": {},
  "sourceToolCallId": "toolcall_...",
  "sourceRuntimeRunId": "runtime_run_...",
  "sourceRuntimeEventSeq": 128,
  "createdAt": "2026-06-08T00:00:00Z"
}
```

Progress, command, detail, and final-summary messages must carry source ids or cursors internally. The agent may phrase text, but it must not create product facts that cannot be traced to runtime-control, Workbench-visible data, or approved artifact refs.

### Memory Management View Models

The memory extension must expose UI-ready API data, but it does not implement memory UI in this goal.

Required memory API data:

- settings state for memory, recall, generation, review requirement, retention, and summary budget;
- candidate list with safe text only, category, status, reason code, source conversation id, and safe evidence excerpt;
- fact list with safe text only, category, status, confidence, source metadata, expires_at, and deleted_at;
- allowed actions for each candidate or fact;
- clear-scope result with affected counts and summary invalidation state.

Memory API responses must never include raw candidate PII, raw resume text, provider payloads, secrets, cookies, auth headers, browser storage, or full JD text.

### Future UI Gate

When designer screens are available, the UI implementation should consume these DTOs directly. If the design requires data not covered here, update this contract and the backend tests before building the UI.

---

## Source: `13-agent-evaluation-contract.md`

## Agent Evaluation Contract

### Purpose

Goal 2 uses OpenAI Agents SDK for conversational orchestration, intent handling, wording, and tool routing. Unit tests alone are not enough for that surface. The agent must also pass focused regression evaluations for conversation behavior and tool-use decisions.

The memory extension uses LLM-assisted extraction and privacy review when configured. It must pass focused memory evaluations before completion.

### Evaluation Ownership

Evaluations are product verification, not product runtime state. They may use recorded fixtures, contract test doubles for runtime-control interfaces, and deterministic model outputs where needed for stable CI. Test doubles must stay inside eval/test code and must not become product fallback paths. Live model evaluations may be run manually when credentials are available, but the required completion gate is the deterministic eval suite.

### Goal 2 Required Eval Cases

The Goal 2 eval suite must cover:

- JD submission calls `extract_requirements`;
- direct checkbox/edit/delete/move operations call `update_requirement_draft`;
- free-form draft additions call `amend_requirement_draft_from_text`;
- ambiguous additions route through `resolve_requirement_review`;
- confirmation calls `confirm_requirements` and only then `start_workflow`;
- user pause intent calls `request_pause`;
- user cancel/end intent calls `request_cancel`;
- resume intent calls `resume_workflow` only from paused state;
- active-run free-form requirement addition calls `submit_next_round_requirement`;
- current-status questions call snapshot/event tools;
- detail questions call `get_runtime_detail`;
- final summary requests call `prepare_final_summary` only after terminal runtime state;
- stale draft responses are not silently merged;
- `runtime_event_gap_detected` does not advance the rendered cursor;
- agent wording never claims next-round requirements are active before `runtime_requirement_revision_activated`;
- no response invents candidate facts, source counts, runtime stages, or command state.

### Memory Extension Required Eval Cases

The memory eval suite must cover:

- extraction accepts stable recruiter preferences in allowed categories;
- extraction rejects candidate PII;
- extraction rejects raw resume text;
- extraction rejects provider payloads;
- extraction rejects secrets and auth material;
- extraction rejects full JD text and one-off hiring requests;
- redaction keeps only useful safe memory;
- rejected rows never persist raw forbidden text;
- recall returns only owner/workspace matching facts;
- hostile memory text cannot bypass requirement confirmation;
- memory suggestions are presented as suggestions and route through normal requirement amendment APIs if accepted.

### Required Commands

Goal 2 focused verification must include:

```bash
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
```

The memory extension focused verification must include:

```bash
uv run --group dev python -m pytest tests/evals/test_agent_memory_extraction_eval.py tests/evals/test_agent_memory_privacy_eval.py tests/evals/test_agent_memory_prompt_injection_eval.py -q
```

If eval file names change during implementation, the progress ledger must map replacement evals to the required cases above.

---

## Source: `14-retention-and-compaction-policy.md`

## Retention And Compaction Policy

### Purpose

Production local use must not grow unbounded SQLite databases or filesystem artifacts. Runtime-control, conversation-agent, and memory stores need explicit retention behavior in addition to artifact/trace output modes.

### Protected State

Retention cleanup must never delete or compact:

- active, starting, running, pause-requested, paused, resume-requested, or cancellation-requested runs;
- pending commands;
- pending or review-required requirement amendments;
- the latest snapshot for a non-terminal run;
- the latest approved requirement revision for a non-terminal run;
- conversation messages needed to resolve a pending user action;
- memory facts that are active and not expired;
- audit rows still inside their configured retention window.

### Runtime-Control Retention

Runtime-control must support configurable retention values through settings:

```text
runtime_terminal_retention_days
runtime_checkpoint_retention_days
runtime_event_payload_retention_days
runtime_final_summary_retention_days
```

Rules:

- terminal runs remain fully queryable until `runtime_terminal_retention_days` expires;
- after event payload retention expires, large `payload_json` values may be compacted only if `summary`, `event_type`, `event_seq`, `stage`, `round_no`, status, timestamps, and source references remain;
- at least the terminal snapshot, final summary record, approved requirement revision, command audit rows, and safe artifact refs must remain while the run summary is retained;
- checkpoint pruning must keep the latest checkpoint for the terminal state and every checkpoint still needed for a paused/resumable run;
- cleanup must run in bounded batches and record how many rows were compacted or deleted.

### Conversation-Agent Retention

Conversation storage must support:

```text
conversation_terminal_retention_days
conversation_tool_payload_retention_days
conversation_error_retention_days
```

Rules:

- active conversations are not pruned;
- terminal conversations keep enough transcript messages to reload final result, requirement review history, command history, and final summary until retention expires;
- tool call payloads containing JD text or detail answers may be compacted into safe references after configured retention, but source tool call ids, statuses, reason codes, and runtime cursors must remain;
- cleanup must not advance or corrupt `latest_rendered_runtime_event_seq`.

### Memory Retention

Memory retention follows the memory extension settings:

```text
retention_days
rejected_retention_days
source_excerpt_retention_days
```

Rules:

- expired facts are excluded from recall before deletion;
- rejected candidates are kept only with safe text, safe excerpt, hash metadata, reason code, and privacy review metadata;
- raw forbidden content is never retained for audit;
- summary cache rows are invalidated when accepted facts are edited, deleted, expired, or cleared;
- cleanup must be scoped by owner user id and workspace id.

### Verification

Goal 1 must test runtime-control event/checkpoint/final-summary retention.

Goal 2 must test conversation transcript and tool-call payload retention without cursor corruption.

The memory extension must test fact expiry, rejected candidate cleanup, clear-scope cleanup, and summary invalidation.
