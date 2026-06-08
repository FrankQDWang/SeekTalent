# Goal 1 Runtime Control Plane Spec

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `goal-1-runtime-control-plane/00-goal.md`
- `goal-1-runtime-control-plane/01-current-system-facts.md`
- `goal-1-runtime-control-plane/02-target-contract.md`
- `goal-1-runtime-control-plane/03-data-storage-and-migrations.md`
- `goal-1-runtime-control-plane/04-runtime-integration-boundaries.md`

---

## Source: `goal-1-runtime-control-plane/00-goal.md`

## Goal 1: Runtime Control Plane

### Objective

Build the durable middle layer between the conversational agent and the current SeekTalent workflow runtime.

This goal turns `WorkflowRuntime` into an agent-callable child workflow without letting the future agent import runtime internals, mutate `RunState`, or invent progress. The control plane owns requirement draft persistence, approved requirement revisions, workflow start, runtime command lifecycle, safe-boundary pause/cancel/resume, event persistence, snapshot read models, checkpoints, artifact policy, and Workbench session mapping.

### Product Result

After this goal, a backend caller can:

1. submit a JD or hiring request for requirement extraction;
2. receive a persisted editable requirement draft;
3. update item selection, edits, deletes, moves, and keyword enablement through revisioned operations;
4. resolve review-required requirement items without agent-side field mapping;
5. confirm the revision into a validated `RequirementSheet`;
6. start the current workflow runtime from the approved revision;
7. list persisted runtime events;
8. read a current workflow snapshot;
9. request pause, cancel, resume, and next-round requirement changes;
10. observe when commands are accepted, pending, applied, rejected, or superseded;
11. read grounded details for query, source result, scoring, reflection, command, checkpoint, and final-result questions;
12. run in development, compact production, and DB-only artifact modes.

### Scope

In scope:

- new runtime-control package and service API;
- dedicated local SQLite store and migrations;
- requirement draft and revision conversion to `RequirementSheet`;
- workflow executor adapter that is the only approved boundary into `WorkflowRuntime`;
- persistent event and snapshot model;
- checkpoint persistence at declared safe boundaries;
- durable command records and idempotency;
- deterministic command conflict and supersession rules;
- review-required requirement resolution;
- safe-boundary pause/cancel/resume semantics;
- Workbench bridge and session mapping;
- artifact/trace policy integration;
- import-boundary, data-model, command, checkpoint, event, artifact, and Workbench integration tests.

Out of scope:

- cloud control plane;
- SaaS multi-tenant operations;
- arbitrary stack-frame suspension;
- replacing the whole workflow runtime;
- building the transcript UI;
- training or changing ranking models;
- adding new provider-specific scraping features;
- making CTS and Liepin the hard-coded source universe.

### Dependency On Current Work

This goal assumes the OpenCLI and Liepin extraction already underway will finish first, because Goal 1 depends on source registry/catalog boundaries and a runtime that can execute without provider-specific imports leaking through the orchestrator.

If that work is incomplete when the goal starts, the worker must first run the boundary checks from this directory and record the exact failed boundary before editing code.

### Completion Statement

Goal 1 is complete only when a real backend caller can drive requirement extraction, confirmation, workflow start, runtime observation, and user commands through runtime-control APIs without direct agent access to `WorkflowRuntime`.

---

## Source: `goal-1-runtime-control-plane/01-current-system-facts.md`

## Goal 1 Current System Facts

### Runtime Facts

- `src/seektalent/runtime/orchestrator.py` defines `WorkflowRuntime`.
- `WorkflowRuntime.run(...)` accepts `approved_requirement_sheet: RequirementSheet | None`.
- `WorkflowRuntime.run_async(...)` also accepts `approved_requirement_sheet`.
- Requirement extraction is exposed through runtime methods around `extract_requirements`.
- Round execution is concentrated in `_run_rounds`.
- Runtime state is represented by `RunState` in `src/seektalent/models.py`.
- Runtime candidate checkpoint refresh logic exists inside the orchestrator through `_refresh_runtime_candidate_checkpoint`.
- The orchestrator remains a high-risk file because it is large and owns too many responsibilities.

### Model Facts

- `src/seektalent/models.py` defines `RequirementSheet`.
- `RequirementSheet` contains `must_have_capabilities`, `preferred_capabilities`, `exclusion_signals`, `hard_constraints`, and `initial_query_term_pool`.
- `QueryTermCandidate` represents query-term candidates.
- `RetrievalState` and `RunState` carry runtime progress and candidate state.
- Existing model validation should be reused when converting approved requirement drafts back into `RequirementSheet`.

### Source Boundary Facts

- `src/seektalent/source_adapters.py` exposes `build_source_enabled_runtime`.
- Existing architecture checks enforce source/provider boundaries.
- Current UI models still expose `SourceKind = Literal["cts", "liepin"]`, so Goal 1 must not deepen that fixed-source assumption.
- Runtime control should accept source ids from a catalog/registry-facing surface, not from provider modules.

### Workbench Facts

- `src/seektalent_ui/runtime_bridge.py` has `extract_requirement_review` and `run_runtime_sourcing_job`.
- `runtime_bridge.py` currently relies on runtime calls and callbacks and is not a stable agent boundary.
- `src/seektalent_ui/workbench_store.py` stores Workbench session and run state.
- `src/seektalent_ui/workbench_routes.py` exposes requirement prepare/approve/start/final-top10 routes.
- `src/seektalent_ui/event_routes.py` provides Workbench event streams.
- Goal 1 must integrate with Workbench instead of creating a second disconnected execution flow.

### Artifact Facts

- `src/seektalent/tracing.py` defines `RunTracer`.
- `src/seektalent/artifacts/store.py` defines `ArtifactStore` and `ArtifactSession`.
- Current development behavior writes many run artifacts locally.
- The new control plane must preserve development visibility while making production local output compact by default.

### Existing Verification Facts

Boundary checks known to matter for this work:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
```

Red-zone verification is required when touching runtime, Workbench, tracing, artifact, or generated frontend paths:

```bash
scripts/verify-red-zone.sh
```

### Dirty Worktree Fact

At planning time the repository had unrelated generated Workbench static assets in the working tree. Goal 1 workers must inspect current dirty state and leave unrelated generated assets untouched unless their own verified frontend regeneration intentionally replaces them.

---

## Source: `goal-1-runtime-control-plane/02-target-contract.md`

## Goal 1 Target Contract

### Public Package

Create a runtime-control package with a small public surface. Preferred location:

```text
src/seektalent_runtime_control/
```

This is the primary home for Goal 1 code. Do not put the new runtime-control service, store, command model, event model, checkpoint model, or agent-facing contracts under `src/seektalent/`.

The package should expose:

```text
RuntimeControlService
RuntimeControlStore
RuntimeWorkflowExecutor
RuntimeControlConfig
RuntimeControlError
RequirementDraft
RequirementDraftSection
RequirementDraftItem
RuntimeRunSnapshot
RuntimeControlEvent
RuntimeControlCommand
```

The implementation may split modules by responsibility, but public imports should remain small and explicit.

### Service Operations

The service must implement the shared tool contract from `../02-agent-tool-and-requirement-contracts.md`:

```text
extract_requirements
get_requirement_draft
update_requirement_draft
amend_requirement_draft_from_text
confirm_requirements
start_workflow
get_workflow_snapshot
list_workflow_events
request_pause
request_cancel
resume_workflow
submit_next_round_requirement
resolve_requirement_review
get_runtime_detail
prepare_final_summary
```

Each operation must accept an idempotency key when the operation changes state. Duplicate state-changing calls with the same idempotency key must return the already-created record when safe.

### Executor Boundary

Only the runtime-control executor adapter may import and instantiate `WorkflowRuntime`.

Allowed executor responsibilities:

- build a source-enabled runtime from catalog-backed source ids;
- call requirement extraction;
- call `run` or `run_async` with an approved `RequirementSheet`;
- receive runtime callbacks or hook events;
- write control-plane events, snapshots, checkpoints, and command application records;
- adapt artifact sink configuration into runtime/tracer construction.

Forbidden executor responsibilities:

- expose `WorkflowRuntime` to the agent package;
- expose `RunState` mutation to the agent package;
- import provider-specific modules for tool decisions;
- manufacture progress summaries without runtime events;
- swallow runtime exceptions without writing a failed event and reason code.

### Executor Start And Partial Failure

`start_workflow` must persist the runtime run before starting the executor:

1. create or reuse the run row by approved requirement revision id;
2. set status to `queued`;
3. create or verify the Workbench session link;
4. transition to `starting`;
5. acquire an active executor lease with `executor_id`, `attempt_no`, and `lease_expires_at`;
6. emit `runtime_executor_starting`;
7. start the executor;
8. require `runtime_executor_started` from the same active `executor_id` within the configured timeout;
9. transition to `running` only after the started event is persisted.

Executor writes that transition run state, write checkpoints, or complete commands must include the active `executor_id`. Runtime control must reject stale executor writes whose lease is expired, failed, released, or superseded by a newer attempt.

If the executor process or thread starts but fails before writing `runtime_executor_started`, recovery must mark the run `failed` or requeue only when no external side effect can have occurred. It must not silently mark the run `running`.

If runtime side effects may have occurred but no checkpoint exists, recovery writes `runtime_executor_start_failed` with `runtime_executor_start_timeout` or a more specific reason code and requires user-visible failure recovery. It must not automatically replay provider, LLM, or browser calls.

If a checkpoint exists, resume must restore from the latest checkpoint and emit `runtime_checkpoint_restored` before new runtime events.

The recovery scanner must first expire stale active leases and emit `runtime_executor_lease_expired`, then decide from persisted events, checkpoint state, and Workbench links whether the run can be failed, requeued, or resumed. It must record the decision with a stable reason code.

### Safe Boundaries

Pause, cancel, and resume apply only at named safe boundaries:

```text
after_requirement_extraction
before_round_controller
after_round_controller
before_source_dispatch
after_source_dispatch
after_candidate_merge
after_scoring
after_reflection
before_finalization
after_finalization
```

The runtime executor must check pending commands at every safe boundary and write one of:

```text
runtime_command_applied
runtime_command_rejected
runtime_command_pending_safe_boundary
runtime_command_superseded
```

Cancel requests should stop as soon as the current non-interruptible provider/LLM/browser call returns and the next safe boundary is reached. Pause requests should stop before starting the next boundary segment. Resume requests start a new execution process from the latest checkpoint and pending command set.

### Command Conflict Contract

Lifecycle commands are deterministic:

1. duplicate idempotency keys return the existing command result;
2. duplicate pending lifecycle commands return the existing pending command;
3. `request_cancel` supersedes pending pause/resume commands and rejects later lifecycle or amendment commands once accepted;
4. `request_pause` is valid only while the run is `running` or `resume_requested`;
5. `resume_workflow` is valid only while the run is `paused`;
6. conflicting lifecycle commands return `runtime_command_conflict` with the conflicting command id, command type, and status;
7. command accepted, pending, applied, rejected, and superseded states each emit persisted events.

Next-round requirement amendments are not superseded by later amendments for the same target round. They accumulate in creation order unless the user explicitly replaces or withdraws a specific pending amendment.

### Round Requirement Boundary

Next-round requirement amendments use a stricter boundary than pause/cancel.

Each round emits `runtime_round_input_locked` at `before_round_controller`, immediately before the controller reads the active requirement revision for that round. After this event, the round's requirement input is immutable.

`submit_next_round_requirement` must:

1. accept raw user text during running or paused execution;
2. normalize the text through Workflow Runtime requirement parsing with the active approved requirement revision as context;
3. create a new approved requirement revision when normalization is resolved;
4. assign `target_round_no` to the next round whose `runtime_round_input_locked` event has not fired;
5. apply only at `before_round_controller` for `target_round_no`;
6. emit `runtime_requirement_revision_activated` when the target round starts with that revision;
7. reject with `runtime_no_future_round_available` if the run is finalizing, completed, cancelled, failed, or max rounds make another round impossible.

If the user submits the amendment while round N is already locked, the earliest target is round N+1. If the user submits while the runtime is paused between N and N+1 before input lock, the target is N+1.

Other safe boundaries such as `after_source_dispatch`, `after_scoring`, and `after_reflection` may record command state, but they must not mutate the requirement revision used by the already-locked current round.

If normalization produces `needs_review`, the amendment must stay inactive until `resolve_requirement_review` resolves it. If the original target round locks before review resolution, the resolved amendment is retargeted to the next not-yet-locked round. If no future round exists, return `runtime_no_future_round_available`.

### Requirement Draft Contract

`extract_requirements` must:

1. call the existing runtime extraction path;
2. persist the raw extracted `RequirementSheet` JSON;
3. convert it to section/item draft form;
4. mark all items selected by default;
5. store item ids, backend fields, editable actions, sort order, and source span references when available;
6. emit `requirement_extraction_started` and `requirement_extraction_completed`;
7. expose the draft through `get_requirement_draft`.

`confirm_requirements` must:

1. include only selected, non-deleted draft items;
2. preserve edits and section moves;
3. validate with the existing `RequirementSheet` model;
4. store approved requirement JSON and selected/deselected item ids;
5. reject unresolved `needs_review` items with `requirement_review_unresolved`;
6. reject stale base revisions with `requirement_draft_stale`;
7. emit `requirement_confirmed`.

### Free-Form Requirement Amendments

Runtime control owns free-form amendment normalization because requirement parsing remains a Workflow Runtime responsibility.

`amend_requirement_draft_from_text` must:

1. accept raw user text, base draft revision id, optional target section hint, and idempotency key;
2. reject stale base revisions;
3. call the Workflow Runtime requirement extraction/normalization path with current draft context;
4. produce a structured draft patch containing additions, edits, moves, rejected fragments, and review-required items;
5. persist a requirement amendment record;
6. create a new draft revision when the amendment produces draft changes;
7. mark all added items selected by default;
8. preserve provenance so transcript replay can distinguish JD-extracted items from user-added items;
9. prevent final confirmation while review-required amendment items remain unresolved.

The agent may provide a target section hint from UI context, but runtime normalization decides the final backend field.

`resolve_requirement_review` must:

1. accept amendment id, base draft revision id, resolution operations, and idempotency key;
2. reject stale draft base revisions before writing a new draft revision;
3. validate accepted or edited candidates against the target section shape;
4. preserve rejected review fragments in audit history;
5. create a new draft or approved requirement revision as appropriate for draft-time or running amendment resolution;
6. retarget running resolved amendments if the previous target round locked before resolution.

Running review resolution must validate the pending amendment against `baseApprovedRequirementRevisionId`. If the pending amendment parent changed before resolution, return `requirement_amendment_stale` with the latest pending amendment state.

All draft-time state-changing operations must compare request `baseRevisionId` with the current latest draft revision for the conversation. Stale requests return `requirement_draft_stale` with latest draft content. The service must not branch drafts or implicitly merge concurrent edits.

### Snapshot Contract

Snapshots must be available after:

- requirement extraction completes;
- a draft revision changes;
- requirements are confirmed;
- workflow starts;
- each safe boundary;
- each command application or rejection;
- finalization;
- failure.

Snapshots must include the latest event cursor. Agent-facing readers use this cursor to poll without duplicating transcript narration.

### Detail Contract

`get_runtime_detail` must support:

```text
round_query
source_result
candidate_score
reflection
final_candidate
command
checkpoint
```

Every detail response must cite source event ids or checkpoint ids. Candidate details must respect the existing Workbench privacy and resume visibility rules.

`prepare_final_summary` must accept runtime run id, optional user instruction, source snapshot event seq, and idempotency key. It must reject active runs with `runtime_run_not_completed`. Summary facts must come from final snapshot, events, permitted details, and Workbench-visible records.

### Error Contract

All externally visible errors must use stable reason codes from `../02-agent-tool-and-requirement-contracts.md`. Internal tracebacks can be stored in development artifacts, but agent-facing messages should be short, localized by the caller, and mapped to reason codes.

---

## Source: `goal-1-runtime-control-plane/03-data-storage-and-migrations.md`

## Goal 1 Data Storage And Migrations

### Database

Use the shared database location defined in `../03-runtime-control-state-and-events.md`:

```text
.seektalent/runtime_control.sqlite3
```

Add `AppSettings.runtime_control_db_path` with this default and resolve it through the existing workspace path resolver. The store must create the parent directory when needed. It must not store raw provider cookies, auth headers, browser storage, or private candidate payloads.

### Migration Rules

Use `PRAGMA user_version` for SQLite migration versioning.

Initial version: `1`.

The migration layer must support:

1. initializing an empty DB;
2. re-running initialization without changing existing data;
3. opening an existing version `1` DB;
4. rejecting a future DB version with a clear reason code;
5. applying schema creation in a transaction;
6. proving relative path resolution follows `workspace_root` and production defaults do not point into the repository.

### Required Tables

Implement the tables from `../03-runtime-control-state-and-events.md`:

```text
runtime_control_runs
runtime_requirement_drafts
runtime_requirement_amendments
runtime_approved_requirements
runtime_control_commands
runtime_control_checkpoints
runtime_control_executor_leases
runtime_control_events
runtime_control_snapshots
```

Also implement stable artifact refs and final summaries:

```sql
CREATE TABLE runtime_control_artifact_refs (
  artifact_ref_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  artifact_kind TEXT NOT NULL,
  safe_uri TEXT NOT NULL,
  visibility TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

```sql
CREATE TABLE runtime_control_final_summaries (
  summary_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  user_instruction TEXT,
  summary_json TEXT NOT NULL,
  source_snapshot_event_seq INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
```

### Indexes

Create indexes for the read paths the agent will use:

```sql
CREATE INDEX idx_runtime_events_run_seq
  ON runtime_control_events(runtime_run_id, event_seq);

CREATE INDEX idx_runtime_commands_run_status
  ON runtime_control_commands(runtime_run_id, status);

CREATE INDEX idx_runtime_drafts_conversation
  ON runtime_requirement_drafts(agent_conversation_id, created_at);

CREATE INDEX idx_runtime_amendments_draft
  ON runtime_requirement_amendments(base_draft_revision_id, created_at);

CREATE INDEX idx_runtime_amendments_target_round
  ON runtime_requirement_amendments(runtime_run_id, target_round_no, status)
  WHERE runtime_run_id IS NOT NULL AND target_round_no IS NOT NULL;

CREATE INDEX idx_runtime_runs_conversation
  ON runtime_control_runs(agent_conversation_id, created_at);

CREATE INDEX idx_runtime_events_workbench_seq
  ON runtime_control_events(workbench_event_global_seq)
  WHERE workbench_event_global_seq IS NOT NULL;

CREATE INDEX idx_runtime_executor_leases_run_status
  ON runtime_control_executor_leases(runtime_run_id, status);

CREATE INDEX idx_runtime_executor_leases_expiry
  ON runtime_control_executor_leases(status, lease_expires_at);
```

### JSON Columns

Use JSON text columns for Pydantic-backed structured payloads where SQLite relational modeling would add churn without improving query paths:

- `sections_json`;
- `requirement_sheet_json`;
- `source_ids_json`;
- `payload_json`;
- `snapshot_json`;
- `run_state_json`;
- `source_plan_json`;
- `pending_commands_json`;
- `metadata_json`;
- `summary_json`.

Every JSON write must be created from a typed Python object or existing validated model, not from ad hoc string concatenation.

### Idempotency

State-changing service calls must record idempotency:

- run start: unique by approved requirement revision;
- command creation: unique by `(runtime_run_id, idempotency_key)`;
- draft update, review resolution, and confirmation: stale base revision rejected before creating a new revision or approved requirement;
- free-form amendment: duplicate conversation/idempotency returns the existing amendment result;
- running next-round amendment: duplicate runtime run/idempotency returns the existing target-round amendment result;
- requirement extraction: duplicate conversation/idempotency returns existing draft.

Run start idempotency must be enforced by a database invariant, not only service logic.

### Executor Lease Consistency

Executor start, resume, heartbeat, and recovery use `runtime_control_executor_leases`.

Rules:

- starting or resuming a run creates a new `active` lease with a monotonic `attempt_no`;
- the store rejects a second active lease for the same run;
- executor writes that change run state must include the active `executor_id`;
- heartbeat updates extend or refresh `heartbeat_at` and `lease_expires_at`;
- recovery marks expired leases `expired` before deciding whether the run can fail, requeue, or resume from checkpoint;
- automatic replay is allowed only when no external provider, LLM, browser, or Workbench side effect can have occurred.

Tests must prove active-lease uniqueness, heartbeat update, stale executor write rejection, start timeout recovery, and resume with a new attempt after checkpoint restore.

### Command And Amendment Consistency

Command rows must persist conflict group and supersession fields from `../03-runtime-control-state-and-events.md`.

Tests must prove:

- duplicate idempotency keys do not create duplicate command rows;
- duplicate pending lifecycle commands return the existing pending command;
- cancel supersedes pending pause/resume commands;
- later next-round amendments for the same target round accumulate by default;
- explicit amendment replacement marks the old amendment `superseded`;
- terminal cancel rejects later lifecycle and amendment commands.

### Event Transaction Requirements

Event writes use SQLite `BEGIN IMMEDIATE` and allocate the next sequence from `runtime_control_runs.latest_event_seq + 1` inside the transaction.

The transaction must include:

1. event insert;
2. run latest event seq update;
3. snapshot replacement when applicable;
4. command or amendment state update when the event represents command/amendment application;
5. Workbench event link update when projected.

No reader should observe a new latest event cursor without the corresponding event row.

### Workbench Event Link

When runtime-control emits an event that is also projected into Workbench events, store the Workbench `global_seq` in `runtime_control_events.workbench_event_global_seq`.

Tests must prove:

- a runtime-control event can be traced to its Workbench event when projected;
- replaying a projected runtime-control event does not duplicate the Workbench event;
- missing Workbench projection is visible in a reconciliation test rather than silently ignored.

### Cross-Store Link Integrity

SQLite does not provide foreign keys across separate Workbench, runtime-control, and conversation-agent databases. Runtime control must therefore expose application-level reconciliation.

Rules:

- `workbench_session_id` may be null only while a run is `queued` or `starting`;
- a run visible as `running`, `paused`, `completed`, `cancelled`, or `failed` must have a valid Workbench session link or a terminal failure reason;
- broken Workbench links return `workbench_session_missing` or `runtime_link_broken`;
- reconciliation must verify runtime run id, Workbench session id, approved requirement revision id, and projected Workbench event seq links.

### Test Coverage

Add focused store tests for:

- empty DB initialization;
- idempotent initialization;
- future-version rejection;
- draft create/read/update revision flow;
- free-form amendment normalization and idempotency;
- review-required amendment resolution and stale resolution rejection;
- confirmation rejection when amendments are unresolved;
- stale confirmation rejection with latest draft payload;
- next-round amendment target-round assignment;
- next-round amendment accumulation and explicit supersession;
- current round requirement immutability after `runtime_round_input_locked`;
- approved requirement creation;
- command idempotency;
- lifecycle command conflict and cancel supersession;
- event sequence monotonicity;
- event sequence concurrent writer safety;
- event gap detection;
- event transaction rollback behavior;
- executor start timeout and recovery marking;
- executor lease uniqueness, heartbeat, and stale executor write rejection;
- checkpoint restore after failed executor process;
- snapshot replacement;
- checkpoint write/read;
- final summary persistence;
- Workbench session link reconciliation;
- privacy-sensitive data not accepted in artifact refs.

---

## Source: `goal-1-runtime-control-plane/04-runtime-integration-boundaries.md`

## Goal 1 Runtime Integration Boundaries

### Primary New Package

Goal 1 main code belongs in:

```text
src/seektalent_runtime_control/
```

This package owns the service, store, models, command lifecycle, event/snapshot contracts, checkpoint persistence, artifact policy facade, and executor adapter.

### Thin Existing-Package Integration

Expected existing-package touch points:

```text
src/seektalent/runtime/orchestrator.py
src/seektalent/runtime/source_lanes.py
src/seektalent/tracing.py
src/seektalent/artifacts/store.py
src/seektalent_ui/runtime_bridge.py
src/seektalent_ui/workbench_store.py
src/seektalent_ui/workbench_routes.py
src/seektalent_ui/models.py
src/seektalent_ui/server.py
tests/
tools/
```

Touch only the files needed to expose hooks or wire the new package into existing runtime and Workbench flows. Do not move new runtime-control business logic into `src/seektalent/`. Do not refactor unrelated runtime logic while adding hooks.

### Runtime Hook Strategy

Add explicit hook points around existing runtime stages instead of splitting the whole orchestrator first.

Required hooks:

- requirement extraction started/completed;
- workflow queued/started;
- executor starting/started/start failed;
- before and after round controller;
- query generated;
- source dispatch started/completed;
- candidate merge completed;
- scoring started/completed;
- reflection/feedback completed;
- finalization started/completed;
- failure;
- safe-boundary checkpoint write.

The hook payload should contain stable IDs, stage, round number, source id when relevant, counts, and safe summary text. It should not contain raw provider payloads.

### Workbench Bridge

The existing Workbench routes may keep their current UX. Goal 1 should add a runtime-control bridge so future agent routes can call the same control-plane service.

Bridge rules:

- Workbench session id and runtime run id must be linked.
- Workbench requirement approval must be able to reuse an approved runtime-control requirement revision.
- Workbench event streams and runtime-control events must not contradict each other.
- The bridge must have tests proving a runtime-control start creates or maps a Workbench session.
- `workbench_session_id` may be null only before the run is visible as running.
- Broken runtime-control, Workbench session, or event projection links must be visible through reconciliation and stable reason codes.

### Artifact Policy Integration

Runtime-control configuration must decide artifact output mode before constructing runtime/tracer objects.

Expected integration points:

- `RunTracer` receives a sink or output-mode policy;
- `ArtifactStore` supports compact/off behavior without breaking development writes;
- runtime-control events and snapshots remain available even when filesystem debug output is disabled.

### Source Registry Boundary

Runtime control accepts source ids, validates them against the current catalog/registry-facing surface, and then calls existing source-enabled runtime construction.

Forbidden:

- direct imports from provider-specific modules in runtime-control service code;
- assuming only `cts` and `liepin` can exist;
- deriving UI source labels from provider module names.

### Import Boundary Tests

Add or update tests/tools so these imports fail verification:

```text
seektalent_conversation_agent -> seektalent.runtime
seektalent_conversation_agent -> seektalent.providers
seektalent_runtime_control service modules -> seektalent.providers
frontend generated code -> runtime-control SQLite path
```

The executor adapter is the only runtime-control module allowed to import `seektalent.runtime.orchestrator`.
