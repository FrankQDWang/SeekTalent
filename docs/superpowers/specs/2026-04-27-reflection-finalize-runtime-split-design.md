# Reflection and Finalize Runtime Split Design

## Goal

Split the remaining LLM stage shells for reflection and finalization out of `WorkflowRuntime` while preserving behavior, artifact schemas, event/progress semantics, and stage ordering.

This phase intentionally covers two related tasks:

1. `reflection_runtime`
2. `finalize_runtime`

The design is shared because both stages have the same local problem shape:

- stage-specific prompt/render and invocation shell logic still lives inline
- artifact writing and progress/event plumbing is mixed into orchestration
- the main runtime owns more stage detail than it should

Implementation should still happen as two separate tasks with two separate commits.

## Why This Next

After extracting requirements bootstrap, controller shell, round-decision resolution, retrieval execution, scoring execution, company-discovery execution, and rescue execution, the biggest remaining stage-shaped concentrations are:

- the reflection shell inside `_run_rounds(...)`
- the finalizer shell inside `run_async(...)`

These are the most natural next targets because they are already bounded by clear inputs and outputs:

- reflection is bounded by `ReflectionContext` in and `ReflectionAdvice` out
- finalization is bounded by finalized shortlist inputs in and `FinalResult` out

This is lower-risk than reworking the full round loop or starting flywheel/data work now.

## Current State

### Reflection

`src/seektalent/runtime/orchestrator.py` currently owns a reflection shell inside `_run_rounds(...)` that does all of the following:

- build and write `reflection_context.json`
- render the reflection prompt
- compute prompt cache metadata
- emit `reflection_started`
- call `self._reflect_round(...)`
- write `reflection_call.json`
- write `repair_reflection_call.json`
- emit `reflection_failed` / `reflection_completed`
- emit reflection progress events
- write `reflection_advice.json`
- write `round_review.md`
- assign `round_state.reflection_advice`

### Finalize

`src/seektalent/runtime/orchestrator.py` currently owns a finalizer shell inside `run_async(...)` that does all of the following:

- build and write `finalizer_context.json`
- render the finalizer prompt
- emit `finalizer_started`
- call `self.finalizer.finalize(...)`
- write `finalizer_call.json`
- write `final_candidates.json`
- write `final_answer.md`
- emit `finalizer_failed` / `finalizer_completed`
- emit finalizer progress events
- raise `RunStageError("finalization", ...)` on failure

Post-finalizer run reporting and evaluation remain nearby, but are a separate concern from the invocation shell itself.

## Problem

These are not coordinator responsibilities. They are stage-specific LLM call shells that still sit inline in the main runtime flow.

That keeps `WorkflowRuntime` mixing:

- stage orchestration
- prompt/render mechanics
- stage artifact plumbing
- stage progress/event wiring
- stage success/failure shell handling

The runtime is much thinner than before, but these two shells are now the obvious remaining bounded concentrations.

## Recommended Approach

Create two new modules:

- `src/seektalent/runtime/reflection_runtime.py`
- `src/seektalent/runtime/finalize_runtime.py`

Use plain module-level functions, not classes.

These stage shells do not need long-lived mutable state. They need explicit inputs, explicit outputs, and clear artifact/event boundaries.

Do not introduce:

- `BaseStageRuntime`
- generic LLM stage shell frameworks
- stage registries
- shared abstract runtime layers

This repository should keep the result flat and literal.

## Phase Structure

This phase has one spec but two implementation tasks.

### Task 1: Reflection Runtime

Extract the reflection invocation shell first.

Reason:

- it is fully inside `_run_rounds(...)`
- it is the thickest remaining round-stage shell
- it reduces main-loop concentration before touching finalization

### Task 2: Finalize Runtime

After reflection is done and verified, extract the finalizer invocation shell.

Reason:

- it is a separate stage with different artifacts and output shape
- it lives in `run_async(...)`, not `_run_rounds(...)`
- doing it second avoids coupling both moves in one commit

## Module Boundary

### New module: `reflection_runtime.py`

Own:

- reflection context artifact writing
- reflection prompt/render shell
- prompt cache key/retention selection for reflection
- reflection invocation shell
- `reflection_call.json` success/failure snapshots
- `repair_reflection_call.json` wiring
- reflection LLM event emission
- reflection progress emission
- `reflection_advice.json` writing
- `round_review.md` writing

Output:

- `ReflectionAdvice | None` according to current behavior

Keep in `WorkflowRuntime`:

- deciding when reflection runs
- round-loop control flow
- creation of the `RoundState`
- helper injection such as `_build_llm_call_snapshot(...)`, `_write_aux_llm_call_artifact(...)`, `_emit_llm_event(...)`, `_emit_progress(...)`
- `_reflect_round(...)` may remain as the domain-level call if that keeps the shell small, but the shell around it must move

### New module: `finalize_runtime.py`

Own:

- finalizer context artifact writing
- finalizer prompt/render shell
- finalizer invocation shell
- `finalizer_call.json` success/failure snapshots
- `final_candidates.json` writing
- `final_answer.md` writing
- finalizer LLM event emission
- finalizer progress emission
- `RunStageError("finalization", ...)` translation on finalizer failure

Output:

- `FinalResult`
- rendered final markdown if that keeps the boundary explicit

Keep in `WorkflowRuntime`:

- the overall `run_async(...)` flow
- post-finalizer evaluation
- `judge_packet.json`
- `search_diagnostics.json`
- `run_summary.md`
- final `RunArtifacts` assembly
- helper injection such as `_build_llm_call_snapshot(...)`, `_emit_llm_event(...)`, `_emit_progress(...)`

This step should not fold run reporting and evaluation into the finalizer host. Only the finalizer invocation shell should move.

## Function Boundary

Preferred shape for reflection:

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
    next_step: str,
    ...
) -> ReflectionAdvice:
    ...
```

Preferred shape for finalize:

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

Key rules:

- do not pass the whole `WorkflowRuntime`
- do not invent large result wrapper models unless truly needed
- keep artifact names and payload schemas unchanged
- keep success/failure semantics unchanged
- keep reflection and finalization as separate modules and separate commits

## What This Phase Does Not Do

This phase does not:

- introduce a shared stage framework
- merge reflection and finalization into one host
- change `_run_rounds(...)` control flow semantics
- change `run_async(...)` control flow semantics
- change retrieval/scoring/controller/runtime boundaries
- change evaluation behavior
- change diagnostics/reporting schemas
- change prompt contents
- change reflection/finalizer model contracts
- start cursor-generalization
- start flywheel/data-asset work

## Testing Strategy

Do not add `direct == wrapper` parity tests.

Testing should follow the task split:

### After Task 1

Run focused tests for reflection/runtime behavior, especially:

- runtime state-flow coverage
- runtime audit coverage
- prompt/input coverage that touches reflection artifacts

### After Task 2

Run focused tests for finalizer/runtime behavior, especially:

- runtime audit coverage for `finalizer_call.json`, `final_candidates.json`, `final_answer.md`, `run_summary.md`
- API / CLI coverage that consumes final outputs

If extra seam tests are added, they should assert observable behavior at the host boundary, not tautological wrapper equivalence.

## Success Criteria

This phase is successful if:

- `reflection_runtime.py` becomes the single host for reflection invocation shell logic
- `finalize_runtime.py` becomes the single host for finalizer invocation shell logic
- `WorkflowRuntime` gets visibly thinner without changing behavior
- reflection and finalizer artifacts, event/progress semantics, and failure behavior remain unchanged
- the two tasks land as separate commits
- no generic stage abstraction is introduced
