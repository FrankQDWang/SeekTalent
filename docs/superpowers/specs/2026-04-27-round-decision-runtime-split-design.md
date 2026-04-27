# Round Decision Runtime Split Design

## Goal

Split the controller-output resolution logic out of `_run_rounds(...)` so `WorkflowRuntime` owns less round-specific branching while preserving rescue behavior, stop behavior, and force-continue semantics.

This is a narrow structural refactor:

- move controller-decision sanitization
- move controller-output rescue resolution
- move force-continue fallback logic
- keep controller invocation unchanged
- keep retrieval planning unchanged

## Why This Next

After extracting requirements bootstrap, scoring, diagnostics/reporting, company-discovery rescue execution, and non-company rescue execution, the biggest remaining concentration in `WorkflowRuntime` is inside `_run_rounds(...)`.

The next best target is the controller-output resolution segment because it is:

- still thick but now bounded by clearer seams
- downstream of controller invocation and upstream of retrieval planning
- lower-risk than restructuring the entire round loop
- the natural next step after extracting rescue execution

## Current State

`src/seektalent/runtime/orchestrator.py` currently owns a large controller-output resolution segment inside `_run_rounds(...)` that does all of the following:

- sanitize raw controller decisions
- derive reflection-backed inactive terms
- sanitize premature max-round claims
- decide whether rescue routing should run
- dispatch rescue lanes and force decision overrides
- apply `force_continue` when stop is not allowed
- decide whether rescue artifacts should be written

The current helper functions directly tied to this segment are:

- `_sanitize_controller_decision(...)`
- `_reflection_backed_inactive_terms(...)`
- `_sanitize_premature_max_round_claim(...)`
- `_force_continue_decision(...)`

The segment also coordinates:

- `_choose_rescue_decision(...)`
- `rescue_execution_runtime`
- `company_discovery_runtime`
- `_write_rescue_decision(...)`

## Problem

This logic is not controller invocation and it is not retrieval planning. It is the decision-resolution layer between them, but it still lives inline inside the round loop.

That keeps `_run_rounds(...)` harder to scan than it needs to be. The round loop still mixes:

- controller execution and its artifacts
- decision-resolution branching
- retrieval planning and search
- scoring, reflection, and finalization handoff

## Recommended Approach

Create a new module:

- `src/seektalent/runtime/round_decision_runtime.py`

Use plain module-level functions, not a class.

This layer does not need long-lived mutable state. It only needs explicit inputs and a clear output.

## Module Boundary

### New module: `round_decision_runtime.py`

Own:

- `resolve_round_decision(...)`
- `sanitize_controller_decision(...)`
- `reflection_backed_inactive_terms(...)`
- `sanitize_premature_max_round_claim(...)`
- `force_continue_decision(...)`

This module should own the branching that turns the raw controller output into the final round decision that the orchestrator will honor.

### Keep in `WorkflowRuntime`

Keep these in `orchestrator.py`:

- controller invocation, retry handling, and repair handling
- controller call artifacts and LLM event/progress emission
- `_choose_rescue_decision(...)`
- `_write_rescue_decision(...)`
- retrieval planning and subsequent stages
- `_emit_progress(...)`
- `_emit_llm_event(...)`
- `_write_aux_llm_call_artifact(...)`

These belong either to controller-stage shell behavior or to broader runtime plumbing.

## Function Boundary

Preferred shape:

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

Internal helpers should remain in the same module and be called directly.

Key rules:

- do not pass the whole `WorkflowRuntime`
- keep current `ControllerDecision` and `RescueDecision` semantics
- preserve rescue artifact write behavior
- preserve the current stop-not-allowed fallback behavior

## What This Step Does Not Do

This step does not:

- move controller invocation itself
- move controller call / repair artifacts
- move `_emit_llm_event(...)`
- move `_emit_progress(...)`
- move retrieval planning
- move `_build_round_query_states(...)`
- change `rescue_router.py`
- change `rescue_execution_runtime.py`
- change `company_discovery_runtime.py`
- restructure `_run_rounds(...)` as a whole
- introduce a generic decision engine or stage framework

## Testing Strategy

Do not add `direct == wrapper` seam tests.

Primary protection should come from existing round/state-flow and rescue-oriented tests, because this step is still behavior-preserving.

If an extra test is needed, prefer:

- a boundary test that `WorkflowRuntime` delegates decision resolution to the new host
- or a direct-output test that checks observable resolved decisions for a concrete rescue/stop scenario

Avoid tautological wrapper-parity tests.

## Success Criteria

This step is successful if:

- `_run_rounds(...)` is visibly thinner after controller invocation
- `round_decision_runtime.py` becomes the single host for controller-output resolution helpers
- rescue behavior, stop behavior, and force-continue behavior remain unchanged
- existing runtime/state-flow/audit coverage remains green
