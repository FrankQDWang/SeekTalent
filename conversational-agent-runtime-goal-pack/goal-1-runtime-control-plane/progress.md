# Runtime Control Plane Progress

## Run Identity

- Goal pack: `conversational-agent-runtime-goal-pack`
- Goal: `goal-1-runtime-control-plane`
- Started at: `2026-06-08T11:29:37Z`
- Branch: `codex/conversational-agent-goal-pack`
- HEAD at start: `c365e52dcb9ca0150939fa66313bc924a5a2dd73`
- Origin main at start: `990e6c1b7a4890566a3f78d2d15d306e9adce175`
- Merge-base with origin/main: `990e6c1b7a4890566a3f78d2d15d306e9adce175`
- Worktree path: `/Users/frankqdwang/Agents/SeekTalent-0.2.4`
- Worktree isolation: normal repository checkout, not a linked worktree. Continuing in place because this branch is already feature-scoped and the active goal says current worktree state is authoritative.
- Dirty state at start:

```text
 M conversational-agent-runtime-goal-pack/00-codex-goal.md
 M conversational-agent-runtime-goal-pack/02-agent-tool-and-requirement-contracts.md
 M conversational-agent-runtime-goal-pack/03-runtime-control-state-and-events.md
 M conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md
 M conversational-agent-runtime-goal-pack/MANIFEST.md
 M conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/PLAN.md
```

- Stashes observed:

```text
stash@{0}: On main: pre-runtime-followup-main-doc-edits
stash@{1}: On main: pre-merge safety stash before liepin browser session probe
stash@{2}: On main: backup-runtime-multi-source-plan-docs-moved-to-worktree
```

## Current Phase

- Phase: Phase 9 - Full Goal Verification
- Status: complete
- Completed at: `2026-06-08T13:52:13Z`
- Latest successful command: `scripts/verify-dev-workbench.sh`
- Latest failed command: none
- Current blocker: none.

## Phase Evidence

| Phase | Status | Files changed | Tests/checks | Evidence |
| --- | --- | --- | --- | --- |
| Phase 1 - Preflight And Boundary Baseline | complete | `conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md` | Goal 1 preflight, boundary checks | Baseline checks exited 0 before product edits. Runtime/model/Workbench/tracing symbols found at expected paths. |
| Plan review gate | blocked | `conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md` | `fw-plan-review` engineering checklist plus read-only plan-review subagent | Subagent verdict: blocked. Further implementation stopped. Blockers recorded below. |
| Plan blocker remediation | complete | `conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/PLAN.md`, `conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md` | Plan-review subagent rerun | Rerun verdict: clear. Product implementation can proceed. |
| Phase 2 - Store And Models | complete | `src/seektalent_runtime_control/__init__.py`, `src/seektalent_runtime_control/errors.py`, `src/seektalent_runtime_control/models.py`, `src/seektalent_runtime_control/source_catalog.py`, `src/seektalent_runtime_control/store.py`, `src/seektalent/config.py`, `pyproject.toml`, `tach.toml`, `tools/check_source_boundaries.py`, `tests/test_runtime_control_store.py`, `tests/test_runtime_control_boundaries.py`, `tests/test_runtime_control_source_catalog.py` | `uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_boundaries.py tests/test_runtime_control_source_catalog.py -q`; boundary gates | Red: 8 failures before package/settings/boundary implementation. Green: 8 passed after implementation. Source boundaries, Tach baseline, and arch imports exited 0. |
| Phase 3 - Requirement Draft Service | complete | `src/seektalent_runtime_control/errors.py`, `src/seektalent_runtime_control/requirements.py`, `src/seektalent_runtime_control/service.py`, `src/seektalent_runtime_control/store.py`, `tests/test_runtime_control_requirements.py`, `tests/test_runtime_control_requirement_amendments.py`, `tests/test_runtime_control_requirement_review.py` | `uv run --group dev python -m pytest tests/test_runtime_control_requirements.py tests/test_runtime_control_requirement_amendments.py tests/test_runtime_control_requirement_review.py -q`; Phase 2+3 focused bundle; boundary gates | Red: collection failed before `seektalent_runtime_control.requirements` existed. Green: requirement draft/review tests passed; Phase 2+3 bundle passed with 13 tests; source boundaries, Tach baseline, and arch imports exited 0. |
| Phase 4 - Runtime Executor, Events, And Checkpoints | complete | `src/seektalent_runtime_control/executor.py`, `src/seektalent_runtime_control/models.py`, `src/seektalent_runtime_control/recovery.py`, `src/seektalent_runtime_control/store.py`, `tach.toml`, `tests/test_runtime_control_workflow_adapter.py`, `tests/test_runtime_control_events.py`, `tests/test_runtime_control_recovery.py`, `tests/test_runtime_control_checkpoints.py` | `uv run --group dev python -m pytest tests/test_runtime_control_workflow_adapter.py tests/test_runtime_control_events.py tests/test_runtime_control_recovery.py tests/test_runtime_control_checkpoints.py -q`; Phase 2-4 focused bundle; boundary gates | Red: 7 failures for missing executor/recovery modules, lease/checkpoint models, and store APIs. Green: Phase 4 focused tests passed with 7 tests; Phase 2-4 bundle passed with 20 tests. Tach initially rejected executor dependencies on runtime/source adapters, then passed after declaring package-level deps while source-boundary checks still confine those imports to `executor.py`. |
| Phase 5 - Commands And Safe Boundaries | complete | `src/seektalent_runtime_control/commands.py`, `src/seektalent_runtime_control/models.py`, `src/seektalent_runtime_control/store.py`, `tests/test_runtime_control_commands.py`, `tests/test_runtime_control_next_round_requirements.py` | `uv run --group dev python -m pytest tests/test_runtime_control_commands.py tests/test_runtime_control_next_round_requirements.py -q`; Phase 2-5 focused bundle; boundary gates | Red: 5 failures because `seektalent_runtime_control.commands` did not exist. Green: Phase 5 focused tests passed with 5 tests; Phase 2-5 bundle passed with 25 tests; source boundaries, Tach baseline, and arch imports exited 0. |
| Phase 6 - Detail Reads And Final Summary | complete | `src/seektalent_runtime_control/detail.py`, `src/seektalent_runtime_control/models.py`, `src/seektalent_runtime_control/store.py`, `tests/test_runtime_control_detail.py`, `tests/test_runtime_control_final_summary.py` | `uv run --group dev python -m pytest tests/test_runtime_control_detail.py tests/test_runtime_control_final_summary.py -q`; Phase 2-6 focused bundle; boundary gates | Red: 5 failures because `seektalent_runtime_control.detail` did not exist. Green: Phase 6 focused tests passed with 5 tests; Phase 2-6 bundle passed with 30 tests; source boundaries, Tach baseline, and arch imports exited 0. |
| Phase 7 - Workbench Bridge | complete | `src/seektalent_runtime_control/workbench_bridge.py`, `src/seektalent_runtime_control/store.py`, `tach.toml`, `tests/test_runtime_control_workbench_bridge.py` | `uv run --group dev python -m pytest tests/test_runtime_control_workbench_bridge.py -q`; `uv run --group dev python -m pytest tests/test_workbench_api.py ... -q`; boundary gates | Red: 3 failures because `seektalent_runtime_control.workbench_bridge` did not exist. Green: bridge tests passed with 3 tests; Workbench API plus accumulated runtime-control suite passed with 147 tests. Tach initially rejected the Workbench bridge dependency, then passed after declaring `seektalent_ui`; source boundaries and arch imports exited 0. |
| Phase 8 - Artifact Policy And Retention | complete | `src/seektalent/api.py`, `src/seektalent/config.py`, `src/seektalent/runtime/orchestrator.py`, `src/seektalent/tracing.py`, `src/seektalent_runtime_control/artifact_policy.py`, `src/seektalent_runtime_control/retention.py`, `src/seektalent_runtime_control/store.py`, `tests/test_runtime_control_artifact_policy.py`, `tests/test_runtime_control_retention.py` | `uv run --group dev python -m pytest tests/test_runtime_control_artifact_policy.py tests/test_runtime_control_retention.py -q`; full Goal 1 required focused suite; boundary gates | Red: 5 failures because `RunTracer` did not support `output_mode` and `seektalent_runtime_control.retention` did not exist. Green: Phase 8 focused tests passed with 5 tests; full Goal 1 required focused suite passed with 152 tests; source boundaries, Tach baseline, and arch imports exited 0. |
| Phase 9 - Full Goal Verification | complete | `src/seektalent/tracing.py`, `src/seektalent_runtime_control/*.py`, `conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md` | Full Goal 1 focused suite, source/Tach/arch, ruff, ty, AI bad-smell, red-zone, dev workbench, diff check | Goal 1 required focused suite passed with 152 tests; source boundaries, Tach baseline, arch imports, ruff, ty, AI bad-smell, and `git diff --check` passed; `scripts/verify-red-zone.sh` passed; `scripts/verify-dev-workbench.sh` passed with the existing chunk-size warning and skipped real-backend mutable smoke because `127.0.0.1:8012` was already owned. |

## Red-Green Evidence

| Check | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |
| Boundary baseline | not applicable before implementation | none | `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 2 store/settings/boundaries | `uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_boundaries.py tests/test_runtime_control_source_catalog.py -q` failed with 8 failures: missing `runtime_control_path`, missing `seektalent_runtime_control`, missing runtime-control boundary detection | Added `AppSettings.runtime_control_path`, `src/seektalent_runtime_control/` core models/store/source catalog/errors, package metadata, Tach module, and source-boundary checks | Same command passed: `8 passed in 0.90s`; `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 3 requirement lifecycle | `uv run --group dev python -m pytest tests/test_runtime_control_requirements.py tests/test_runtime_control_requirement_amendments.py tests/test_runtime_control_requirement_review.py -q` failed during collection because `seektalent_runtime_control.requirements` did not exist | Added requirement draft models, service methods, draft/amendment/approved store APIs, idempotency replay handling, review-state refresh, and microsecond timestamps for revision ordering | Same command passed: `5 passed in 0.93s`; Phase 2+3 bundle passed: `13 passed in 1.11s`; `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 4 executor/events/checkpoints | `uv run --group dev python -m pytest tests/test_runtime_control_workflow_adapter.py tests/test_runtime_control_events.py tests/test_runtime_control_recovery.py tests/test_runtime_control_checkpoints.py -q` failed with 7 failures for missing executor/recovery modules, lease/checkpoint models, and store APIs | Added executor adapter, lease/checkpoint models, atomic executor event writes, checkpoint persistence, lease heartbeat/expiry, and recovery decisions | Same command passed: `7 passed in 1.03s`; Phase 2-4 bundle passed: `20 passed in 1.45s`; source boundaries and arch imports exited 0; Tach failed once on executor dependencies, then passed after updating `tach.toml` while preserving executor-only boundary checks |
| Phase 5 commands/safe boundaries | `uv run --group dev python -m pytest tests/test_runtime_control_commands.py tests/test_runtime_control_next_round_requirements.py -q` failed with 5 failures because `seektalent_runtime_control.commands` did not exist | Added command DTOs, command service lifecycle conflict rules, safe-boundary application, pause checkpoint handling, and running next-round amendment scheduling/activation | Same command passed: `5 passed in 0.74s`; Phase 2-5 bundle passed: `25 passed in 1.50s`; `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 6 detail/final summary | `uv run --group dev python -m pytest tests/test_runtime_control_detail.py tests/test_runtime_control_final_summary.py -q` failed with 5 failures because `seektalent_runtime_control.detail` did not exist | Added detail read-model service, checkpoint/event-backed detail responses, safe artifact filtering, final-summary idempotency, terminal-run enforcement, and stale snapshot cursor handling | Same command passed: `5 passed in 0.86s`; Phase 2-6 bundle passed: `30 passed in 1.97s`; `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 7 Workbench bridge | `uv run --group dev python -m pytest tests/test_runtime_control_workbench_bridge.py -q` failed with 3 failures because `seektalent_runtime_control.workbench_bridge` did not exist | Added Workbench bridge session linking, runtime event projection with Workbench `global_seq` backfill, replay protection through existing Workbench idempotency, and stable reconciliation reason codes | Same command passed: `3 passed in 0.99s`; Workbench API plus accumulated runtime-control suite passed: `147 passed in 20.72s`; source boundaries and arch imports exited 0; Tach failed once on the Workbench dependency, then passed after updating `tach.toml` |
| Phase 8 artifact policy/retention | `uv run --group dev python -m pytest tests/test_runtime_control_artifact_policy.py tests/test_runtime_control_retention.py -q` failed with 5 failures because `RunTracer` did not accept `output_mode` and retention service did not exist | Added runtime artifact output modes, compact/off tracing behavior, settings wiring into `WorkflowRuntime`, runtime retention service, and terminal-event payload compaction | Same command passed: `5 passed in 0.70s`; full Goal 1 required focused suite passed: `152 passed in 20.87s`; `uv run python tools/check_source_boundaries.py` exited 0; `uv run python tools/check_tach_baseline.py` printed `Tach baseline ok: 0 current accepted failures`; `uv run python tools/check_arch_imports.py` exited 0 |
| Phase 9 full verification | `uv run --group dev ty check src tests` initially failed after broad `Any` removal with 33 diagnostics around unknown JSON dict keys, one event payload inference issue, and suppressed artifact-session typing | Added string-key JSON normalization at runtime-control boundaries, tightened event payload typing, replaced `Any` in touched tracing signatures with `object`, and made `off_except_db` use a suppressed artifact store/session without local writes | Final checks passed: Goal 1 focused suite `152 passed in 21.10s`; source boundaries exit 0; Tach baseline `Tach baseline ok: 0 current accepted failures`; arch imports exit 0; ruff `All checks passed!`; ty `All checks passed!`; AI bad-smell exit 0; `scripts/verify-red-zone.sh` passed with Python red-zone `290 passed in 10.59s`, source decoupling `173 passed in 2.08s`, and Liepin worker `73 pass`; `scripts/verify-dev-workbench.sh` passed with Python `215 passed in 20.89s`, Svelte check 0 errors/warnings, Vitest 31 files/115 tests passed, Playwright parity 10 passed, existing chunk-size warning, and skipped mutable smoke because `127.0.0.1:8012` was already owned; `git diff --check` exit 0 |

## Preflight Output

### `pwd`

```text
/Users/frankqdwang/Agents/SeekTalent-0.2.4
```

### `git branch --show-current`

```text
codex/conversational-agent-goal-pack
```

### `git rev-parse HEAD`

```text
c365e52dcb9ca0150939fa66313bc924a5a2dd73
```

### `git rev-parse --verify origin/main`

```text
990e6c1b7a4890566a3f78d2d15d306e9adce175
```

### `git merge-base HEAD origin/main`

```text
990e6c1b7a4890566a3f78d2d15d306e9adce175
```

### `git status --short --untracked-files=all`

```text
 M conversational-agent-runtime-goal-pack/00-codex-goal.md
 M conversational-agent-runtime-goal-pack/02-agent-tool-and-requirement-contracts.md
 M conversational-agent-runtime-goal-pack/03-runtime-control-state-and-events.md
 M conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md
 M conversational-agent-runtime-goal-pack/MANIFEST.md
 M conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/PLAN.md
```

### `git stash list`

```text
stash@{0}: On main: pre-runtime-followup-main-doc-edits
stash@{1}: On main: pre-merge safety stash before liepin browser session probe
stash@{2}: On main: backup-runtime-multi-source-plan-docs-moved-to-worktree
```

### `uv run python tools/check_source_boundaries.py`

```text
```

Exit code: `0`.

### `uv run python tools/check_tach_baseline.py`

```text
Tach baseline ok: 0 current accepted failures
```

Exit code: `0`.

### `uv run python tools/check_arch_imports.py`

```text
```

Exit code: `0`.

### Runtime and model fact commands

`rg -n "class WorkflowRuntime|def run\\(|def run_async|def extract_requirements|def _run_rounds|def _refresh_runtime_candidate_checkpoint" src/seektalent/runtime/orchestrator.py`

```text
390:class WorkflowRuntime:
472:    def run(
501:    def extract_requirements(
720:    async def extract_requirements_async(
757:    async def run_async(
1820:    async def _run_rounds(
2308:    def _refresh_runtime_candidate_checkpoint(
```

`rg -n "class RequirementSheet|class QueryTermCandidate|class RunState|class RetrievalState" src/seektalent/models.py`

```text
252:class QueryTermCandidate(BaseModel):
278:class RequirementSheet(BaseModel):
557:class RetrievalState(BaseModel):
1414:class RunState(BaseModel):
```

`rg -n "extract_requirement_review|run_runtime_sourcing_job" src/seektalent_ui/runtime_bridge.py`

```text
23:def extract_requirement_review(
45:def run_runtime_sourcing_job(
```

`rg -n "RunTracer|ArtifactStore|ArtifactSession" src/seektalent src/seektalent_ui tests`

```text
tests/test_liepin_corpus_integration.py:9:from seektalent.artifacts import ArtifactStore
tests/test_scoring_cache.py:24:from seektalent.tracing import ProviderUsageSnapshot, RunTracer
tests/test_cli.py:12:from seektalent.artifacts import ArtifactResolver, ArtifactStore
tests/test_runtime_source_lanes.py:54:from seektalent.tracing import RunTracer
tests/test_liepin_boundary_preflight.py:2:    from seektalent.artifacts import ArtifactStore
tests/test_flywheel_runtime.py:7:from seektalent.artifacts import ArtifactStore
tests/test_flywheel_runtime.py:18:from seektalent.tracing import RunTracer
tests/test_artifact_path_contract.py:9:from seektalent.tracing import RunTracer
tests/test_runtime_audit.py:40:from seektalent.artifacts import ArtifactStore
tests/test_runtime_audit.py:57:from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer, json_sha256, provider_usage_from_result
tests/test_flywheel_datasets.py:6:from seektalent.artifacts import ArtifactStore
tests/test_runtime_public_event_contract.py:12:from seektalent.tracing import RunTracer
tests/test_artifact_store.py:13:from seektalent.artifacts.store import ArtifactResolver, ArtifactStore
tests/test_location_execution_plan.py:15:from seektalent.tracing import RunTracer
tests/test_runtime_state_flow.py:64:from seektalent.tracing import RunTracer
tests/test_evaluation.py:41:from seektalent.artifacts import ArtifactResolver, ArtifactStore
tests/test_openclaw_baseline.py:19:from seektalent.tracing import RunTracer
tests/test_runtime_multi_source_round_dispatch.py:56:from seektalent.tracing import RunTracer
tests/test_corpus_runtime.py:9:from seektalent.artifacts import ArtifactStore
src/seektalent/scoring/scorer.py:22:from seektalent.tracing import LLMCallSnapshot, RunTracer
src/seektalent/flywheel/datasets.py:8:from seektalent.artifacts import ArtifactStore
src/seektalent/source_adapters.py:45:from seektalent.tracing import RunTracer
src/seektalent/cli.py:21:from seektalent.artifacts import ArtifactSession, ArtifactStore
src/seektalent/evaluation.py:20:from seektalent.artifacts import ArtifactResolver, ArtifactSession
src/seektalent/runtime/reflection_runtime.py:14:from seektalent.tracing import RunTracer, json_sha256
src/seektalent/runtime/round_decision_runtime.py:19:from seektalent.tracing import RunTracer
src/seektalent/api.py:8:from seektalent.artifacts import ArtifactSession, ArtifactStore
src/seektalent/api.py:15:from seektalent.tracing import RunTracer as BaseRunTracer
src/seektalent/runtime/finalize_runtime.py:13:from seektalent.tracing import RunTracer
src/seektalent/tracing.py:13:from seektalent.artifacts import ArtifactStore
src/seektalent/tracing.py:214:class RunTracer:
src/seektalent/runtime/orchestrator.py:16:from seektalent.artifacts import ArtifactSession
src/seektalent/runtime/orchestrator.py:211:from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer
src/seektalent/runtime/retrieval_runtime.py:42:from seektalent.tracing import RunTracer
src/seektalent/runtime/controller_runtime.py:14:from seektalent.tracing import RunTracer, json_sha256
src/seektalent/runtime/candidate_intake.py:18:from seektalent.tracing import RunTracer
src/seektalent/runtime/scoring_runtime.py:18:from seektalent.tracing import RunTracer, json_char_count, json_sha256
src/seektalent/runtime/rescue_execution_runtime.py:18:from seektalent.tracing import RunTracer
src/seektalent/runtime/runtime_diagnostics.py:41:from seektalent.tracing import RunTracer, json_char_count, json_sha256
src/seektalent/runtime/post_finalize_runtime.py:11:from seektalent.tracing import RunTracer
src/seektalent/runtime/requirements_runtime.py:13:from seektalent.tracing import RunTracer
```

## Current Code Facts

- `WorkflowRuntime.run` and `WorkflowRuntime.run_async` accept `approved_requirement_sheet`, `progress_callback`, `runtime_start_callback`, and `runtime_checkpoint_callback` in `src/seektalent/runtime/orchestrator.py`.
- `WorkflowRuntime.extract_requirements` and `extract_requirements_async` exist and can be called through the executor adapter.
- `_run_rounds` is the main round loop; current progress callbacks are available around run start, search started/completed, failures, and checkpoint refresh.
- `_refresh_runtime_candidate_checkpoint` passes a `SimpleNamespace` that includes `run_state`, candidate stores, final result shell, finalization revision, and source coverage summary.
- `RequirementSheet` already has the fields required by Goal 1: `must_have_capabilities`, `preferred_capabilities`, `exclusion_signals`, `hard_constraints`, and `initial_query_term_pool`.
- `RunState` is Pydantic-backed and can be persisted as JSON for checkpoint state. It must not be exposed through agent-facing runtime-control contracts.
- `AppSettings` currently resolves workspace-relative paths through `resolve_workspace_path`; Goal 1 needs to add `runtime_control_db_path` with the same behavior.
- `SourceRegistry` exists in `src/seektalent/source_contracts/registry.py` and validates source ids without provider modules. `src/seektalent/source_adapters.py` builds the default source-enabled runtime but imports provider modules, so runtime-control business logic must not import it.
- Workbench has requirement prepare/approve/start routes and a runtime bridge that already attaches runtime run ids and refreshes candidate checkpoints. Goal 1 must link runtime-control runs to Workbench session ids rather than starting a disconnected flow.
- Workbench event rows expose `global_seq`, and `WorkbenchStore` has append helpers that can support event projection links.
- `RunTracer` and `ArtifactStore` are widely used. Artifact policy changes are red-zone work and must preserve dev-mode behavior while adding compact/off modes.

## Implementation Plan

### Stack Strategy

Use a Graphite-ready five-layer split if the diff exceeds repository governance limits or touches multiple review surfaces. Each layer must be complete, independently testable, and free of scaffold-only code.

1. Core storage and models: `src/seektalent_runtime_control/` models, SQLite migration version `1`, store methods, `AppSettings.runtime_control_db_path`, store tests, import-boundary tests.
2. Requirement lifecycle: extraction through executor boundary, draft item conversion, revision edits, free-form amendment normalization, review resolution, confirmation, requirement events/snapshots, focused requirement tests.
3. Executor, commands, events, snapshots, checkpoints: one `WorkflowRuntime` adapter boundary, executor leases, safe-boundary hooks, event transaction writes, idempotent commands, pause/cancel/resume, next-round scheduling, recovery tests.
4. Workbench bridge: runtime run/session mapping, Workbench event projection and reconciliation, Workbench compatibility tests.
5. Artifact policy, retention, and final verification: output modes, trace/artifact suppression, final summaries, retention/compaction, red-zone and full focused verification.

### Data Flow

```text
backend caller / future agent
  -> RuntimeControlService
     -> RuntimeControlStore
     -> RuntimeWorkflowExecutor
        -> WorkflowRuntime
        -> Workbench bridge
     -> events + snapshots + checkpoints + artifact refs
```

### Implementation Tasks

1. Add runtime-control public models and errors with stable reason codes, using Pydantic for externally visible DTOs and JSON-backed store payloads.
2. Add SQLite store initialization with `PRAGMA user_version = 1`, all required tables, indexes, idempotency constraints, future-version rejection, event transaction allocation, and retention methods.
3. Add `runtime_control_db_path` and runtime-control artifact mode settings to `AppSettings`, resolved through `resolve_workspace_path`.
4. Implement requirement draft conversion from `RequirementSheet`, all draft update operations, stale revision rejection, selected/default state, query term enablement, confirmation validation, and unresolved review blocking.
5. Implement free-form amendment normalization through the executor adapter, not agent-side mapping. Persist amendments, review items, rejected fragments, and provenance.
6. Implement `RuntimeWorkflowExecutor` as the only runtime-control module importing `WorkflowRuntime` and `build_source_enabled_runtime`.
7. Add runtime hook/callback payloads around required safe boundaries and runtime stages with privacy-safe summaries.
8. Implement executor leases, start acknowledgement, heartbeat, start timeout handling, checkpoint write/read/restore, and stale executor write rejection.
9. Implement command lifecycle for pause, cancel, resume, command conflicts, idempotency, supersession, safe-boundary application, and persisted command events.
10. Implement next-round requirement submission, target-round assignment before `runtime_round_input_locked`, accumulation, explicit supersession, review resolution retargeting, and activation events.
11. Implement snapshots, detail read models, final summaries, artifact refs, output-mode sink behavior, and retention/compaction without deleting protected state.
12. Implement Workbench bridge mapping, projected event links, reconciliation APIs, and tests proving runtime-control and Workbench state cannot diverge silently.
13. Add focused tests named in Goal 1 PLAN or record exact replacements in this ledger.
14. Run focused Goal 1 verification, boundary gates, lint/type checks, red-zone gate, and `git diff --check`.

### Plan Review Scope Notes

- UI/UX design review is not triggered: Goal 1 is backend/runtime control and Workbench bridge compatibility only. It must not build transcript or memory Svelte UI.
- Scope reduction is not allowed by the active Goal 1 objective. The engineering plan-review scope challenge is handled by stack splitting and strict dependency boundaries, not by dropping required acceptance criteria.
- Existing code must be reused where it already owns behavior: `RequirementSheet`, `WorkflowRuntime`, `SourceRegistry`, `WorkbenchStore`, Workbench event streams, `RunTracer`, and `ArtifactStore`.

## Plan Review Result

- Status: blocked
- Gate owner: `fw-plan-review`
- Result evidence: local engineering plan review found no blocker, but the read-only plan-review subagent returned `VERDICT: blocked`. The blocking verdict controls. Raw upstream telemetry/routing side effects were neutralized by the `fw-plan-review` safety adapter.
- Gate action: clear after rerun. Product implementation can proceed.

### Scope Challenge

- Existing code already solves parts of the problem: `WorkflowRuntime` owns extraction/execution, `RequirementSheet` owns validated runtime input shape, `SourceRegistry` validates source ids, `WorkbenchStore` owns session/event state, and `RunTracer`/`ArtifactStore` own current artifact writes.
- The plan is intentionally broad because Goal 1 acceptance requires a real runtime-control plane, not a scaffold. Scope reduction would contradict the active objective.
- Complexity risk is handled by stack-ready layers, not by dropping accepted behavior.
- No new distribution artifact is introduced.
- No `TODOS.md` file is present in the repository root, and no deferred item is required before Goal 1 implementation starts.

### Blocking Review Findings

1. Detail-read acceptance and tests are missing from Goal 1 final verification. `SPEC.md` requires grounded detail reads, and `02-agent-tool-and-requirement-contracts.md` defines `get_runtime_detail`, but `PLAN.md` acceptance/final verification does not explicitly require detail-read implementation or tests.
2. Final-summary service behavior coverage is incomplete. `prepare_final_summary` must reject active runs, be idempotent, handle stale `sourceSnapshotEventSeq`, and ground output in final snapshots/events/details without leaking unsafe facts; current plan only clearly covers persistence.
3. Workbench bridge tests are named in Phase 6 but omitted from final verification. Final Goal 1 verification must include `tests/test_runtime_control_workbench_bridge.py` or an explicitly mapped replacement.
4. Source/provider boundary verification is under-specified for `src/seektalent_runtime_control/`. The final gate must prove service modules stay provider-free, only the executor adapter imports `WorkflowRuntime` or `source_adapters`, and source validation is catalog/registry-driven rather than hard-coded to CTS/Liepin.
5. Test coverage gaps must include non-CTS/Liepin registered source ids, all detail read kinds, final-summary grounding/privacy/idempotency, Workbench event projection/replay/reconciliation, and new package import-boundary checks.
6. Stack split needs refinement: keep the five-layer grouping as high-level structure, but split out detail/final-summary behavior and split executor/events/checkpoints from command/next-round semantics for independently reviewable red/green gates.

### Plan Blocker Remediation

`PLAN.md` was updated after the blocking review to add:

- Product acceptance for `get_runtime_detail` covering all required detail kinds, source citation, Workbench-visible privacy, artifact policy, and missing-data reason codes.
- Product acceptance for `prepare_final_summary` covering terminal-only enforcement, idempotency, stale cursor handling, final-result grounding, and no invented candidate facts.
- Technical acceptance for detail-read tests, final-summary tests, runtime-control import-boundary checks, and non-CTS/Liepin source catalog coverage.
- Required focused verification entries for `tests/test_runtime_control_boundaries.py`, `tests/test_runtime_control_source_catalog.py`, `tests/test_runtime_control_detail.py`, `tests/test_runtime_control_final_summary.py`, and `tests/test_runtime_control_workbench_bridge.py`.
- Refined phases separating executor/events/checkpoints, command/next-round semantics, detail/final-summary behavior, Workbench bridge, and artifact/retention.
- Final verification command updated to include the added test files.
- Plan-review rerun result: `VERDICT: clear`; blockers: none. Non-blocking risk addressed by adding `tests/test_workbench_api.py` to final focused verification.

### Local Architecture Review

Verdict: no blocker.

Findings:

1. The service must not import `seektalent.source_adapters` because that module imports providers. Mitigation is already in the plan: only `RuntimeWorkflowExecutor` may import it.
2. Runtime safe-boundary hooks are not complete today. Mitigation is in the plan: add narrow hook/callback payloads around required boundaries and cover them with runtime-control workflow adapter tests.
3. Separate runtime-control and Workbench databases require explicit reconciliation. Mitigation is in the plan: persist mapping and projected Workbench `global_seq` links, then add reconciliation tests.

### Code Quality Review

Verdict: no blocker.

Findings:

1. `src/seektalent_runtime_control/` must stay small and literal: public contracts, store, service, executor adapter, Workbench bridge, artifact policy, and retention. Avoid new generic engine abstractions.
2. Existing `src/seektalent/` edits must be hook-only. The plan keeps new business logic outside `src/seektalent/`.
3. `RunState` checkpoint JSON is allowed internally but must never leak through public DTOs.

### Test Review

Verdict: clear with required coverage additions.

Coverage diagram:

```text
extract JD
  -> executor extraction
  -> draft rows + events + snapshot
  -> edit/amend/review/confirm
  -> approved requirement revision
  -> start run + lease + Workbench link
  -> runtime hooks
  -> events/snapshots/checkpoints
  -> commands + next-round amendments
  -> detail/final summary/retention
```

Required additions beyond existing baseline:

1. Extend import-boundary coverage so `seektalent_runtime_control` service modules cannot import `seektalent.providers` and only the executor adapter can import `seektalent.runtime.orchestrator`.
2. Add store tests for migration, idempotency, future-version rejection, event transactions, gap detection, concurrent event writes, rollback behavior, and retention.
3. Add requirement tests for default selected state, stale edit rejection, free-form normalization, review resolution, confirmation conversion, and unresolved review blocking.
4. Add command tests for idempotency, conflicts, cancel supersession, safe-boundary application, pause checkpoint, resume, and terminal command rejection.
5. Add Workbench bridge tests for run/session mapping, projected event links, and reconciliation failure reason codes.
6. Add artifact mode tests proving dev full output is preserved and compact/off modes suppress unsafe debug writes.

### Performance Review

Verdict: no blocker.

Findings:

1. Event writes must use `BEGIN IMMEDIATE` and update run cursor, event row, snapshot, and command/amendment state in one transaction to avoid cursor gaps.
2. Retention cleanup must run bounded batches and preserve active runs, pending commands, pending amendments, latest snapshots, approved requirements, command audit rows, and safe artifact refs.
3. Runtime-control reads need the indexes listed in Goal 1 SPEC for event polling, command lookup, draft lookup, target-round amendment lookup, run lookup, Workbench event links, and leases.

### NOT In Scope

- Goal 2 transcript-agent backend: deferred until Goal 1 is complete and verified.
- Memory extension: deferred until Goal 2 is complete and explicitly invoked.
- Svelte transcript or memory UI: deferred until designer-backed UI work starts.
- SaaS/cloud control plane: outside local product scope.
- Arbitrary stack-frame suspension: explicitly out of scope; commands apply at safe boundaries.
- Treating CTS/Liepin as the full source universe: forbidden; use source registry/catalog ids.

### What Already Exists

- `WorkflowRuntime.run/run_async/extract_requirements`: reused through one executor adapter.
- `RequirementSheet` and `QueryTermCandidate`: reused for approved requirement validation.
- `RunState` and checkpoint callback payloads: reused internally for checkpoint persistence.
- `SourceRegistry`: reused for source-id validation without provider imports.
- `WorkbenchStore` and event streams: reused through a bridge instead of creating a disconnected runtime UI state.
- `RunTracer` and `ArtifactStore`: reused behind output-mode policy rather than replaced.

### Failure Modes To Cover

| Flow | Failure mode | Required evidence |
| --- | --- | --- |
| Migration | Future DB version opened | Store rejects with stable reason code. |
| Event write | Insert fails after cursor allocation | Transaction rollback leaves no cursor gap. |
| Executor start | Process starts but no `runtime_executor_started` arrives | Recovery writes start-failed event or restores from checkpoint; never silently marks running. |
| Command lifecycle | Duplicate/conflicting pause/cancel/resume | Existing command returned, conflict rejected, or supersession event persisted. |
| Next-round amendment | Current round already emitted `runtime_round_input_locked` | Amendment targets a later round or rejects with `runtime_no_future_round_available`. |
| Workbench bridge | Workbench session link missing for running run | Reconciliation returns `workbench_session_missing` or `runtime_link_broken`. |
| Artifact policy | Prod compact writes raw prompt/provider payload | Artifact policy test fails; compact/off modes suppress unsafe writes. |
| Retention | Cleanup deletes active run state | Retention test proves protected state remains. |

### Parallelization And Stack Review

The five-layer split is sufficient:

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Core storage and models | `src/seektalent_runtime_control/`, `src/seektalent/config.py`, `tests/`, `tools/` | none |
| Requirement lifecycle | `src/seektalent_runtime_control/`, executor adapter tests | Core storage and models |
| Executor, commands, events, checkpoints | `src/seektalent_runtime_control/`, `src/seektalent/runtime/`, tests | Core storage and models, requirement lifecycle |
| Workbench bridge | `src/seektalent_runtime_control/`, `src/seektalent_ui/`, tests | Core storage and models, executor run mapping |
| Artifact policy and retention | `src/seektalent_runtime_control/`, `src/seektalent/tracing.py`, `src/seektalent/artifacts/store.py`, tests | Core storage and models |

Execution order: core storage first. Requirement lifecycle and artifact/retention can proceed after core APIs stabilize. Executor/commands and Workbench bridge should remain sequential because they share run/event semantics.

### Implementation Tasks From Review

- Add explicit import-boundary tests or tool checks for `seektalent_runtime_control`.
- Keep runtime-control service source validation provider-free; isolate source-enabled runtime construction in executor adapter.
- Add transactional event tests before event store implementation.
- Add Workbench reconciliation tests before final bridge wiring.
- Add dev/prod/off artifact mode tests before changing tracer/store behavior.

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |
| 2026-06-08T11:29:37Z | Continue in current checkout instead of creating a new worktree | The active branch is already feature-scoped and the goal says current worktree state is authoritative. Existing dirty files are goal-pack docs, not product code. | none |
| 2026-06-08T11:29:37Z | Use subagent-driven development selectively | The user explicitly allowed subagents. Discovery/review can run in parallel, while product edits remain sequenced to avoid overlapping writes. | none |
| 2026-06-08T11:29:37Z | Treat `source_adapters.py` as executor-adapter-only for runtime-control | It imports provider modules and constructs `WorkflowRuntime`; runtime-control service modules must stay provider-free. | planned `src/seektalent_runtime_control/` |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
| Goal 1 scope is broad and touches red-zone runtime/Workbench/artifact files | mitigated | Focused Goal 1 suite, red-zone, and dev workbench verification passed. |
| `WorkflowRuntime` currently has progress callbacks but not every required safe-boundary hook | mitigated | Narrow start/progress/checkpoint callbacks are wired through the executor adapter and covered by workflow adapter, event, checkpoint, command, and recovery tests. |
| Source registry validation could accidentally import provider modules through `source_adapters.py` | mitigated | Service validation uses source registry/catalog contracts; source adapter construction is isolated to `executor.py` and enforced by source-boundary checks. |
| Workbench and runtime-control are separate SQLite stores | mitigated | Runtime run/session mapping, projected Workbench `global_seq` links, and reconciliation reason codes are covered by bridge tests. |
| Artifact policy changes can break existing dev artifact tests | mitigated | Dev full behavior is preserved; compact/off modes are covered by artifact policy tests and final red-zone/workbench verification. |
