# Round Decision Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the controller-output resolution logic out of `_run_rounds(...)` without changing rescue behavior, stop behavior, force-continue behavior, or retrieval planning semantics.

**Architecture:** Extract plain runtime functions into `round_decision_runtime.py` for controller-decision sanitization and post-controller decision resolution only. Keep `WorkflowRuntime` responsible for controller invocation, controller artifacts/events, retrieval planning, and the overall round loop.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime state-flow and audit tests

---

## File Map

- Create: `src/seektalent/runtime/round_decision_runtime.py`
  Purpose: own controller-output resolution and its direct helper functions.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate post-controller decision resolution to the new runtime module.

- Modify only if a minimal boundary test is needed:
  - `tests/test_runtime_state_flow.py`

Primary validation should rely on existing round/state-flow and rescue coverage rather than wrapper-parity tests.

## Task 1: Extract Round Decision Resolution

**Files:**
- Create: `src/seektalent/runtime/round_decision_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current decision-resolution segment structurally**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth. This is a behavior-preserving move.

Move together:

- `_sanitize_controller_decision(...)`
- `_reflection_backed_inactive_terms(...)`
- `_sanitize_premature_max_round_claim(...)`
- `_force_continue_decision(...)`
- the controller-output resolution segment inside `_run_rounds(...)`

That segment includes:

- rescue-routing entry
- rescue lane dispatch and forced decision overrides
- stop-not-allowed fallback
- rescue artifact write decision

Do not change:

- controller invocation behavior
- controller artifact names or payload shapes
- retrieval planning behavior
- rescue behavior
- stop behavior

- [ ] **Step 2: Create `round_decision_runtime.py` with a plain async entrypoint**

Create:

- `src/seektalent/runtime/round_decision_runtime.py`

Add a plain async function shaped roughly like:

```python
async def resolve_round_decision(
    *,
    run_state: RunState,
    round_no: int,
    controller_context: ControllerContext,
    controller_decision: ControllerDecision,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    choose_rescue_decision,
    force_broaden_decision,
    force_candidate_feedback_decision,
    continue_after_empty_feedback,
    force_company_discovery_decision,
    select_anchor_only_after_failed_company_discovery,
    force_anchor_only_decision,
    write_rescue_decision,
) -> tuple[ControllerDecision, RescueDecision | None]:
    ...
```

Keep the direct helper functions in the same module:

- `sanitize_controller_decision(...)`
- `reflection_backed_inactive_terms(...)`
- `sanitize_premature_max_round_claim(...)`
- `force_continue_decision(...)`

Keep this module free of classes.

- [ ] **Step 3: Update `_run_rounds(...)` to use a thin decision-resolution delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the current post-controller branching segment with a call into `round_decision_runtime.resolve_round_decision(...)`.

Keep these in `WorkflowRuntime`:

- controller invocation and error handling
- controller/repair artifact writes
- `_emit_llm_event(...)`
- `_emit_progress(...)`
- `_choose_rescue_decision(...)`
- `_write_rescue_decision(...)`
- retrieval planning and `_build_round_query_states(...)`
- the broader `_run_rounds(...)` control-flow skeleton

Do not broaden this into a controller-stage or round-loop refactor.

- [ ] **Step 4: Run focused existing tests that already cover round decision behavior**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py -q
```

Expected: PASS.

These files already cover:

- rescue and stop behavior
- rescue artifact writes
- round-level decision outcomes

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small test that checks host-boundary behavior, for example:

- `WorkflowRuntime` delegates decision resolution to the new runtime host

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/round_decision_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py
git commit -m "refactor: extract round decision runtime"
```

## Task 2: Focused Regression And Drift Sweep

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

Do not expand scope into controller invocation, rescue execution, company discovery, or retrieval planning.

- [ ] **Step 3: Re-run the same regression command**

Run the same command again and confirm it passes before claiming completion.

- [ ] **Step 4: Commit follow-up only if needed**

If Task 2 required code changes:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/round_decision_runtime.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py
git commit -m "test: fix round decision runtime follow-ups"
```

If no changes were needed, do not create an extra commit.

## Notes For Reviewers

Reviewers should check:

- `_run_rounds(...)` is thinner immediately after controller invocation
- `round_decision_runtime.py` owns controller-output resolution helpers
- rescue behavior and rescue artifact writes remain unchanged
- force-continue behavior remains unchanged
- no wrapper-parity seam test was introduced

## Done Criteria

This plan is complete when:

- `round_decision_runtime.py` exists
- `WorkflowRuntime` delegates controller-output resolution to the new module
- runtime state-flow/audit/API/CLI focused regression passes
- rescue behavior, stop behavior, and force-continue behavior remain unchanged
