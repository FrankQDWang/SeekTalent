# Goal 1 Runtime Control Plane Plan

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `goal-1-runtime-control-plane/05-acceptance-criteria.md`
- `goal-1-runtime-control-plane/06-implementation-sequence.md`
- `goal-1-runtime-control-plane/07-execution-control.md`

---

## Source: `goal-1-runtime-control-plane/05-acceptance-criteria.md`

## Goal 1 Acceptance Criteria

### Product Acceptance

1. A backend caller can submit JD text and receive a persisted requirement draft with all extracted items selected.
2. Draft sections cover `must_have_capabilities`, `preferred_capabilities`, `hard_constraints`, `exclusion_signals`, and `initial_query_term_pool`.
3. A caller can select, unselect, edit, delete, move supported items, and enable/disable query terms.
4. A caller can submit free-form extra requirements and receive normalized draft items from Workflow Runtime parsing.
5. Free-form additions keep raw text provenance and are selected by default after normalization.
6. Ambiguous free-form additions require review before confirmation.
7. Every draft edit creates a new revision and stale base revisions are rejected.
8. Review-required additions can be accepted, edited, moved, or rejected through `resolve_requirement_review`.
9. Confirmation creates an approved `RequirementSheet` containing only selected active resolved items.
10. Stale edit, review resolution, and confirmation requests return the latest draft payload.
11. A workflow can start from an approved requirement revision.
12. Duplicate start for the same approved revision is idempotent.
13. Executor start requires a persisted `runtime_executor_started` event before the run is visible as running.
14. Executor start, resume, heartbeat, and stale-write rejection are governed by runtime-control executor leases.
15. Runtime events are persisted, ordered, contiguous, and gap-detected.
16. Snapshots expose current status, current stage, current round, latest event cursor, candidate counts when available, pending commands, and artifact refs.
17. Pause requests are persisted, reported as accepted or pending, and applied at the next safe boundary.
18. Cancel requests are persisted, reported as accepted or pending, supersede pending lifecycle commands, and apply at the next safe boundary.
19. Resume starts from a persisted paused checkpoint.
20. Lifecycle command conflicts return stable reason codes and do not create ambiguous pending commands.
21. Next-round requirement amendments are persisted and applied only before `runtime_round_input_locked` for their target round.
22. Multiple next-round amendments for the same target round accumulate unless explicitly replaced or withdrawn.
23. Runtime detail reads support round query, source result, candidate score, reflection, final candidate, command, and checkpoint questions with source event/checkpoint/artifact references.
24. Runtime detail reads apply Workbench-visible privacy rules, redact unsafe artifact payloads, and return stable reason codes for missing or unavailable backing data.
25. Final summary preparation is available only for terminal runs, is idempotent by key, rejects stale source snapshot cursors, and reads final runtime result plus user instruction.
26. Final summaries are grounded in final snapshots, events, permitted detail read models, and Workbench-visible candidate facts without inventing candidate facts.
27. Production compact mode does not write full debug artifacts by default.
28. Runtime-control retention and compaction protect terminal run storage without deleting active runs or required audit state.
29. Running next-round requirement amendments never mutate the already-locked current round's requirement revision.
30. Running next-round requirement amendments apply only before `runtime_round_input_locked` for the target round.

### Technical Acceptance

1. Runtime-control service modules do not import provider-specific modules.
2. Only the executor adapter imports `WorkflowRuntime`.
3. Agent-facing contracts do not expose `RunState`.
4. New runtime-control business logic lives under `src/seektalent_runtime_control/`, not under `src/seektalent/`.
5. Existing `src/seektalent/` changes are limited to hooks, adapter seams, and artifact/tracing policy integration.
6. `runtime_control_db_path` is configured through `AppSettings` and resolved through workspace-root rules.
7. SQLite initialization and migration behavior is tested.
8. Run start idempotency is enforced by a database invariant.
9. Command idempotency is tested.
10. Command conflict, duplicate, and supersession behavior is tested.
11. Event ordering, event gap detection, concurrent event writes, and event transaction rollback behavior are tested.
12. Runtime-control events store Workbench event references when projected.
13. `runtime_round_input_locked` is emitted before every round controller reads requirements.
14. Snapshot replacement and cursor behavior is tested.
15. Checkpoint write/read/restore is tested.
16. Executor start timeout and recovery behavior is tested.
17. Executor lease uniqueness, heartbeat, and stale executor write rejection are tested.
18. Artifact output modes are tested.
19. Runtime-control event, checkpoint, final-summary, and payload retention is tested.
20. Workbench session mapping and reconciliation is tested.
21. Runtime detail read behavior, source citation, privacy redaction, missing backing data, and `includeArtifacts` policy are tested.
22. Final summary service behavior, terminal-only enforcement, idempotency, stale cursor handling, grounding, and privacy behavior are tested.
23. Runtime-control package import-boundary checks prove service modules do not import providers, source adapters, or runtime internals, and only the executor adapter imports `WorkflowRuntime`.
24. Source catalog tests include at least one non-CTS/Liepin registered source id so runtime control cannot treat current fixtures as the full source universe.
25. Source boundary checks pass.
26. Tach baseline check passes.
27. Architecture import check passes.
28. Red-zone gate passes if red-zone files are touched.

### Required Focused Verification

Run and record:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_boundaries.py tests/test_runtime_control_source_catalog.py tests/test_runtime_control_requirements.py tests/test_runtime_control_requirement_amendments.py tests/test_runtime_control_requirement_review.py tests/test_runtime_control_workflow_adapter.py tests/test_runtime_control_events.py tests/test_runtime_control_recovery.py tests/test_runtime_control_checkpoints.py tests/test_runtime_control_commands.py tests/test_runtime_control_next_round_requirements.py tests/test_runtime_control_detail.py tests/test_runtime_control_final_summary.py tests/test_workbench_api.py tests/test_runtime_control_workbench_bridge.py tests/test_runtime_control_artifact_policy.py tests/test_runtime_control_retention.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
scripts/verify-red-zone.sh
git diff --check
```

If test file names change during implementation, the progress ledger must map the replacement test files to the acceptance criteria above.

### Completion Evidence

The Goal 1 final packet must list:

- changed backend packages and why each was touched;
- new or changed database schema version;
- runtime-control public API operations implemented;
- command semantics implemented;
- detail read models implemented;
- final-summary grounding and idempotency implemented;
- Workbench mapping/reconciliation implemented;
- artifact modes implemented;
- retention and compaction implemented;
- focused verification output;
- remaining risks, if any.

---

## Source: `goal-1-runtime-control-plane/06-implementation-sequence.md`

## Goal 1 Implementation Sequence

### Phase 1: Preflight And Boundary Baseline

1. Run shared preflight from `../04-operating-policies-and-runtime-contracts.md`.
2. Read current `WorkflowRuntime`, `RequirementSheet`, Workbench bridge, tracer, artifact store, and source adapter surfaces.
3. Record current branch, HEAD, dirty state, stashes, and boundary-check results in `progress.md`.
4. Stop if source boundaries are already failing for reasons unrelated to this goal.

Verification:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
```

### Phase 2: Store And Models

1. Create `src/seektalent_runtime_control/`.
2. Add typed models for requirement drafts, draft operations, approved requirements, run records, commands, events, snapshots, checkpoints, artifact refs, and final summaries.
3. Add SQLite store and migration version `1`.
4. Add store tests for initialization, idempotency, future-version rejection, JSON round trips, event ordering, event gap detection, concurrent event writes, transaction rollback, final-summary rows, and retained artifact refs.
5. Add import-boundary and source-catalog tests proving runtime-control service modules are provider-free, only the executor adapter imports runtime/source adapters, and non-CTS/Liepin source ids can be validated through registry/catalog contracts.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_boundaries.py tests/test_runtime_control_source_catalog.py -q
```

### Phase 3: Requirement Draft Service

1. Add requirement extraction service method that calls the existing runtime extraction path through the executor boundary.
2. Convert `RequirementSheet` to itemized draft sections.
3. Implement draft update operations.
4. Implement free-form amendment normalization through Workflow Runtime requirement parsing.
5. Implement review-required amendment resolution.
6. Implement stale draft rejection for edit, review resolution, and confirmation.
7. Implement confirmation back to `RequirementSheet`.
8. Emit requirement events and snapshots.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_requirements.py tests/test_runtime_control_requirement_amendments.py tests/test_runtime_control_requirement_review.py -q
```

### Phase 4: Runtime Executor, Events, And Checkpoints

1. Add executor adapter for `WorkflowRuntime`.
2. Add executor lease acquisition, heartbeat, stale-write rejection, start acknowledgement, and timeout recovery.
3. Add runtime hooks or callback adapters around existing stages.
4. Persist lifecycle, round, source, scoring, reflection, finalization, failure, and checkpoint events.
5. Ensure event summaries come from real runtime state.
6. Keep event payloads privacy-safe.
7. Make event writes atomic with snapshot and command/amendment state updates.
8. Persist checkpoint payloads with `RunState` JSON, source plan, pending commands, stage, round, schema version, and artifact manifest refs for resume.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_workflow_adapter.py tests/test_runtime_control_events.py tests/test_runtime_control_recovery.py tests/test_runtime_control_checkpoints.py -q
```

### Phase 5: Commands And Safe Boundaries

1. Add command creation and idempotency.
2. Check pending commands at every safe boundary.
3. Persist command accepted, pending, applied, rejected, and superseded events.
4. Implement pause checkpoint behavior.
5. Implement resume from paused checkpoint.
6. Implement cancel at safe boundary.
7. Implement lifecycle command conflict rules.
8. Implement next-round requirement amendment accumulation and explicit supersession.
9. Implement next-round requirement amendment application.
10. Keep next-round requirement amendments inactive until `runtime_requirement_revision_activated`.
11. Reject or retarget resolved running amendments when the original target round has already emitted `runtime_round_input_locked`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_commands.py tests/test_runtime_control_next_round_requirements.py -q
```

### Phase 6: Detail Reads And Final Summary

1. Implement `get_runtime_detail` for `round_query`, `source_result`, `candidate_score`, `reflection`, `final_candidate`, `command`, and `checkpoint`.
2. Cite backing runtime event ids, checkpoint ids, safe artifact refs, or Workbench-visible record ids in every detail response.
3. Enforce privacy redaction and `includeArtifacts` policy for detail responses.
4. Return stable reason codes for missing event, missing checkpoint, unavailable artifact, or broken Workbench backing data.
5. Implement `prepare_final_summary` as an idempotent service operation.
6. Reject active runs with `runtime_run_not_completed`.
7. Reject stale `sourceSnapshotEventSeq` by returning the latest terminal cursor.
8. Ground summary facts in terminal snapshot, events, permitted detail models, Workbench-visible candidate facts, and user instruction.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_detail.py tests/test_runtime_control_final_summary.py -q
```

### Phase 7: Workbench Bridge

1. Link runtime run id to Workbench session id.
2. Adapt existing Workbench requirement prepare/approve/start flows to call or map runtime-control records where needed.
3. Preserve existing Workbench routes and event streams.
4. Add reconciliation for runtime run, Workbench session, approved requirement revision, and projected event seq links.
5. Persist projected Workbench `global_seq` values on runtime-control events.
6. Add replay protection so projecting the same runtime-control event does not duplicate Workbench events.
7. Add integration tests proving no silent divergence and stable `workbench_session_missing` / `runtime_link_broken` reason codes.

Verification:

```bash
uv run --group dev python -m pytest tests/test_workbench_api.py tests/test_runtime_control_workbench_bridge.py -q
```

### Phase 8: Artifact Policy And Retention

1. Add output-mode configuration.
2. Route tracer/artifact writes through policy.
3. Preserve development artifact behavior.
4. Suppress full debug writes in compact production and DB-only modes.
5. Keep final result and error summary available.
6. Add runtime-control retention and compaction service from `../04-operating-policies-and-runtime-contracts.md`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_artifact_policy.py -q
uv run --group dev python -m pytest tests/test_runtime_control_retention.py -q
```

### Phase 9: Full Goal Verification

Run the focused commands from `PLAN.md`, then run broader tests affected by touched files.

Record final evidence in `progress.md`, including the exact completion statement required by `../MANIFEST.md`.

---

## Source: `goal-1-runtime-control-plane/07-execution-control.md`

## Goal 1 Execution Control

### Progress Ledger

Use:

```text
conversational-agent-runtime-goal-pack/goal-1-runtime-control-plane/progress.md
```

Create the ledger before product edits. Keep it current after every phase.

### Goal 1 Preflight

Run:

```bash
pwd
git branch --show-current
git rev-parse HEAD
git rev-parse --verify origin/main || echo "MISSING origin/main; fetch before final verification"
git merge-base HEAD origin/main || echo "MISSING merge-base with origin/main"
git status --short --untracked-files=all
git stash list
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
rg -n "class WorkflowRuntime|def run\\(|def run_async|def extract_requirements|def _run_rounds|def _refresh_runtime_candidate_checkpoint" src/seektalent/runtime/orchestrator.py
rg -n "class RequirementSheet|class QueryTermCandidate|class RunState|class RetrievalState" src/seektalent/models.py
rg -n "extract_requirement_review|run_runtime_sourcing_job" src/seektalent_ui/runtime_bridge.py
rg -n "RunTracer|ArtifactStore|ArtifactSession" src/seektalent src/seektalent_ui tests
```

### Ledger Template

```markdown
# Runtime Control Plane Progress

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
- Status:
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

### Stop Conditions

Stop before edits when:

- source boundary checks fail before Goal 1 changes;
- `WorkflowRuntime` signature has changed so approved requirement injection is no longer available;
- the source registry/catalog cannot validate source ids without provider imports;
- Workbench session creation cannot be linked to runtime-control runs;
- artifact/tracer construction cannot accept output policy without broad unrelated rewrites.

### Final Goal 1 Verification

Run:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_boundaries.py tests/test_runtime_control_source_catalog.py tests/test_runtime_control_requirements.py tests/test_runtime_control_requirement_amendments.py tests/test_runtime_control_requirement_review.py tests/test_runtime_control_workflow_adapter.py tests/test_runtime_control_events.py tests/test_runtime_control_recovery.py tests/test_runtime_control_checkpoints.py tests/test_runtime_control_commands.py tests/test_runtime_control_next_round_requirements.py tests/test_runtime_control_detail.py tests/test_runtime_control_final_summary.py tests/test_workbench_api.py tests/test_runtime_control_workbench_bridge.py tests/test_runtime_control_artifact_policy.py tests/test_runtime_control_retention.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
scripts/verify-red-zone.sh
git diff --check
```

### Required Final Packet

The final response or release-readiness packet must include:

```text
This PR completes the runtime control plane goal. It is a complete local runtime-control implementation for the agreed scope.
```
