# Requirements Bootstrap Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the requirements/bootstrap stage out of `WorkflowRuntime` without changing `RunState`, artifact schemas, progress events, or failure semantics.

**Architecture:** Extract a plain async `build_run_state(...) -> RunState` function into `requirements_runtime.py`. Keep generic runtime shell helpers such as `_build_llm_call_snapshot`, `_write_aux_llm_call_artifact`, `_emit_llm_event`, and `_emit_progress` in `WorkflowRuntime`, and inject them explicitly.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime audit and state-flow tests

---

## File Map

- Create: `src/seektalent/runtime/requirements_runtime.py`
  Purpose: own the requirements/bootstrap stage implementation.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate `_build_run_state(...)` to the new module-level function.

- Modify only if a focused direct-output test is needed:
  - `tests/test_runtime_audit.py`
  - `tests/test_runtime_state_flow.py`

Primary validation should come from existing integration tests, not wrapper-parity tests.

## Task 1: Extract Requirements Bootstrap Execution

**Files:**
- Create: `src/seektalent/runtime/requirements_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current `_build_run_state(...)` body and copy it structurally**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth. This task is a behavior-preserving move, not a redesign.

Do not change:

- artifact file names
- prompt names
- progress event names/messages
- `RunStageError("requirement_extraction", ...)`
- success/failure branch semantics

- [ ] **Step 2: Create `requirements_runtime.py` with a plain async entrypoint**

Create:

- `src/seektalent/runtime/requirements_runtime.py`

Add a plain async function shaped roughly like:

```python
async def build_run_state(
    *,
    settings,
    requirement_extractor,
    tracer,
    job_title: str,
    jd: str,
    notes: str,
    progress_callback,
    emit_llm_event,
    emit_progress,
    build_llm_call_snapshot,
    write_aux_llm_call_artifact,
) -> RunState:
    ...
```

Move into this function:

- `build_input_truth(...)`
- requirements call payload construction
- requirements prompt rendering
- requirements-stage started/completed/failed event and progress logic
- success/failure `requirements_call.json` writing
- `repair_requirements_call.json` writing
- `RunState` initial assembly
- bootstrap artifact writes:
  - `input_truth.json`
  - `requirement_sheet.json`
  - `scoring_policy.json`
  - `sent_query_history.json`

Keep this module free of classes.

- [ ] **Step 3: Update `WorkflowRuntime._build_run_state(...)` to a thin delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the current large method body with a thin call into `requirements_runtime.build_run_state(...)`.

The method should continue to exist so current callers and tests do not need to change.

Do not move:

- `_write_run_preamble`
- `_build_llm_call_snapshot`
- `_write_aux_llm_call_artifact`
- `_emit_llm_event`
- `_emit_progress`

- [ ] **Step 4: Run focused existing tests that cover requirements bootstrap**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py -q
```

Expected: PASS.

These files already cover:

- `requirements_call.json`
- `repair_requirements_call.json`
- `requirement_sheet.json`
- failure-path requirements call artifacts
- direct `_build_run_state(...)` call sites

- [ ] **Step 5: Add a focused direct-output test only if coverage is missing**

Only if the extraction exposes a gap not already protected by existing tests, add one small test that directly checks observable outcomes of the extracted module function.

Allowed:

- assert `RunState.input_truth`
- assert `RunState.requirement_sheet`
- assert tracer artifacts written to disk
- assert failure semantics

Not allowed:

- `direct == wrapper` seam tests

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/requirements_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py
git commit -m "refactor: extract requirements bootstrap runtime"
```

## Task 2: Focused Regression And Import Sweep

**Files:**
- Modify: only if a stale import or helper reference remains
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Run the planned focused regression set**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_api.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix only import/helper drift if needed**

If a failure appears:

- inspect whether it is caused by a stale import
- inspect whether a helper path changed
- fix the smallest thing necessary

Do not expand the scope into controller/reflection/finalizer refactors.

- [ ] **Step 3: Re-run the same regression command**

Run the same command again and confirm it passes before claiming completion.

- [ ] **Step 4: Commit follow-up only if needed**

If Task 2 required code changes:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/requirements_runtime.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_api.py tests/test_cli.py
git commit -m "test: fix requirements bootstrap runtime follow-ups"
```

If no changes were needed, do not create an extra commit.

## Notes For Reviewers

Reviewers should check:

- `_build_run_state(...)` is now thin
- `requirements_runtime.py` owns the stage-specific success/failure logic
- no artifact schema drift occurred
- no prompt wording changed
- no wrapper-parity seam test was introduced

## Done Criteria

This plan is complete when:

- `requirements_runtime.py` exists
- `WorkflowRuntime._build_run_state(...)` is reduced to delegation
- runtime audit/state-flow/API/CLI focused regression passes
- requirements-stage artifacts and failure semantics remain unchanged
