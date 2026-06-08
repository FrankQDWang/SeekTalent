# Local Codex Intake Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready local conversation intake harness that uses project-isolated Codex harness and memory to create confirmed Workbench sessions from natural-language hiring needs.

**Architecture:** Add a new `seektalent_intake` backend package, a thin FastAPI intake router, and a Svelte transcript UI. Codex runs through a project-local Codex App Server boundary with `CODEX_HOME=.seektalent/codex_home`; Workbench remains the only bridge into the existing runtime. The initial agent can start the workflow and read progress/results, but cannot mutate in-progress runtime state.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, Codex CLI App Server, Svelte 5, TanStack Svelte Query, Vitest, pytest, Ruff.

---

## Spec References

Read these before implementation:

- `local-codex-intake-harness/01-product-goal.md`
- `local-codex-intake-harness/02-boundaries.md`
- `local-codex-intake-harness/03-system-architecture.md`
- `local-codex-intake-harness/04-codex-harness-and-memory.md`
- `local-codex-intake-harness/05-model-provider-config.md`
- `local-codex-intake-harness/06-intake-conversation-contract.md`
- `local-codex-intake-harness/07-workbench-integration-contract.md`
- `local-codex-intake-harness/08-frontend-ux.md`
- `local-codex-intake-harness/09-data-storage-and-migrations.md`
- `local-codex-intake-harness/10-acceptance-criteria.md`

## File Structure

Create:

```text
src/seektalent_intake/__init__.py
src/seektalent_intake/models.py
src/seektalent_intake/errors.py
src/seektalent_intake/paths.py
src/seektalent_intake/store.py
src/seektalent_intake/codex_config.py
src/seektalent_intake/codex_app_server.py
src/seektalent_intake/intake_service.py
src/seektalent_intake/workbench_bridge.py
src/seektalent_intake/workflow_reader.py
src/seektalent_intake/source_catalog.py
src/seektalent_ui/intake_routes.py
tests/test_intake_paths.py
tests/test_intake_store.py
tests/test_intake_codex_config.py
tests/test_intake_codex_app_server.py
tests/test_intake_service.py
tests/test_intake_workbench_bridge.py
tests/test_intake_workflow_reader.py
tests/test_intake_source_catalog.py
apps/web-svelte/src/lib/api/intake.ts
apps/web-svelte/src/lib/intake/types.ts
apps/web-svelte/src/lib/intake/state.ts
apps/web-svelte/src/lib/intake/state.test.ts
apps/web-svelte/src/lib/components/IntakeConversation.svelte
apps/web-svelte/src/lib/components/IntakeConversation.test.ts
```

Modify:

```text
THIRD_PARTY_NOTICES.md
src/seektalent_ui/server.py
src/seektalent_ui/models.py
tests/test_workbench_api.py
apps/web-svelte/src/lib/api/schema.d.ts
apps/web-svelte/src/lib/query/keys.ts
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
```

Avoid unless forced by a failing acceptance gate:

```text
src/seektalent/runtime/**
src/seektalent/config.py
src/seektalent_ui/workbench_store.py
src/seektalent_ui/runtime_bridge.py
scripts/verify-dev-workbench.sh
scripts/verify-red-zone.sh
```

## Task 1: Add Path Guardrails

**Files:**

- Create: `src/seektalent_intake/paths.py`
- Create: `tests/test_intake_paths.py`

- [ ] Write tests proving the intake data root resolves to `.seektalent`, Codex home resolves to `.seektalent/codex_home`, memory resolves below that home, and Codex cwd resolves to `.seektalent/codex_workspace`.
- [ ] Write tests proving `~/.codex`, the user's home directory, and the repository root are rejected as Codex home or Codex cwd.
- [ ] Implement `IntakePaths` with resolved absolute paths and named validation errors.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_paths.py -q`.

Expected public functions:

```python
def build_intake_paths(workspace_root: Path) -> IntakePaths: ...
def ensure_safe_codex_paths(paths: IntakePaths) -> None: ...
```

## Task 2: Add Intake Models And Errors

**Files:**

- Create: `src/seektalent_intake/models.py`
- Create: `src/seektalent_intake/errors.py`
- Create or update: `tests/test_intake_service.py`

- [ ] Define literal state values from `06-intake-conversation-contract.md`.
- [ ] Define Pydantic models for message, draft, conversation, Codex turn result, and public error response.
- [ ] Define named exceptions for unsafe paths, unavailable Codex harness, provider failure, stale confirmation, invalid draft, and Workbench handoff failure.
- [ ] Add tests for validation limits matching `WorkbenchSessionCreateRequest`: `jobTitle` 256, `jdText` 20000, `notes` 5000, and source ids drawn from the current source catalog.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_service.py -q`.

Required reason codes:

```text
codex_cli_missing
codex_app_server_unavailable
codex_provider_smoke_failed
codex_memory_path_unsafe
provider_not_configured
intake_draft_invalid
intake_confirmation_stale
intake_already_confirmed
workbench_session_create_failed
requirement_prepare_failed
source_catalog_unavailable
source_id_unavailable
```

## Task 3: Add SQLite Store

**Files:**

- Create: `src/seektalent_intake/store.py`
- Create: `tests/test_intake_store.py`

- [ ] Write store initialization tests for an empty database.
- [ ] Write idempotent initialization tests.
- [ ] Write future schema version rejection test.
- [ ] Write CRUD tests for conversation, messages, drafts, errors, Codex thread id, Workbench session id, and user/workspace scoping.
- [ ] Implement SQLite schema version 1 using `PRAGMA user_version`.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_store.py -q`.

Store methods must be explicit and small. Prefer module-level helpers for row mapping. Do not hide SQL behind a generic repository abstraction.

## Task 4: Add Codex Config And Provider Smoke

**Files:**

- Create: `src/seektalent_intake/codex_config.py`
- Create: `tests/test_intake_codex_config.py`

- [ ] Test generated TOML uses `model_provider = "dashscope"`.
- [ ] Test generated TOML uses `env_key = "SEEKTALENT_TEXT_LLM_API_KEY"`.
- [ ] Test generated TOML enables memories.
- [ ] Test generated TOML does not contain `requires_openai_auth`.
- [ ] Test missing `SEEKTALENT_TEXT_LLM_API_KEY` returns `provider_not_configured`.
- [ ] Implement config generation and validation.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_codex_config.py -q`.

The provider smoke function should return structured status instead of raising for expected operator setup states.

## Task 5: Add Codex App Server Adapter

**Files:**

- Create: `src/seektalent_intake/codex_app_server.py`
- Create: `tests/test_intake_codex_app_server.py`

- [ ] Write tests using a fake process transport that asserts `CODEX_HOME` and cwd are project-local.
- [ ] Write tests for successful structured turn parsing.
- [ ] Write tests for malformed JSON returning `intake_draft_invalid`.
- [ ] Write tests for process start failure returning `codex_app_server_unavailable`.
- [ ] Write tests proving the adapter does not use the Codex SDK path.
- [ ] Implement a narrow adapter around Codex App Server.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_codex_app_server.py -q`.

The adapter must be injectable. Unit tests must not require a real Codex binary.

## Task 6: Add Source Catalog And Intake Service

**Files:**

- Create: `src/seektalent_intake/source_catalog.py`
- Create: `src/seektalent_intake/intake_service.py`
- Create: `tests/test_intake_source_catalog.py`
- Update: `tests/test_intake_service.py`

- [ ] Write tests proving selectable sources come from a catalog abstraction.
- [ ] Write tests proving `cts`/`liepin` can be accepted as current registered ids but are not hard-coded as the complete source universe.
- [ ] Write tests proving unavailable source ids are rejected with `source_id_unavailable`.
- [ ] Write tests for `new -> collecting`.
- [ ] Write tests for `collecting -> clarifying`.
- [ ] Write tests for `collecting -> draft_ready`.
- [ ] Write tests for draft edit and validation.
- [ ] Write tests for stale confirmation rejection.
- [ ] Write tests for Codex failure recording.
- [ ] Implement the source catalog abstraction and service with explicit state transitions.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_source_catalog.py tests/test_intake_service.py -q`.

The service must not import `seektalent.runtime`.

## Task 7: Add Workbench Bridge And Workflow Reader

**Files:**

- Create: `src/seektalent_intake/workbench_bridge.py`
- Create: `src/seektalent_intake/workflow_reader.py`
- Create: `tests/test_intake_workbench_bridge.py`
- Create: `tests/test_intake_workflow_reader.py`

- [ ] Write tests proving a confirmed draft creates one Workbench session.
- [ ] Write tests proving duplicate confirmation returns the existing session id.
- [ ] Write tests proving requirement preparation is started through `WorkbenchJobRunner`.
- [ ] Write tests proving requirement review is not auto-approved.
- [ ] Write tests proving progress/result questions read Workbench state without mutating runtime state.
- [ ] Write tests proving the workflow reader does not import `seektalent.runtime`.
- [ ] Implement the bridge using existing Workbench store and runner boundaries.
- [ ] Implement a read-only workflow reader over existing Workbench/session/event/runtime-graph/final result surfaces.
- [ ] Run `uv run --group dev python -m pytest tests/test_intake_workbench_bridge.py tests/test_intake_workflow_reader.py -q`.

The bridge must not call `WorkflowRuntime` directly.

## Task 8: Add Intake API Routes

**Files:**

- Create: `src/seektalent_ui/intake_routes.py`
- Modify: `src/seektalent_ui/server.py`
- Modify: `src/seektalent_ui/models.py`
- Modify: `tests/test_workbench_api.py`

- [ ] Add route tests for unauthenticated access rejection.
- [ ] Add CSRF tests for every mutation route.
- [ ] Add tests for source catalog, create conversation, append message, edit draft, confirm, provider smoke, and memory reset.
- [ ] Add tests for a progress question after Workbench session creation.
- [ ] Wire `app.state.intake_service` in `create_app`.
- [ ] Include the intake router.
- [ ] Run `uv run --group dev python -m pytest tests/test_workbench_api.py tests/test_intake_* -q`.

Required routes are listed in `10-acceptance-criteria.md`.

## Task 9: Generate OpenAPI And Add Frontend API

**Files:**

- Modify generated: `apps/web-svelte/src/lib/api/schema.d.ts`
- Create: `apps/web-svelte/src/lib/api/intake.ts`
- Create: `apps/web-svelte/src/lib/intake/types.ts`
- Modify: `apps/web-svelte/src/lib/query/keys.ts`

- [ ] Start the local API on `127.0.0.1:8012`.
- [ ] Run `cd apps/web-svelte && bun run api:gen`.
- [ ] Add typed frontend API wrappers for intake routes.
- [ ] Add query keys for intake conversation and provider smoke.
- [ ] Add typed source catalog API wrapper.
- [ ] Run `cd apps/web-svelte && bun run test -- src/lib/intake/state.test.ts`.

Do not hand-edit generated schema except through `bun run api:gen`.

## Task 10: Add Frontend Conversation State Helpers

**Files:**

- Create: `apps/web-svelte/src/lib/intake/state.ts`
- Create: `apps/web-svelte/src/lib/intake/state.test.ts`

- [ ] Test empty state labels.
- [ ] Test sending/disabled behavior.
- [ ] Test draft-ready behavior.
- [ ] Test stale confirmation error mapping.
- [ ] Test provider-not-configured error mapping.
- [ ] Implement small pure helpers for UI state derivation.
- [ ] Run `cd apps/web-svelte && bun run test -- src/lib/intake/state.test.ts`.

Keep these helpers pure. Do not put network calls here.

## Task 11: Add Intake Conversation UI

**Files:**

- Create: `apps/web-svelte/src/lib/components/IntakeConversation.svelte`
- Create: `apps/web-svelte/src/lib/components/IntakeConversation.test.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/+page.svelte`

- [ ] Write component tests for initial empty state.
- [ ] Write component tests for sending a message.
- [ ] Write component tests for clarification display.
- [ ] Write component tests for confirmation card and edit controls.
- [ ] Write component tests for confirm-and-navigate callback.
- [ ] Write component tests for upward-scrolling transcript rendering.
- [ ] Write component tests for progress/result question messages after session creation.
- [ ] Replace the primary manual create form on `/sessions` with the intake conversation component.
- [ ] Run `cd apps/web-svelte && bun run test -- src/lib/components/IntakeConversation.test.ts`.

The manual fields should be available as confirmation edits, not as the primary first screen. Keep visual styling intentionally modest so the later designer-led redesign can replace the component without changing backend contracts.

## Task 12: Run Full Verification

**Files:**

- No source changes unless verification exposes a defect.

- [ ] Run the focused Python tests from `10-acceptance-criteria.md`.
- [ ] Run `uv run --group dev ruff check src tests`.
- [ ] Run `cd apps/web-svelte && bun run check`.
- [ ] Run `cd apps/web-svelte && bun run lint`.
- [ ] Run `cd apps/web-svelte && bun run test`.
- [ ] Run `cd apps/web-svelte && bun run build`.
- [ ] Run `scripts/verify-dev-workbench.sh`.
- [ ] Run `scripts/verify-red-zone.sh` only if red-zone paths were touched.
- [ ] Run `git diff --check`.

## Task 13: Verify Commercial Packaging Boundary

**Files:**

- Create or modify: `THIRD_PARTY_NOTICES.md`
- Create or modify packaging tests only if packaging behavior is implemented in this task.

- [ ] Document whether SeekTalent redistributes Codex artifacts or invokes an operator-installed Codex binary.
- [ ] If Codex artifacts are redistributed, add the applicable Apache-2.0 notice to `THIRD_PARTY_NOTICES.md` and ensure the packaged product includes it.
- [ ] Add or update packaging exclusion checks so product archives do not include `~/.codex`, `.seektalent/codex_home/memories`, Codex auth state, provider credentials, or Codex thread history.
- [ ] If packaging code is not touched in this implementation, add a clear final-report statement that packaging distribution was not changed and identify the required follow-up gate before commercial distribution.

## Task 14: Final Report

The final report must include:

- files changed;
- confirmation that `~/.codex` was not touched;
- confirmation that Codex memory, auth state, and global `~/.codex` are not packaged;
- Apache-2.0 notice handling for Codex artifacts;
- confirmation that the implementation uses Codex App Server directly and not the Codex SDK;
- confirmation that initial workflow control is start/read-only and does not mutate in-progress runtime state;
- confirmation that runtime internals were not touched, or explanation and red-zone evidence if they were;
- Codex provider smoke result;
- frontend verification result;
- backend verification result;
- Workbench integration result;
- known limitations, limited to actual unresolved facts.
