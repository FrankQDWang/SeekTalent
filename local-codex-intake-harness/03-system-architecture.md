# System Architecture

## Target Shape

```text
Svelte new-session page
  -> Intake FastAPI router
  -> Intake service
     -> Intake SQLite store
     -> Project-isolated Codex App Server
        -> Project-isolated Codex memory
     -> Workbench bridge
        -> existing WorkbenchStore
        -> existing WorkbenchJobRunner
        -> existing runtime bridge
        -> existing WorkflowRuntime
```

## Component Responsibilities

### Frontend

The frontend owns the user-facing conversation and confirmation controls:

- message input;
- upward-scrolling transcript rendering;
- clarification prompts;
- confirmation card;
- source kind picker;
- edit-before-confirm controls;
- create-session transition;
- workflow progress question/answer surface;
- error display;
- retry and reset controls.

The frontend does not parse requirements itself.

### Intake API Router

The router is a thin HTTP layer:

- authenticates the current Workbench user;
- enforces CSRF for mutations;
- validates request bodies;
- calls `IntakeService`;
- returns API-safe response models;
- does not contain Codex protocol logic;
- does not contain SQLite SQL;
- does not import runtime internals.

### Intake Service

The service owns the state machine:

```text
new
  -> collecting
  -> clarifying
  -> draft_ready
  -> confirmed
  -> session_created
  -> requirement_prepare_started
```

The service decides when to ask a clarifying question, when to request a structured draft from Codex, when a confirmation can be accepted, when to call the Workbench bridge, and how to answer read-only progress/result questions after a workflow has started.

### Codex Adapter

The adapter owns the narrow process/protocol boundary to Codex App Server. The Codex SDK is intentionally out of scope for this feature.

It must:

- launch with `CODEX_HOME` set to the project-local Codex home;
- launch with cwd set to the project-local Codex workspace;
- refuse to run if either resolved path is `~/.codex`;
- stream or collect Codex turn output;
- request structured intake JSON;
- expose named failure reasons;
- allow tests to use a fake adapter without launching Codex.

### Intake Store

The store owns local persistence:

- conversations;
- messages;
- structured drafts;
- confirmation status;
- Codex thread id mapping;
- Workbench session id mapping;
- error records.

### Workbench Bridge

The bridge owns the handoff from confirmed intake draft to existing Workbench flow:

```text
ConfirmedIntakeDraft
  -> WorkbenchStore.create_workbench_session(...)
  -> WorkbenchJobRunner.start_requirement_review(...)
```

The bridge must not call `WorkflowRuntime` directly.

### Workflow Reader

The workflow reader owns read-only access to existing Workbench workflow state after session creation:

```text
Workbench session
  -> requirement review status
  -> source run status
  -> Workbench events
  -> runtime graph
  -> final shortlist/results
```

Initial version rules:

- it may read progress and results;
- it may summarize progress in the transcript;
- it may not mutate runtime state;
- it may not approve requirements automatically;
- it may not pause, resume, cancel, replan, or inject data into a running workflow.

## Data Flow

```text
User message
  -> POST /api/intake/conversations/{id}/messages
  -> store message
  -> Codex turn with project-local memory
  -> structured draft or clarification
  -> store assistant message and draft
  -> response to frontend
```

```text
User confirms
  -> POST /api/intake/conversations/{id}/confirm
  -> validate latest draft
  -> create Workbench session
  -> start requirement preparation
  -> store session id and handoff status
  -> response includes Workbench session id
```

```text
User asks "现在进度怎么样？"
  -> POST /api/intake/conversations/{id}/messages
  -> detect progress/result question
  -> read Workbench session/events/runtime graph/final result
  -> ask Codex App Server to summarize read-only state when useful
  -> store assistant progress answer
  -> response to frontend transcript
```

## Codex Execution Context

Codex App Server must run with:

```text
CODEX_HOME=<repo>/.seektalent/codex_home
cwd=<repo>/.seektalent/codex_workspace
```

The Codex workspace is intentionally not the repository root. This protects parallel runtime refactors and prevents the intake harness from giving Codex broad implicit write access to the project source tree during ordinary user conversations.

## Project Data Layout

```text
.seektalent/
  intake.sqlite3
  codex_home/
    config.toml
    memories/
    threads/
  codex_workspace/
    README.md
```

The implementation may use a temporary path under tests. Production local runs must not use `~/.codex`.

## Dependency Direction

Allowed:

```text
seektalent_ui -> seektalent_intake -> seektalent_ui.workbench_store through explicit bridge inputs
```

Forbidden:

```text
seektalent_intake -> seektalent.runtime
seektalent_intake -> seektalent.providers
Codex adapter -> WorkbenchStore
Workflow reader -> seektalent.runtime internals
Frontend -> Codex app-server
```

## Failure Visibility

Every failure must be visible through a named reason code. The user should see short Chinese copy, while logs and tests should assert stable English reason codes.

Examples:

```text
codex_cli_missing
codex_app_server_unavailable
codex_provider_smoke_failed
codex_memory_path_unsafe
intake_draft_invalid
intake_confirmation_stale
workbench_session_create_failed
requirement_prepare_failed
```
