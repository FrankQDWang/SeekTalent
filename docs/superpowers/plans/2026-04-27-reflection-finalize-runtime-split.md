# Reflection and Finalize Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the remaining reflection and finalizer invocation shells out of `WorkflowRuntime` without changing behavior, artifact schemas, progress/event semantics, or post-stage orchestration.

**Architecture:** Introduce two plain runtime modules:

- `reflection_runtime.py` for the reflection-stage shell inside `_run_rounds(...)`
- `finalize_runtime.py` for the finalizer-stage shell inside `run_async(...)`

Implement them as two separate tasks with two separate commits. Do not introduce a generic stage framework.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime state-flow and audit coverage

---

## File Map

### Reflection task

- Create: `src/seektalent/runtime/reflection_runtime.py`
  Purpose: own the reflection invocation shell.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate reflection shell work to the new runtime module.

- Modify only if a minimal seam test is needed:
  - `tests/test_runtime_state_flow.py`
  - `tests/test_runtime_audit.py`
  - `tests/test_llm_input_prompts.py`

### Finalize task

- Create: `src/seektalent/runtime/finalize_runtime.py`
  Purpose: own the finalizer invocation shell.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate finalizer shell work to the new runtime module.

- Modify only if a minimal seam test is needed:
  - `tests/test_runtime_audit.py`
  - `tests/test_api.py`
  - `tests/test_cli.py`

Primary validation should continue to rely on existing observable artifact/state-flow coverage rather than wrapper-parity tests.

## Task 1: Extract Reflection Invocation Shell

**Files:**
- Create: `src/seektalent/runtime/reflection_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current reflection shell as a single bounded stage**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth.

Move together:

- `reflection_context.json` writing
- reflection prompt rendering
- prompt cache key/retention selection
- reflection started/failed/completed LLM events
- reflection progress emission
- `reflection_call.json` success/failure snapshot construction
- `repair_reflection_call.json` wiring
- `reflection_advice.json` writing
- `round_review.md` writing

Keep behavior unchanged:

- `ReflectionAdvice` semantics
- `RunStageError("reflection", ...)`
- reflection prompt contents
- artifact names and payload schemas
- round-loop sequencing

- [ ] **Step 2: Create `reflection_runtime.py` with a plain async stage entrypoint**

Create:

- `src/seektalent/runtime/reflection_runtime.py`

Add a plain async function shaped roughly like:

```python
async def run_reflection_stage(
    *,
    settings: AppSettings,
    reflection_critic,
    reflection_context: ReflectionContext,
    round_no: int,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot,
    write_aux_llm_call_artifact,
    emit_llm_event,
    emit_progress,
    render_round_review,
    ...
) -> ReflectionAdvice:
    ...
```

Keep shell-only helpers in the same module.

Do not add a class.

- [ ] **Step 3: Update `_run_rounds(...)` to use a thin reflection-stage delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the inline reflection shell with a call into `reflection_runtime.run_reflection_stage(...)`.

Keep these in `WorkflowRuntime`:

- deciding when reflection runs
- `RoundState` ownership
- helper injection such as `_build_llm_call_snapshot(...)`, `_write_aux_llm_call_artifact(...)`, `_emit_llm_event(...)`, `_emit_progress(...)`
- the broader `_run_rounds(...)` control-flow skeleton

Do not broaden this into a generic stage abstraction.

- [ ] **Step 4: Run reflection-focused tests before touching finalize**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_llm_input_prompts.py -q
```

Expected: PASS.

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small host-boundary test that checks the reflection stage is delegated and still produces the expected observable artifacts.

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit reflection task**

```bash
git add src/seektalent/runtime/reflection_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_llm_input_prompts.py
git commit -m "refactor: extract reflection runtime"
```

## Task 2: Extract Finalizer Invocation Shell

**Files:**
- Create: `src/seektalent/runtime/finalize_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current finalizer shell as a bounded stage**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth.

Move together:

- `finalizer_context.json` writing
- finalizer prompt rendering
- `finalizer_started` / `finalizer_failed` / `finalizer_completed`
- finalizer progress emission
- `finalizer_call.json` success/failure snapshot construction
- `final_candidates.json` writing
- `final_answer.md` writing
- `RunStageError("finalization", ...)`

Do not move in this task:

- evaluation
- `judge_packet.json`
- `search_diagnostics.json`
- `run_summary.md`
- final `RunArtifacts` assembly

- [ ] **Step 2: Create `finalize_runtime.py` with a plain async stage entrypoint**

Create:

- `src/seektalent/runtime/finalize_runtime.py`

Add a plain async function shaped roughly like:

```python
async def run_finalize_stage(
    *,
    settings: AppSettings,
    finalizer,
    finalize_context: FinalizeContext,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot,
    emit_llm_event,
    emit_progress,
    render_final_markdown,
    run_stage_error,
    ...
) -> tuple[FinalResult, str]:
    ...
```

Keep this module free of classes.

- [ ] **Step 3: Update `run_async(...)` to use a thin finalizer-stage delegate**

In `src/seektalent/runtime/orchestrator.py`, replace the inline finalizer shell with a call into `finalize_runtime.run_finalize_stage(...)`.

Keep these in `WorkflowRuntime`:

- post-finalizer evaluation
- `judge_packet.json`
- `search_diagnostics.json`
- `run_summary.md`
- final `RunArtifacts` assembly
- helper injection such as `_build_llm_call_snapshot(...)`, `_emit_llm_event(...)`, `_emit_progress(...)`

Do not broaden this into a generic finalize/reporting framework.

- [ ] **Step 4: Run finalizer-focused regression before claiming success**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_runtime_state_flow.py -q
```

Expected: PASS.

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small host-boundary test that checks finalizer shell delegation while asserting observable artifact behavior.

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit finalize task**

```bash
git add src/seektalent/runtime/finalize_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_runtime_state_flow.py
git commit -m "refactor: extract finalize runtime"
```

## Task 3: Final Focused Regression

**Files:**
- Modify: only if a stale import/helper reference remains

- [ ] **Step 1: Run the combined focused regression set**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_llm_input_prompts.py tests/test_controller_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix only stale import/helper drift if needed**

If a failure appears:

- inspect whether it is a stale import
- inspect whether a helper reference changed host modules
- fix the smallest thing necessary

Do not expand scope into evaluation, cursor-generalization, or flywheel/data work.

- [ ] **Step 3: Re-run the same regression command**

Re-run the same command and confirm it passes before claiming completion.

## Notes For Reviewers

Reviewers should check:

- `reflection_runtime.py` owns the reflection invocation shell
- `finalize_runtime.py` owns the finalizer invocation shell
- no generic stage framework was introduced
- reflection/finalizer artifact names and payloads remain unchanged
- evaluation and run reporting did not get accidentally folded into `finalize_runtime`
- each task landed in a separate commit

## Done Criteria

This plan is complete when:

- `reflection_runtime.py` exists and hosts the reflection shell
- `finalize_runtime.py` exists and hosts the finalizer shell
- `WorkflowRuntime` is visibly thinner in both `_run_rounds(...)` and `run_async(...)`
- reflection and finalizer behavior, artifact schemas, and event/progress semantics remain unchanged
- the reflection task and finalize task land as separate commits
- the final focused regression passes
