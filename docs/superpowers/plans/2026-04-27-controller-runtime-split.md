# Controller Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the controller invocation shell out of `_run_rounds(...)` without changing controller behavior, controller artifacts, repair handling, progress/event semantics, or downstream decision-resolution behavior.

**Architecture:** Extract plain runtime functions into `controller_runtime.py` for controller prompt/render, invocation, artifact wiring, and progress/event shell behavior only. Keep `WorkflowRuntime` responsible for controller context construction, decision resolution, retrieval planning, and the overall round loop.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime state-flow, audit, and controller-contract tests

---

## File Map

- Create: `src/seektalent/runtime/controller_runtime.py`
  Purpose: own controller-stage invocation shell logic.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate controller invocation shell to the new runtime module.

- Modify only if a minimal boundary test is needed:
  - `tests/test_runtime_state_flow.py`
  - `tests/test_controller_contract.py`

Primary validation should rely on existing runtime/audit/controller-contract coverage rather than wrapper-parity tests.

## Task 1: Extract Controller Invocation Shell

**Files:**
- Create: `src/seektalent/runtime/controller_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current controller stage shell structurally**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth. This is a behavior-preserving move.

Move together:

- controller prompt rendering
- prompt cache key/retention selection
- controller invocation
- `controller_call.json` success/failure snapshot wiring
- `repair_controller_call.json` wiring
- `controller_failed` / `controller_completed` LLM events
- `controller_failed` / `controller_completed` progress emission
- `RunStageError("controller", ...)` failure handling

Do not change:

- controller behavior
- controller artifact names or payload shapes
- repair handling behavior
- decision-resolution behavior
- retrieval planning behavior

- [ ] **Step 2: Create `controller_runtime.py` with a plain async entrypoint**

Create:

- `src/seektalent/runtime/controller_runtime.py`

Add a plain async function shaped roughly like:

```python
async def run_controller_stage(
    *,
    settings: AppSettings,
    controller,
    controller_context: ControllerContext,
    round_no: int,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot,
    write_aux_llm_call_artifact,
    emit_llm_event,
    emit_progress,
    prompt_cache_key,
) -> ControllerDecision:
    ...
```

Keep any direct shell-only helpers in the same module.

Keep this module free of classes.

- [ ] **Step 3: Update `_run_rounds(...)` to use a thin controller-stage delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the inline controller invocation shell with a call into `controller_runtime.run_controller_stage(...)`.

Keep these in `WorkflowRuntime`:

- `build_controller_context(...)` call site
- `round_decision_runtime.resolve_round_decision(...)`
- `_build_llm_call_snapshot(...)`
- `_write_aux_llm_call_artifact(...)`
- `_emit_llm_event(...)`
- `_emit_progress(...)`
- retrieval planning and later stages
- the broader `_run_rounds(...)` control-flow skeleton

Do not broaden this into a controller-stage or round-loop framework.

- [ ] **Step 4: Run focused existing tests that already cover controller shell behavior**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_controller_contract.py -q
```

Expected: PASS.

These files already cover:

- controller success/failure paths
- controller artifacts
- controller-contract behavior
- downstream decision outcomes

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small test that checks host-boundary behavior, for example:

- `WorkflowRuntime` delegates controller invocation to the new runtime host

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/controller_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_controller_contract.py
git commit -m "refactor: extract controller runtime"
```

## Task 2: Focused Regression And Drift Sweep

**Files:**
- Modify: only if a stale import or helper reference remains
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_controller_contract.py`

- [ ] **Step 1: Run the planned focused regression set**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_controller_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix only stale import/helper drift if needed**

If a failure appears:

- inspect whether it is a stale import
- inspect whether a helper reference changed host modules
- fix the smallest thing necessary

Do not expand scope into round-decision resolution, rescue execution, company discovery, or retrieval planning.

- [ ] **Step 3: Re-run the same regression command**

Run the same command again and confirm it passes before claiming completion.

- [ ] **Step 4: Commit follow-up only if needed**

If Task 2 required code changes:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/controller_runtime.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_controller_contract.py
git commit -m "test: fix controller runtime follow-ups"
```

If no changes were needed, do not create an extra commit.

## Notes For Reviewers

Reviewers should check:

- `_run_rounds(...)` is thinner before round-decision resolution
- `controller_runtime.py` owns controller invocation shell logic
- controller artifacts and repair artifacts remain unchanged
- controller failure semantics remain unchanged
- no wrapper-parity seam test was introduced

## Done Criteria

This plan is complete when:

- `controller_runtime.py` exists
- `WorkflowRuntime` delegates controller invocation shell to the new module
- runtime state-flow/audit/API/CLI/controller-contract focused regression passes
- controller behavior, controller artifacts, repair handling, and event/progress semantics remain unchanged
