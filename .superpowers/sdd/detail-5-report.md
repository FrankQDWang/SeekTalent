# Detail-5 Report: Concrete OpenCLI and Checkpoint Regression Boundaries

## Status

Test-only verification is implemented. No production code, public API, runtime
resume API, generic worker/HTTP contract, BFF, or approved-detail path changed.

## Coverage added

1. `test_concrete_opencli_private_chain_opens_same_subject_once_across_queries_and_rounds`
   composes the real `LiepinOpenCliWorkerClient` and
   `LiepinOpenCliResumeRetriever` with a deterministic private runner/site and
   real `LiepinSearchWorkflow`. Two logical queries in round 1 and a later
   round 2 see the same canonical subject through one shared
   `DetailOpenClaimLedger`. It proves exactly one cached detail open, an
   `opened` opaque claim, skipped later sightings, and sanitized source-lane
   public output (no raw subject, ref, URL parameter, or claim key).
2. The existing runtime checkpoint regression now snapshots an `opened` claim,
   JSON-dumps and revalidates `RunState`, then builds a new ledger around the
   rehydrated map and proves the same key cannot be claimed. This is explicitly
   persistence/rehydration coverage, not a claim that `WorkflowRuntime` resumes
   from a checkpoint.
3. The workflow-adapter callback fixture now persists an explicit claim map in
   `RuntimeControlStore`; the user-facing checkpoint detail projection is
   asserted not to contain either the opaque key or the claim-map field.
4. `tests/test_liepin_detail_ledger.py` remains untouched and runs as the
   approved-detail daily-ledger independence regression.

## Test-first evidence

Detail-5 is a verification-only task executed after the Detail-1–4 production
contracts were already present. Each new boundary test was added before any
production action and run immediately. All were green on first execution, so
there was no missing production behavior to fix and no artificial RED failure
was manufactured.

```text
uv run pytest -q \
  tests/test_liepin_runtime_source_lane.py::test_concrete_opencli_private_chain_opens_same_subject_once_across_queries_and_rounds
1 passed in 1.17s

uv run pytest -q \
  tests/test_runtime_multi_source_round_dispatch.py::test_runtime_checkpoint_persistence_rehydrates_opened_claim_without_private_ledger_payload
1 passed in 1.77s

uv run pytest -q \
  tests/test_runtime_control_workflow_adapter.py::test_workflow_adapter_persists_private_detail_claim_map_without_exposing_checkpoint_detail
1 passed in 1.19s
```

## Focused verification

```text
uv run pytest -q \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_control_workflow_adapter.py \
  tests/test_liepin_detail_ledger.py
110 passed in 2.50s

uv run ruff check \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_control_workflow_adapter.py
All checks passed!

uv run ty check \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_control_workflow_adapter.py
All checks passed!

uv run python tools/check_arch_imports.py
passed

git diff --check
passed
```

## Independent-review correction

The independent review correctly found two assertion gaps. The checkpoint test
now directly verifies the rehydrated opaque claim is `opened`, retains one
browser-open attempt, and has no terminal-failure reason before it asserts a
new ledger denies the same key. The concrete chain test now checks synthetic
`finalize` safe counts for all three logical executions: the first grants and
opens once, while both later executions report exactly one seen-skip and no
grant/open.

The strengthened concrete-chain assertion produced the expected RED signal:

```text
uv run pytest -q \
  tests/test_runtime_multi_source_round_dispatch.py::test_runtime_checkpoint_persistence_rehydrates_opened_claim_without_private_ledger_payload \
  tests/test_liepin_runtime_source_lane.py::test_concrete_opencli_private_chain_opens_same_subject_once_across_queries_and_rounds
1 failed, 1 passed
```

Root cause: source compilation appends `:target:1` to each concrete lane run
ID. The behavior and counts were correct; the new test expected the pre-target
ID. Updating that test-only expectation yielded:

```text
2 passed in 1.41s
```

Post-correction relevant verification:

```text
uv run pytest -q \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_control_workflow_adapter.py \
  tests/test_liepin_detail_ledger.py
175 passed in 6.40s

uv run ruff check [three Detail-5 test files]
All checks passed!

uv run ty check [three Detail-5 test files]
All checks passed!

uv run python tools/check_arch_imports.py
passed

git diff --check -- [Detail-5 paths]
passed
```

The full suite remains intentionally deferred to the coordinated shared-tree
gate while Logical-5 has uncommitted work.

## Shared-tree source-boundary gate

The shared worktree gate was intentionally recorded separately from Detail-5:

```text
uv run python tools/check_source_boundaries.py
exit 1
```

- The three findings in `runtime/normalized_artifacts.py:8` and
  `runtime/orchestrator.py:112` predate this task.
- A fourth finding at `runtime/public_events.py:94` was introduced by the
  concurrent, uncommitted Logical-5 work; it is outside this commit and has
  been handed to that owner.
- None of the Detail-5 test-only files participates in a source-boundary
  finding.

## Shared-worktree isolation

Logical-5 was concurrently editing its owned runtime, Runtime Control, UI, and
Workbench test files. Detail-5 changes only:

- `tests/test_liepin_runtime_source_lane.py`
- `tests/test_runtime_multi_source_round_dispatch.py`
- `tests/test_runtime_control_workflow_adapter.py`
- this report

The eventual commit must use an explicit pathspec for those files only. Full
suite and source-boundary evidence will be appended once the concurrent
Logical-5 worktree is quiescent.
