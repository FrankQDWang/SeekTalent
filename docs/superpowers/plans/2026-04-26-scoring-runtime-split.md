# Scoring Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the scoring execution stage out of `WorkflowRuntime` without changing scoring artifacts, tuple return values, scorer behavior, or round-loop semantics.

**Architecture:** Extract a plain async `score_round(...)` function plus its direct helper functions into `scoring_runtime.py`. Keep `WorkflowRuntime` as the round-level coordinator and leave broader orchestration, progress payloads, and post-scoring flow in `orchestrator.py`.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime state-flow and audit tests

---

## File Map

- Create: `src/seektalent/runtime/scoring_runtime.py`
  Purpose: own scoring-stage execution and its direct helper functions.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate `_score_round(...)` to the new scoring runtime module.

- Modify only if a minimal boundary test is needed:
  - `tests/test_runtime_state_flow.py`

Primary validation should rely on existing state-flow and audit coverage rather than wrapper-parity tests.

## Task 1: Extract Scoring Stage Execution

**Files:**
- Create: `src/seektalent/runtime/scoring_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current scoring-stage implementation structurally**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth. This is a behavior-preserving move.

Move together:

- `_score_round(...)`
- `_build_scoring_pool(...)`
- `_normalize_scoring_pool(...)`
- `_build_pool_decisions(...)`
- `_scoring_input_ref(...)`

Do not change:

- `scorecards.jsonl`
- `top_pool_snapshot.json`
- tuple return shape
- scorer invocation shape
- error semantics for scoring failures

- [ ] **Step 2: Create `scoring_runtime.py` with a plain async entrypoint**

Create:

- `src/seektalent/runtime/scoring_runtime.py`

Add a plain async function shaped roughly like:

```python
async def score_round(
    *,
    round_no: int,
    new_candidates: list[ResumeCandidate],
    run_state: RunState,
    tracer: RunTracer,
    runtime_only_constraints: list[RuntimeConstraint],
    resume_scorer,
    build_scoring_context,
    format_scoring_failure_message,
    slim_top_pool_snapshot,
) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
    ...
```

Put these helpers in the same module:

- `build_scoring_pool(...)`
- `normalize_scoring_pool(...)`
- `build_pool_decisions(...)`
- `scoring_input_ref(...)`

Keep this module free of classes.

- [ ] **Step 3: Update `WorkflowRuntime._score_round(...)` to a thin delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the current `_score_round(...)` body with a thin call into `scoring_runtime.score_round(...)`.

The method should continue to exist so current callers and tests do not need to change.

Do not move:

- `_format_scoring_failure_message(...)`
- `_materialize_candidates(...)`
- round-loop logic around when scoring runs

- [ ] **Step 4: Run focused existing tests that already cover scoring behavior**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py -q
```

Expected: PASS.

These files already cover:

- direct `_score_round(...)` behavior
- `scorecards.jsonl`
- `top_pool_snapshot.json`
- downstream scoring counts and top-pool effects

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small test that checks host-boundary behavior, for example:

- `WorkflowRuntime` delegates `_score_round(...)` to the new scoring host

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/scoring_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py
git commit -m "refactor: extract scoring runtime"
```

## Task 2: Focused Regression And Import Sweep

**Files:**
- Modify: only if a stale import or helper reference remains
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Run the planned focused regression set**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix only stale import/helper drift if needed**

If a failure appears:

- inspect whether it is a stale import
- inspect whether a helper reference changed host modules
- fix the smallest thing necessary

Do not expand scope into reflection, resume quality comment, or `_run_rounds(...)`.

- [ ] **Step 3: Re-run the same regression command**

Run the same command again and confirm it passes before claiming completion.

- [ ] **Step 4: Commit follow-up only if needed**

If Task 2 required code changes:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/scoring_runtime.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py
git commit -m "test: fix scoring runtime follow-ups"
```

If no changes were needed, do not create an extra commit.

## Notes For Reviewers

Reviewers should check:

- `_score_round(...)` is now thin
- `scoring_runtime.py` owns the scoring-stage execution helpers
- scoring artifact schemas remain unchanged
- no resume quality comment logic moved accidentally
- no wrapper-parity seam test was introduced

## Done Criteria

This plan is complete when:

- `scoring_runtime.py` exists
- `WorkflowRuntime._score_round(...)` is reduced to delegation
- runtime state-flow/audit/API/CLI focused regression passes
- scoring tuple results and scoring artifacts remain unchanged
