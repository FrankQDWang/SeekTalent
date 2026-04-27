# Controller Runtime Split Design

## Goal

Split the controller invocation shell out of `_run_rounds(...)` so `WorkflowRuntime` owns less stage-specific LLM call handling while preserving controller behavior, controller artifacts, repair handling, and progress/event semantics.

This is a narrow structural refactor:

- move controller prompt/render and invocation shell
- move controller failed/completed artifact wiring
- move controller repair-call artifact wiring
- keep decision resolution unchanged
- keep retrieval planning unchanged

## Why This Next

After extracting requirements bootstrap, scoring, diagnostics/reporting, rescue execution, company-discovery execution, and round-decision resolution, the next thick concentration in `_run_rounds(...)` is the controller call shell itself.

This is the best next target because it is:

- now bounded cleanly by `build_controller_context(...)` on the input side
- already separated from decision resolution by `round_decision_runtime`
- lower-risk than restructuring the entire round loop
- the natural next step if the goal is a visibly coordinator-like `_run_rounds(...)`

## Current State

`src/seektalent/runtime/orchestrator.py` currently owns a controller stage shell inside `_run_rounds(...)` that does all of the following:

- render the controller prompt from `ControllerContext`
- compute prompt cache metadata
- call `self.controller.decide(...)`
- write `controller_call.json`
- write `repair_controller_call.json`
- emit `controller_failed` / `controller_completed` LLM events
- emit `controller_failed` / `controller_completed` progress events
- raise `RunStageError("controller", ...)` on failure

This shell currently surrounds the controller invocation before control is handed to `round_decision_runtime`.

## Problem

This logic is not round coordination. It is a single stage-invocation shell that still lives inline inside the main round loop.

That keeps `_run_rounds(...)` mixing:

- controller call mechanics
- controller artifact/repair plumbing
- controller progress/event reporting
- round decision resolution
- retrieval/scoring/reflection/finalization orchestration

The round loop is thinner than before, but this remaining stage shell is still an obvious concentration point.

## Recommended Approach

Create a new module:

- `src/seektalent/runtime/controller_runtime.py`

Use plain module-level functions, not a class.

This stage shell does not need long-lived mutable state. It needs explicit inputs and a clear output, and should stay as flat functions.

## Module Boundary

### New module: `controller_runtime.py`

Own:

- `run_controller_stage(...)`

This function should own:

- prompt rendering
- prompt cache key/retention selection
- controller invocation
- success/failure `controller_call.json` snapshot construction
- repair artifact wiring
- controller LLM event emission
- controller progress emission

### Keep in `WorkflowRuntime`

Keep these in `orchestrator.py`:

- `build_controller_context(...)` call site
- `round_decision_runtime.resolve_round_decision(...)`
- retrieval planning and subsequent stages
- `_build_llm_call_snapshot(...)`
- `_write_aux_llm_call_artifact(...)`
- `_emit_llm_event(...)`
- `_emit_progress(...)`
- the broader `_run_rounds(...)` control-flow skeleton

The helper functions above may still be injected into the controller runtime function rather than moved.

## Function Boundary

Preferred shape:

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

Internal helpers should remain in the same module and be called directly.

Key rules:

- do not pass the whole `WorkflowRuntime`
- keep `ControllerDecision` behavior unchanged
- preserve controller artifact names and payload schemas
- preserve controller failure semantics and `RunStageError("controller", ...)`

## What This Step Does Not Do

This step does not:

- move `build_controller_context(...)`
- change `round_decision_runtime.py`
- change rescue routing or rescue execution
- change retrieval planning
- change reflection or finalizer stages
- change `_build_llm_call_snapshot(...)`
- change `_write_aux_llm_call_artifact(...)`
- change `_emit_llm_event(...)` or `_emit_progress(...)`
- introduce a generic stage framework

## Testing Strategy

Do not add `direct == wrapper` seam tests.

Primary protection should come from existing state-flow, audit, and controller-contract tests, because this is a behavior-preserving extraction of stage plumbing.

If an extra test is needed, prefer:

- a boundary test that `WorkflowRuntime` delegates controller invocation to the new host
- or a direct-output test that checks observable controller call artifacts in a concrete success/failure scenario

Avoid tautological wrapper-parity tests.

## Success Criteria

This step is successful if:

- `_run_rounds(...)` is visibly thinner before decision resolution
- `controller_runtime.py` becomes the single host for controller invocation shell logic
- controller behavior, controller artifacts, repair handling, and event/progress semantics remain unchanged
- existing runtime/state-flow/audit/controller-contract coverage remains green
